"""Streaming metrics for completed f32le stereo PCM captures at 48 kHz."""
import argparse
import heapq
import io
import json
import math
from pathlib import Path
import struct
import numpy as np

SAMPLE_RATE = 48000
FRAME_BYTES = 8
WINDOW_BYTES = SAMPLE_RATE * FRAME_BYTES
RAIL_THRESHOLD = 0.999
INTERVAL_CAP = 100


def empty_channel():
    return {key: 0 for key in ('finite_samples', 'nan_samples', 'inf_samples',
        'exact_full_scale_samples', 'near_full_scale_samples', 'exact_zero_samples', 'sum', 'sum_square')}


def channel_stats(values):
    finite = np.isfinite(values)
    numbers = values[finite].astype(np.float64)
    magnitude = np.abs(values)
    return {'finite_samples': int(np.count_nonzero(finite)),
            'nan_samples': int(np.count_nonzero(np.isnan(values))),
            'inf_samples': int(np.count_nonzero(np.isinf(values))),
            'exact_full_scale_samples': int(np.count_nonzero(finite & (magnitude == 1))),
            'near_full_scale_samples': int(np.count_nonzero(finite & (magnitude >= RAIL_THRESHOLD))),
            'exact_zero_samples': int(np.count_nonzero(finite & (values == 0))),
            'sum': float(np.sum(numbers, dtype=np.float64)),
            'sum_square': float(np.sum(numbers * numbers, dtype=np.float64))}


def report_channel(stats, frames, index):
    finite = stats['finite_samples']
    return {'channel': index, 'name': ('left', 'right')[index],
            'finite_samples': finite, 'nan_samples': stats['nan_samples'], 'inf_samples': stats['inf_samples'],
            'mean': stats['sum'] / finite if finite else None,
            'rms': math.sqrt(stats['sum_square'] / finite) if finite else None,
            'exact_full_scale_samples': stats['exact_full_scale_samples'],
            'near_full_scale_samples': stats['near_full_scale_samples'],
            'exact_zero_samples': stats['exact_zero_samples'],
            'exact_full_scale_fraction': stats['exact_full_scale_samples'] / frames if frames else None,
            'near_full_scale_fraction': stats['near_full_scale_samples'] / frames if frames else None,
            'exact_zero_fraction': stats['exact_zero_samples'] / frames if frames else None}


class RailIntervals:
    def __init__(self):
        self.open_start = None
        self.total_intervals = 0
        self.total_frames = 0
        self.longest = []

    def close(self, end):
        if self.open_start is None:
            return
        start = self.open_start
        self.open_start = None
        self.total_intervals += 1
        item = (end - start, -start, start, end)
        if len(self.longest) < INTERVAL_CAP:
            heapq.heappush(self.longest, item)
        elif item > self.longest[0]:
            heapq.heapreplace(self.longest, item)

    def add(self, mask, start):
        self.total_frames += int(np.count_nonzero(mask))
        if bool(mask[0]):
            if self.open_start is None:
                self.open_start = start
        else:
            self.close(start)
        # Keep a run open across one-second/chunk boundaries.
        for offset in np.flatnonzero(mask[1:] != mask[:-1]) + 1:
            position = start + int(offset)
            if bool(mask[offset]):
                self.open_start = position
            else:
                self.close(position)

    def report(self, frames):
        self.close(frames)
        intervals = [{'start_frame': start, 'end_frame_exclusive': end, 'frames': count,
                      'start_seconds': start / SAMPLE_RATE, 'end_seconds': end / SAMPLE_RATE,
                      'duration_seconds': count / SAMPLE_RATE}
                     for count, _, start, end in sorted(self.longest, key=lambda item: (-item[0], item[2]))]
        return {'total_intervals': self.total_intervals, 'total_frames': self.total_frames,
                'fraction_of_frames': self.total_frames / frames if frames else None,
                'retained_interval_cap': INTERVAL_CAP, 'longest': intervals}


def analyze_stream(stream):
    frames = bytes_read = 0
    total = [empty_channel(), empty_channel()]
    any_rail, both_rails = RailIntervals(), RailIntervals()
    windows = []

    def consume(raw):
        nonlocal frames
        values = np.frombuffer(raw, dtype='<f4').reshape(-1, 2)
        count = len(values)
        stats = [channel_stats(values[:, index]) for index in (0, 1)]
        near = np.isfinite(values) & (np.abs(values) >= RAIL_THRESHOLD)
        any_mask, both_mask = np.any(near, axis=1), np.all(near, axis=1)
        any_rail.add(any_mask, frames)
        both_rails.add(both_mask, frames)
        windows.append({'start_frame': frames, 'start_seconds': frames / SAMPLE_RATE,
                        'frames': count, 'duration_seconds': count / SAMPLE_RATE,
                        'channels': [report_channel(stats[index], count, index) for index in (0, 1)],
                        'any_channel_near_full_scale_fraction': int(np.count_nonzero(any_mask)) / count,
                        'both_channels_near_full_scale_fraction': int(np.count_nonzero(both_mask)) / count,
                        'exact_zero_fraction': sum(s['exact_zero_samples'] for s in stats) / (count * 2)})
        for index in (0, 1):
            for key, value in stats[index].items():
                total[index][key] += value
        frames += count

    pending = b''
    while True:
        block = stream.read(WINDOW_BYTES - len(pending))
        if not block:
            break
        bytes_read += len(block)
        pending += block
        if len(pending) == WINDOW_BYTES:
            consume(pending)
            pending = b''
    complete_bytes = len(pending) // FRAME_BYTES * FRAME_BYTES
    if complete_bytes:
        consume(pending[:complete_bytes])
    return {'format': 'raw f32le interleaved stereo', 'sample_rate': SAMPLE_RATE, 'channel_count': 2,
            'bytes_read': bytes_read, 'frames': frames, 'duration_seconds': frames / SAMPLE_RATE,
            'trailing_bytes_ignored': len(pending) - complete_bytes,
            'trailing_byte_policy': 'Only complete 8-byte stereo frames are analyzed; final 0..7 bytes are ignored.',
            'finite_samples': sum(s['finite_samples'] for s in total),
            'nan_samples': sum(s['nan_samples'] for s in total), 'inf_samples': sum(s['inf_samples'] for s in total),
            'mean_rms_denominator': 'Finite samples in each channel.',
            'fraction_denominator': 'All complete frames per channel; nonfinite samples do not count as rail or zero.',
            'channels': [report_channel(total[index], frames, index) for index in (0, 1)],
            'rail_threshold': RAIL_THRESHOLD,
            'rail_intervals': {'any_channel': any_rail.report(frames), 'both_channels': both_rails.report(frames)},
            'one_second_windows': windows,
            'scope': 'Mixed samples from both ports before WASAPI submission; does not attribute a port or capture device output.'}


def self_test():
    def analyze(frames, trailing=b''):
        raw = b''.join(struct.pack('<ff', left, right) for left, right in frames) + trailing
        return analyze_stream(io.BytesIO(raw))

    silence = analyze([(0, 0), (0, -0.0)])
    assert silence['frames'] == 2, 'silence: two complete stereo frames must be counted'
    assert silence['finite_samples'] == 4 and silence['nan_samples'] == silence['inf_samples'] == 0
    assert all(channel['rms'] == 0 and channel['mean'] == 0 and
               channel['exact_zero_fraction'] == 1 for channel in silence['channels'])
    assert silence['rail_intervals']['any_channel']['total_intervals'] == 0

    rails = analyze([(1, -1), (-1, 1), (1, -1), (-1, 1)])
    assert all(channel['rms'] == 1 and channel['mean'] == 0 and
               channel['exact_full_scale_fraction'] == channel['near_full_scale_fraction'] == 1
               for channel in rails['channels'])
    assert rails['rail_intervals']['both_channels']['longest'][0]['frames'] == 4

    alternating = analyze([(1, 0), (0, 1), (-1, 0), (0, -1)])
    assert all(math.isclose(channel['rms'], math.sqrt(0.5)) and channel['mean'] == 0 and
               channel['exact_full_scale_fraction'] == channel['exact_zero_fraction'] == 0.5
               for channel in alternating['channels'])
    assert alternating['rail_intervals']['any_channel']['longest'][0]['frames'] == 4
    assert alternating['rail_intervals']['both_channels']['total_intervals'] == 0

    invalid = analyze([(float('nan'), float('inf')), (-float('inf'), 1), (0, -1)])
    assert (invalid['finite_samples'], invalid['nan_samples'], invalid['inf_samples']) == (3, 1, 2)
    assert invalid['channels'][0]['rms'] == 0 and invalid['channels'][1]['rms'] == 1
    assert invalid['rail_intervals']['any_channel']['longest'][0]['start_frame'] == 1
    assert invalid['rail_intervals']['any_channel']['longest'][0]['frames'] == 2

    near = analyze([(0.999, -0.999), (0.998, 0), (1, 1)], b'xyz')
    assert near['frames'] == 3 and near['trailing_bytes_ignored'] == 3
    assert all(channel['exact_full_scale_fraction'] == 1 / 3 and
               channel['near_full_scale_fraction'] == 2 / 3 for channel in near['channels'])
    assert near['rail_intervals']['both_channels']['total_intervals'] == 2

    boundary = analyze([(1, -1)] * 48001)
    assert [window['frames'] for window in boundary['one_second_windows']] == [48000, 1]
    assert boundary['rail_intervals']['both_channels']['longest'][0]['frames'] == 48001
    assert all(window['channels'][0]['exact_full_scale_fraction'] == 1 and
               window['channels'][0]['exact_zero_fraction'] == 0 for window in boundary['one_second_windows'])

    many = []
    for length in range(1, 111):
        many.extend([(1, -1)] * length + [(0, 0)])
    capped = analyze(many)['rail_intervals']['both_channels']
    assert capped['total_intervals'] == 110 and len(capped['longest']) == 100
    assert (capped['longest'][0]['frames'], capped['longest'][-1]['frames']) == (110, 11)

    class ShortReads(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 5))
    short = analyze_stream(ShortReads(struct.pack('<ff', 1, -1) * 4 + b'xyz'))
    assert short['frames'] == 4 and short['trailing_bytes_ignored'] == 3
    assert short['rail_intervals']['both_channels']['longest'][0]['frames'] == 4
    assert all(channel['rms'] == 1 and channel['exact_full_scale_fraction'] == 1
               for channel in short['channels'])
    all_invalid = analyze([(float('nan'), float('inf'))])
    assert all(channel['mean'] is None and channel['rms'] is None and
               channel['near_full_scale_fraction'] == 0 for channel in all_invalid['channels'])
    json.dumps(all_invalid, allow_nan=False)
    print(json.dumps({'self_test': 'PASS', 'synthetic_cases': 9,
                      'scope': 'synthetic PCM only; no game capture opened'}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path, nargs='?', help='Completed raw mix.f32 capture')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        try:
            self_test()
        except AssertionError as error:
            print(json.dumps({'self_test': 'FAIL', 'reason': str(error)}))
            raise SystemExit(1)
        return
    if args.capture is None:
        parser.error('Provide a completed capture or --self-test')
    with args.capture.open('rb') as stream:
        result = analyze_stream(stream)
    result['path'] = str(args.capture.resolve())
    output = args.capture.with_suffix('.full-scale.metrics.json')
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'frames': result['frames'], 'duration_seconds': result['duration_seconds'],
                      'finite_samples': result['finite_samples'], 'nan_samples': result['nan_samples'],
                      'inf_samples': result['inf_samples'], 'channels': result['channels'],
                      'trailing_bytes_ignored': result['trailing_bytes_ignored'], 'out': str(output.resolve())}))


if __name__ == '__main__':
    main()

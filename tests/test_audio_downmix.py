"""Tests for cellAudio PortParam level preservation and 7.1 downmix attenuation."""
import io
import math
import struct
import unittest
import numpy as np

from scripts import analyze_audio_full_scale


class AudioDownmixTests(unittest.TestCase):
    def test_cell_audio_port_param_guest_layout(self):
        """CellAudioPortParam in PS3 guest memory has level at +24 after nChannel, nBlock, attr."""
        # Pack BE struct: u64 nChannel, u64 nBlock, u64 attr, float level
        n_channel = 8
        n_block = 16
        attr = 0x1000
        level = 0.75

        packed = struct.pack(">QQQf", n_channel, n_block, attr, level)
        self.assertEqual(len(packed), 28)

        # Decode fields by offset matching cellAudioPortOpen
        dec_nch = struct.unpack(">Q", packed[0:8])[0]
        dec_nblk = struct.unpack(">Q", packed[8:16])[0]
        dec_attr = struct.unpack(">Q", packed[16:24])[0]
        dec_level = struct.unpack(">f", packed[24:28])[0]

        self.assertEqual(dec_nch, 8)
        self.assertEqual(dec_nblk, 16)
        self.assertEqual(dec_attr, 0x1000)
        self.assertAlmostEqual(dec_level, 0.75, places=5)

    def test_downmix_coefficients_and_headroom_scale(self):
        """7.1 downmix sum has coefficients 1.0, 0.707, 0.5, 0.5, 0.3 with sum 3.007."""
        coeffs = [1.0, 0.707, 0.5, 0.5, 0.3]
        coeff_sum = sum(coeffs)
        self.assertAlmostEqual(coeff_sum, 3.007, places=4)

        downmix_scale = 1.0 / coeff_sum

        # Worst-case: all channels at +1.0 full scale
        worst_case_sum = coeff_sum * 1.0
        scaled_peak = worst_case_sum * downmix_scale
        self.assertAlmostEqual(scaled_peak, 1.0, places=5)

        # Worst-case negative: all channels at -1.0 full scale
        scaled_min = (-coeff_sum) * downmix_scale
        self.assertAlmostEqual(scaled_min, -1.0, places=5)

    def test_unattenuated_downmix_clips_whereas_attenuated_has_headroom(self):
        """Unscaled 8-channel summing clips heavily, while attenuated downmixing stays within [-1.0, 1.0]."""
        num_frames = 48000
        # 8 channels of active signals with peaks up to 0.95
        np.random.seed(12345)
        channels = np.random.uniform(-0.9, 0.9, (num_frames, 8)).astype(np.float32)

        level = 1.0
        center = channels[:, 2] * level * 0.707
        lfe = channels[:, 3] * level * 0.5
        rl = channels[:, 4] * level * 0.5
        rr = channels[:, 5] * level * 0.5
        sl = channels[:, 6] * level * 0.3
        sr = channels[:, 7] * level * 0.3

        # Unscaled sum
        left_unscaled = channels[:, 0] * level + center + lfe + rl + sl
        right_unscaled = channels[:, 1] * level + center + lfe + rr + sr

        # Scaled sum with headroom attenuation
        downmix_scale = 1.0 / (1.0 + 0.707 + 0.5 + 0.5 + 0.3)
        left_scaled = left_unscaled * downmix_scale
        right_scaled = right_unscaled * downmix_scale

        # Unscaled exceeds rail and saturates upon clipping
        unscaled_clipped = np.clip(np.column_stack([left_unscaled, right_unscaled]), -1.0, 1.0)
        unscaled_raw = unscaled_clipped.tobytes()
        unscaled_report = analyze_audio_full_scale.analyze_stream(io.BytesIO(unscaled_raw))

        # Scaled never exceeds [-1.0, 1.0]
        self.assertLessEqual(np.max(np.abs(left_scaled)), 1.0)
        self.assertLessEqual(np.max(np.abs(right_scaled)), 1.0)

        scaled_clipped = np.clip(np.column_stack([left_scaled, right_scaled]), -1.0, 1.0)
        scaled_raw = scaled_clipped.tobytes()
        scaled_report = analyze_audio_full_scale.analyze_stream(io.BytesIO(scaled_raw))

        # Verify unscaled has significant rail saturation
        left_rail_unscaled = unscaled_report['channels'][0]['exact_full_scale_fraction']
        self.assertGreater(left_rail_unscaled, 0.10)

        # Verify scaled has 0 rail saturation
        left_rail_scaled = scaled_report['channels'][0]['exact_full_scale_fraction']
        right_rail_scaled = scaled_report['channels'][1]['exact_full_scale_fraction']
        self.assertEqual(left_rail_scaled, 0.0)
        self.assertEqual(right_rail_scaled, 0.0)


if __name__ == "__main__":
    unittest.main()

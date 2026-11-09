"""Compare BCUS98125's captured Y/V/U planes with a matching GPU readback.

Uses the coefficients observed in FP 0F2D3B41. Concurrent source captures and
chroma filtering can differ from GPU sampling; this is not a video oracle.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

import numpy as np
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("readback", help="RSX capture directory, e.g. frame_25")
    args = parser.parse_args()
    directory = args.capture / "rsx" / args.readback
    manifest = (directory / "manifest.txt").read_text(encoding="utf-8")
    frame = int(re.search(r"frame_count=(\d+)", manifest).group(1))
    if "fp=0x0F2D3B41" not in manifest:
        raise ValueError("Readback does not record the observed Bink fragment program")
    gpu = np.asarray(Image.open(directory / "backbuffer.bmp").convert("RGB"), dtype=np.float32)
    height, width = gpu.shape[:2]
    planes, sources = [], []
    for unit in range(3):
        address, w, h = re.search(
            rf"t{unit} off=0x([0-9A-Fa-f]+).*? (\d+)x(\d+) remap=", manifest).groups()
        matches = list(args.capture.glob(f"texb8_{address.upper()}_{w}x{h}_f{frame}_*.bmp"))
        if len(matches) != 1:
            raise ValueError(f"Expected one matching source plane for t{unit}; found {len(matches)}")
        source = matches[0]
        with Image.open(source) as image:
            plane = image.convert("L").resize((width, height), Image.Resampling.BILINEAR)
            planes.append(np.asarray(plane, dtype=np.float32) / 255)
        sources.append({"unit": unit, "path": str(source.resolve()),
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    y, v, u = planes
    rgb = np.stack((1.16412*y + 1.59579*v - .870655,
                    1.16412*y - .813477*v - .391449*u + .529705,
                    1.16412*y + 2.01782*u - 1.08167), axis=2)
    error = np.abs(np.clip(rgb, 0, 1)*255 - gpu)
    report = {"frame": frame, "sources": sources, "mean_abs_byte_error": float(error.mean()),
              "p95_abs_byte_error": float(np.percentile(error, 95)),
              "max_abs_byte_error": float(error.max()), "concurrent_sources": True,
              "cpu_chroma_filter": "Pillow bilinear", "reference_video_compared": False}
    output = directory / "plane-comparison.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

# Third-party notices and licenses

Provenance inventory reviewed for public release preparation on 9 November 2026. This file does not replace original licenses nor grant rights to the game.

## PS3Recomp — MIT

- Upstream project: [sp00nznet/ps3recomp](https://github.com/sp00nznet/ps3recomp).
- Baseline commit: `5244db92669b54831c6d3ebb7a96d58c4dbfa6df`.
- Original license: [LICENSE at baseline commit](https://github.com/sp00nznet/ps3recomp/blob/5244db92669b54831c6d3ebb7a96d58c4dbfa6df/LICENSE).
- Local copy: [src/port/PS3Recomp-LICENSE](src/port/PS3Recomp-LICENSE).

The context-owned MFC patch is adapted from [White Knight Chronicles II Recomp](https://github.com/WK2D/White-Knight-Chronicles-2-Recomp/blob/fd7749bcb8fbc83d48031a80111b5318b7533861/patches/0061-context-owned-mfc.patch), Copyright (c) 2026 WK2D contributors and Ned Heller / sp00nznet, MIT. Its complete license is preserved in [src/port/WKC2-LICENSE](src/port/WKC2-LICENSE).

Local runtime integration derives from upstream templates and compiles runtime components. Distributed patch files also include context from upstream code. The notice below and license must accompany copies or substantial portions of this software. The separate decoders described below carry a different license.

```text
MIT License

Copyright (c) 2026 Ned Heller / sp00nznet

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## ATRAC3/ATRAC3+ Decoders — LGPL-2.1-or-later

The `CMakeLists.txt` of the pinned revision compiles `third_party/at3_standalone/*.cpp` into the static runtime library. The [upstream README](https://github.com/sp00nznet/ps3recomp/blob/5244db92669b54831c6d3ebb7a96d58c4dbfa6df/third_party/at3_standalone/README.txt) identifies their provenance: decoders from [FFmpeg](https://ffmpeg.org/), extracted by [PPSSPP](https://github.com/hrydgard/ppsspp/tree/ebc151f8f28beca7c07c9ec5d183fc6171c4a1fc/ext/at3_standalone), commit `ebc151f8f28beca7c07c9ec5d183fc6171c4a1fc`, with integration adaptations by PS3Recomp.

Source headers preserve individual copyright notices, including Maxim Poliakovski, Benjamin Larsson, Michael Niedermayer, Loren Merritt, and Fabrice Bellard.

- License: GNU Lesser General Public License, version 2.1 or later (`LGPL-2.1-or-later`), as stated in source headers.
- License text in SDK: [COPYING.LGPLv2.1](https://github.com/sp00nznet/ps3recomp/blob/5244db92669b54831c6d3ebb7a96d58c4dbfa6df/third_party/at3_standalone/COPYING.LGPLv2.1).
- The MIT license of the SDK does NOT apply to these decoders.
- The public release does not distribute compiled binaries or libraries. Before distributing binaries incorporating this component, verify compliance with licensing terms, source disclosure, and relinking requirements given the static linkage.

## erikomis/infamous2-recomp — MIT (static reference; one ordering fix adopted)

- Referenced project: [erikomis/infamous2-recomp](https://github.com/erikomis/infamous2-recomp) by Erik Martins (erikomis).
- License: the MIT text with additional provenance paragraphs; GitHub classifies the repository as a custom license (`NOASSERTION`) because of those paragraphs.
- Status in this repository: consulted as a **static reference**. One small ordering fix was adopted on 20/10/2026 — `spu_lifted_lookup` resolving resident code/task/overlay before the base image (our patch 55, `scripts/patches/spu-overlay-lookup.patch`). It was reimplemented locally and covered by our own RED/GREEN fixture. No file, asset, or other code was copied, and none of its announced results are used as evidence of execution here.
- The MIT notice and attribution above are preserved for that adopted fix.
- If any part is ever incorporated, the MIT notice and attribution must be preserved here and in [CREDITS.md](CREDITS.md).

Many of the fixes it references are **upstream PS3Recomp pull requests** (MIT; covered by the PS3Recomp section above): #266, #268, #278, #293, #297, #302, #303, #304, #305, #307, #311–#314, #326, #340, #349, #358, #360–#363 and #366. Adopting any of them means moving to a later PS3Recomp revision; the local baseline remains `5244db92669b54831c6d3ebb7a96d58c4dbfa6df` (`v0.13.0`).

## External tools and references

Compilers, runtimes, diagnostic tools, and MCP servers are obtained separately under their own respective licenses. Their installers or binaries are not part of this release. See [CREDITS.md](CREDITS.md).

Code and documentation from Infamous-mod-manager, libPSARC, PSL1GHT, RPCS3, erikomis/infamous2-recomp, and other projects are technical references documented in [CREDITS.md](CREDITS.md). A technical reference does not authorize copying implementation. Future adoptions must record origin, revision, license, and notices.

## Scope and limitations

- **Original contributions:** Copyright © 2026 juans-collab and respective contributors, under [GPL-3.0-only](LICENSE). Third-party rights, licenses, and notices remain applicable to their respective portions. This license does not cover proprietary game assets or code.
- **Repository provenance:** This inventory summarizes known third-party code in the prepared public tree. It does not certify unexamined local files.

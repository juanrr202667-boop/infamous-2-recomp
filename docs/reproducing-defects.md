# Reproducing defects with synthetic tests

A useful bug report has a minimal, deterministic reproduction that does not
include game files. Start from an observed host or guest behavior, then reduce
it to the smallest fixture that fails through production code.

## Rules

- Never attach an EBOOT/ELF, ISO, PKG, save, firmware, key, game asset, memory
  dump, or GPU capture.
- Separate the observation from the explanation: "capture 7 has a black surface"
  is an observation; "the depth clear is skipped" is a hypothesis until the
  fixture reproduces it.
- Exercise production sources (`libs/video/rsx_commands.c`,
  `runtime/spu/spu_interp.c`, `cellAudio.c`, and so on), not a copy of them.
- A fixture that only parses a string does not prove rendering or gameplay.
- Keep the test deterministic: no wall-clock sleeps, no GPU device requirement,
  no network, and no dependence on the developer's absolute paths.

## Procedure

1. **Describe the observation.** Include the command, build hash, and the first
   unexpected log line. Do not include a personal path.
2. **Find the boundary.** Is the guest command decoded? Is the host resource
   created? Is the draw issued? Capture the smallest state that still shows the
   difference.
3. **Write a failing fixture.** Use synthetic guest memory and the real
   production function. Assert the behavior you expect, not the current one.
4. **Confirm the failure is caused by the suspected code.** Add a control case
   that should pass before and after the change. A control that fails with and
   without the patch is not a control.
5. **Fix one cause.** Do not combine a renderer change, a shader change, and a
   timing change in one step.
6. **Run the focused test, then the suite.** Record skipped and excluded tests.
7. **Update the baseline only after review.**

## Minimal fixture skeleton

```cpp
#include "ppu_recomp.h"
#include "production_header.h"
#include <cstdio>

static uint8_t memory[0x10000];
extern "C" uint8_t* vm_base = memory;
extern "C" const func_entry function_table[] = {{0, nullptr}};
extern "C" const uint64_t function_table_count = 0;

int main() {
    // Arrange: synthetic guest bytes, no game file.
    // Act: call the production decoder/runtime function.
    // Assert: return 0 only on the expected behavior.
    return 0;
}
```

Register it in `tests/native/CMakeLists.txt`, choose labels, and add it to the
regression baseline.

## When a defect needs the game

Some defects cannot be reduced yet. Mark them **game-local**, keep the evidence
local, and publish only a technical summary and a synthetic part if one exists.
Do not present a local gameplay observation as a public test pass.

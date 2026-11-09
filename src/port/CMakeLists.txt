cmake_minimum_required(VERSION 3.20)
project(Infamous2Native
    VERSION 0.1.0
    LANGUAGES C CXX
    DESCRIPTION "inFAMOUS 2 BCUS98125 native port experiment"
)

# ---------------------------------------------------------------------------
# Standards
# ---------------------------------------------------------------------------
set(CMAKE_C_STANDARD 17)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Prefer Ninja if available
if(NOT CMAKE_GENERATOR MATCHES "Ninja" AND NOT DEFINED ENV{CMAKE_GENERATOR})
    message(STATUS "Tip: use -G Ninja for faster builds")
endif()

# ---------------------------------------------------------------------------
# The two directories this project is built from
# ---------------------------------------------------------------------------
# PS3RECOMP_DIR is the toolkit checkout: include/, runtime/, libs/, tools/.
# RECOMP_DIR is where the lifter's output for THIS game lives. They are
# separate on purpose -- a port keeps its generated code in its own tree and
# points at whatever toolkit checkout it is building against:
#
#   cmake -B build -G Ninja \
#         -DPS3RECOMP_DIR=/path/to/ps3recomp \
#         -DRECOMP_DIR=/path/to/my_game/recompiled
#
get_filename_component(PORT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
if(NOT DEFINED PS3RECOMP_DIR)
    set(PS3RECOMP_DIR "${PORT_ROOT}/tools/ps3recomp")
endif()
get_filename_component(PS3RECOMP_DIR "${PS3RECOMP_DIR}" ABSOLUTE)

if(NOT EXISTS "${PS3RECOMP_DIR}/CMakeLists.txt")
    message(FATAL_ERROR
        "PS3Recomp SDK not found at ${PS3RECOMP_DIR}.\n"
        "Run scripts/setup.ps1, clone the pinned revision there, or pass "
        "-DPS3RECOMP_DIR=<checkout>.")
endif()

# The SDK revision is part of the reproducibility contract. A dirty tree is
# expected after the local patch stack is applied; a different commit is not.
set(PORT_PINNED_PS3RECOMP_REVISION
    "5244db92669b54831c6d3ebb7a96d58c4dbfa6df"
    CACHE STRING "PS3Recomp commit this port's patch stack targets")
option(PORT_STRICT_SDK_REVISION
    "Fail configuration when the SDK HEAD differs from the pinned revision" ON)

if(EXISTS "${PS3RECOMP_DIR}/.git")
    find_package(Git QUIET)
    if(Git_FOUND)
        execute_process(
            COMMAND "${GIT_EXECUTABLE}" -C "${PS3RECOMP_DIR}" rev-parse HEAD
            OUTPUT_VARIABLE PORT_SDK_REVISION
            OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET)
        if(PORT_SDK_REVISION AND
           NOT PORT_SDK_REVISION STREQUAL PORT_PINNED_PS3RECOMP_REVISION)
            set(_sdk_revision_message
                "PS3Recomp is at ${PORT_SDK_REVISION}, expected "
                "${PORT_PINNED_PS3RECOMP_REVISION}.\n"
                "The local patch stack is only validated against the pinned commit.")
            if(PORT_STRICT_SDK_REVISION)
                message(FATAL_ERROR "${_sdk_revision_message}")
            else()
                message(WARNING "${_sdk_revision_message}")
            endif()
        endif()
        message(STATUS "PS3Recomp HEAD:  ${PORT_SDK_REVISION}")
    endif()
else()
    message(STATUS "PS3Recomp HEAD:  unknown (not a git checkout)")
endif()

set(RECOMP_DIR "${PORT_ROOT}/build/game/recomp" CACHE PATH
    "Directory holding the lifter's output: ppu_recomp.h and the lifted sources")
get_filename_component(RECOMP_DIR "${RECOMP_DIR}" ABSOLUTE)

option(PORT_BUILD_TESTS_ONLY "Build synthetic runner tests and fixtures only, without the full game executable" OFF)
set(PORT_TESTS_USE_SYNTHETIC OFF CACHE BOOL
    "Compile native fixtures against tests/native/synthetic_recomp instead of a real lift")

if(PORT_BUILD_TESTS_ONLY)
    # The test-only mode must never read a real lift or any game file, even if
    # build/game/recomp happens to exist in the developer's tree.
    set(PORT_TESTS_USE_SYNTHETIC ON CACHE BOOL
        "Compile native fixtures against tests/native/synthetic_recomp" FORCE)
    message(STATUS "Mode:            Synthetic test fixtures only (PORT_BUILD_TESTS_ONLY=ON)")
    add_subdirectory("${PS3RECOMP_DIR}" "${CMAKE_BINARY_DIR}/ps3recomp_sdk" EXCLUDE_FROM_ALL)
    if(WIN32)
        enable_testing()
        add_subdirectory("${PORT_ROOT}/tests/native" "${CMAKE_BINARY_DIR}/runner-tests")
        message(STATUS "Tests:           ctest --test-dir ${CMAKE_BINARY_DIR}")
    else()
        message(STATUS "Tests:           disabled (native fixtures are Windows-only)")
    endif()
    return()
endif()

message(STATUS "ps3recomp SDK:   ${PS3RECOMP_DIR}")
message(STATUS "Lifted output:   ${RECOMP_DIR}")

if(NOT EXISTS "${RECOMP_DIR}/ppu_recomp.h")
    message(FATAL_ERROR
        "No ppu_recomp.h in ${RECOMP_DIR}.\n"
        "That header and the lifted sources beside it are what "
        "tools/ppu_lifter.py writes. Run the lifter, drop its output there, "
        "or point -DRECOMP_DIR= at wherever it already is.")
endif()

# The lifted translation units. A game's lift is hundreds of generated files,
# so they are globbed rather than listed; re-run CMake after a fresh lift.
file(GLOB RECOMP_SOURCES CONFIGURE_DEPENDS
    "${RECOMP_DIR}/*.c"
    "${RECOMP_DIR}/*.cpp"
)
if(NOT RECOMP_SOURCES)
    message(FATAL_ERROR
        "${RECOMP_DIR} has a ppu_recomp.h but no .c or .cpp beside it. The "
        "lifted functions and the function table live in those; without them "
        "there is nothing to run.")
endif()
list(LENGTH RECOMP_SOURCES RECOMP_SOURCE_COUNT)
message(STATUS "Lifted sources:  ${RECOMP_SOURCE_COUNT} file(s)")

# ---------------------------------------------------------------------------
# ps3recomp runtime library
# ---------------------------------------------------------------------------
# Built from source as a subdirectory rather than imported as a prebuilt
# archive. A static library carries none of its own link requirements, so an
# imported one arrives without SDL2, pthreads, the Metal frameworks or the
# Winsock/XInput imports -- the link then fails on every platform for a
# different reason. add_subdirectory hands all of that over with the target.
add_subdirectory("${PS3RECOMP_DIR}" "${CMAKE_BINARY_DIR}/ps3recomp_sdk"
                 EXCLUDE_FROM_ALL)

# ---------------------------------------------------------------------------
# The HLE NID table
# ---------------------------------------------------------------------------
# Maps each firmware import NID onto the handler in the runtime that answers
# it. A port may keep a hand-curated one beside its lift; otherwise generate
# the canonical every-module table, which is what the toolkit's own boot
# harness uses.
if(EXISTS "${RECOMP_DIR}/ppu_hle_nids.cpp")
    set(HLE_NID_TABLE "${RECOMP_DIR}/ppu_hle_nids.cpp")
    message(STATUS "HLE NID table:   ${HLE_NID_TABLE}")
else()
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
    set(HLE_NID_TABLE "${CMAKE_CURRENT_BINARY_DIR}/ppu_hle_nids.cpp")
    add_custom_command(
        OUTPUT "${HLE_NID_TABLE}"
        COMMAND "${Python3_EXECUTABLE}"
                "${PS3RECOMP_DIR}/tools/gen_hle_nids.py"
                --out "${HLE_NID_TABLE}" --all
        DEPENDS "${PS3RECOMP_DIR}/tools/gen_hle_nids.py"
                "${PS3RECOMP_DIR}/tools/nid_database.py"
        COMMENT "Generating the HLE NID -> handler table")
    message(STATUS "HLE NID table:   generated (every module)")
endif()

# ---------------------------------------------------------------------------
# The game executable
# ---------------------------------------------------------------------------
# The PPU boot scaffold is compiled here rather than taken from the runtime
# library: every one of these files #includes the lifter's per-game
# ppu_recomp.h, so they can only be built against a particular lift.
set(PS3RECOMP_SCAFFOLD
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_loader.cpp"
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_hle.cpp"
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_fs.cpp"
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_sysprx.cpp"
)

# ---------------------------------------------------------------------------
# Lifted SPU images (optional)
# ---------------------------------------------------------------------------
# A title that runs SPU work gets its images lifted into <SPU_DIR>/<image>/ by
# tools/spu_lifter.py, with a registration TU generated by
# tools/build_spu_workloads.py. cellSpurs dispatches them by content
# fingerprint, so the generated table is keyed to the images THIS build ships:
# a table generated from another release registers signatures that can never
# match, which is worse than shipping none. Both the table and the job-module
# registry self-register from constructors and are therefore optional -- a tree
# that has not lifted its SPU images yet configures and links without them.
set(SPU_DIR "${PORT_ROOT}/build/game/spu_lifted" CACHE PATH
    "Directory holding the lifted SPU images (<SPU_DIR>/<image>/spu_recomp.c)")
option(PORT_ENABLE_SPU "Compile and register the title's lifted SPU images" ON)
option(PORT_BINK_SSE41 "Use SSE4.1 helpers only for the Bink SPU image (requires host SSE4.1)" OFF)

set(SPU_SOURCES "")
if(PORT_ENABLE_SPU)
    file(GLOB SPU_LIFTED CONFIGURE_DEPENDS "${SPU_DIR}/*/spu_recomp.c")
    if(SPU_LIFTED)
        list(LENGTH SPU_LIFTED SPU_IMAGE_COUNT)
        message(STATUS "Lifted SPU:      ${SPU_IMAGE_COUNT} image(s)")
        # The musttail indirect-branch fast path. Excluded from the runtime
        # library on purpose (see the SDK root CMakeLists): it must be built
        # here, by clang, or the tail call degrades to a plain call and a guest
        # loop that branches indirectly walks the thread stack off its guard
        # page a few thousand iterations in.
        list(APPEND SPU_SOURCES ${SPU_LIFTED}
             "${PS3RECOMP_DIR}/runtime/spu/spu_dispatch_mt.c")
        # Generated beside the lift; each is present only once its generator
        # has been run, so each is included only if it is actually here.
        foreach(gen IN ITEMS spu_workloads.c jobmods_register.c taskset_policy_bytes.c spu_plugins_register.c)
            if(EXISTS "${SPU_DIR}/${gen}")
                list(APPEND SPU_SOURCES "${SPU_DIR}/${gen}")
            else()
                message(STATUS "  (no ${gen} -- not registered)")
            endif()
        endforeach()
    else()
        message(STATUS "Lifted SPU:      none found in ${SPU_DIR}")
    endif()
endif()

add_executable(${PROJECT_NAME}
    main.cpp
    stubs.cpp
    lfqueue.cpp
    fast_inflate.cpp
    lwcond.cpp
    bink_sem_stats.cpp
    gcm_flip_stats.cpp
    ${PS3RECOMP_SCAFFOLD}
    ${HLE_NID_TABLE}
    ${RECOMP_SOURCES}
    ${SPU_SOURCES}
)

# The lifted SPU sources use bare #include "spu_context.h". Scope that include
# directory (and their flags) to those TUs alone: adding it to the whole target
# changes the compile command of every lifted PPU TU, and those are large enough
# that the needless rebuild is measured in tens of minutes.
#   SPU_USE_DISPATCH_MT -- route SPU_IB_DISPATCH through the musttail path.
#   -O2                 -- the target-wide -O1 exists for the huge PPU TUs; the
#                          SPU sources are small and decode-hot, so they get
#                          full optimization. The last -O on the line wins.
if(SPU_SOURCES)
    set_source_files_properties(${SPU_SOURCES} PROPERTIES
        INCLUDE_DIRECTORIES "${PS3RECOMP_DIR}/runtime/spu"
        COMPILE_DEFINITIONS SPU_USE_DISPATCH_MT)
    if(NOT MSVC OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
        set_source_files_properties(${SPU_SOURCES} PROPERTIES COMPILE_OPTIONS "-O2")
    endif()
endif()

# Experiment is source-scoped: changing it must not rebuild lifted PPU TUs.
if(PORT_BINK_SSE41)
    if(NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(AMD64|amd64|x86_64|X86_64|x64|i[3-6]86|x86)$")
        message(FATAL_ERROR "PORT_BINK_SSE41 requires an x86/x64 target")
    endif()
    if(NOT CMAKE_C_COMPILER_ID MATCHES "^(Clang|GNU|AppleClang)$" OR
       NOT CMAKE_CXX_COMPILER_ID MATCHES "^(Clang|GNU|AppleClang)$")
        message(FATAL_ERROR "PORT_BINK_SSE41 requires Clang or GNU C/C++ compilers")
    endif()
    set(BINK_SSE41_SOURCE "${SPU_DIR}/spu_0001_at_0085E380/spu_recomp.c")
    if(NOT BINK_SSE41_SOURCE IN_LIST SPU_SOURCES)
        message(FATAL_ERROR "PORT_BINK_SSE41 requires the registered Bink SPU image source")
    endif()
    if(CMAKE_C_COMPILER_FRONTEND_VARIANT STREQUAL "MSVC")
        set(BINK_SSE41_FLAG "/clang:-msse4.1")
    else()
        set(BINK_SSE41_FLAG "-msse4.1")
    endif()
    set_property(SOURCE "${BINK_SSE41_SOURCE}" APPEND PROPERTY COMPILE_OPTIONS "${BINK_SSE41_FLAG}")
    set_property(SOURCE main.cpp APPEND PROPERTY COMPILE_DEFINITIONS I2_REQUIRE_BINK_SSE41=1)
    message(STATUS "Bink SPU SSE4.1: enabled (${BINK_SSE41_FLAG}); startup checks CPU support")
endif()

# app.rc carries the icon, the version block and the manifest, and needs a
# resource compiler. Listing it as a source on a host that has none is not a
# link error, it is a "no rule to make" for a file nothing can consume.
# The toolchain file resolves rc.exe before project(); this check also covers
# full builds configured with a plain `cmake -S src/port` invocation.
if(WIN32 AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/app.rc")
    include(CheckLanguage)
    check_language(RC)
    if(CMAKE_RC_COMPILER)
        enable_language(RC)
        target_sources(${PROJECT_NAME} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/app.rc")
        message(STATUS "Resource file:   app.rc (${CMAKE_RC_COMPILER})")
    else()
        message(WARNING
            "No resource compiler found; building without app.rc. "
            "The executable will lack its icon, version block and manifest.")
    endif()
endif()

target_include_directories(${PROJECT_NAME} PRIVATE
    "${RECOMP_DIR}"                            # the generated ppu_recomp.h
    "${PS3RECOMP_DIR}/include"
    "${PS3RECOMP_DIR}/runtime/ppu"
    "${PS3RECOMP_DIR}/runtime/spu"
    "${PS3RECOMP_DIR}/runtime/memory"
    "${PS3RECOMP_DIR}/runtime/syscalls"
    "${PS3RECOMP_DIR}/runtime/platform"        # win32_compat.h and friends
    "${PS3RECOMP_DIR}/libs/video"
    "${CMAKE_CURRENT_SOURCE_DIR}"
)

target_link_libraries(${PROJECT_NAME} PRIVATE
    ps3recomp_runtime
)
set_source_files_properties(lfqueue.cpp PROPERTIES
    INCLUDE_DIRECTORIES "${PS3RECOMP_DIR}/libs/spurs")

# The runtime is C++, and on Apple also Objective-C. Letting CMake infer the
# link language from the generated C would leave the C++ standard library out.
set_target_properties(${PROJECT_NAME} PROPERTIES LINKER_LANGUAGE CXX)

# Windows system import libraries. dbghelp symbolizes crash-handler addresses
# against the PDB, user32 and gdi32 are what the backend's window and its GDI
# present are made of, and winmm is timeBeginPeriod. The D3D12 backend uses
# IID_IDXGIFactory6, whose definition is in the Windows SDK's dxguid.lib.
# The backend's #pragma comment(lib) directives cover its other imports.
if(WIN32)
    target_link_libraries(${PROJECT_NAME} PRIVATE
        dbghelp
        user32
        gdi32
        winmm
        dxguid
    )
endif()

# ---------------------------------------------------------------------------
# Compiler-specific flags
# ---------------------------------------------------------------------------
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
    target_compile_options(${PROJECT_NAME} PRIVATE
        -Wall -Wextra -Wno-unused-parameter
        -fno-strict-aliasing
    )
elseif(MSVC)
    target_compile_options(${PROJECT_NAME} PRIVATE
        /W3 /wd4100 /wd4244 /wd4267
        /Zc:__cplusplus         # report correct __cplusplus value
        # ppu_recomp.h defines PPU_FENCE, the lowering of the guest's
        # sync/lwsync/eieio/isync, and in C that is <stdatomic.h>. The lifter
        # writes C++ by default, but --single-file writes ppu_recomp.c, and a
        # port that took it would not compile without this. The runtime's own
        # build already carries the same flag for the same reason.
        /experimental:c11atomics
    )
    target_compile_definitions(${PROJECT_NAME} PRIVATE
        _CRT_SECURE_NO_WARNINGS
    )
endif()

# The lifted translation units are machine-generated and can run to hundreds of
# thousands of lines each, so they get their own flags:
#
#   - warnings off. They are about generated code and nobody will act on them,
#     and at this volume they bury the diagnostics that matter.
#   - no floating-point contraction. arm64 fuses a multiply and an add by
#     default; a PPC fmadd is spelt as one where the lifter means one.
#   - /bigobj on MSVC. A single lifted file routinely passes the 65,536-section
#     limit of the default object format.
if(MSVC)
    set_source_files_properties(${RECOMP_SOURCES} PROPERTIES
        COMPILE_OPTIONS "/w;/bigobj;/Od")
else()
    set_source_files_properties(${RECOMP_SOURCES} PROPERTIES
        COMPILE_OPTIONS "-w;-ffp-contract=off")
endif()

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
install(TARGETS ${PROJECT_NAME} RUNTIME DESTINATION bin)

option(PORT_RUNNER_TESTS "Build synthetic runner tests without executing game code" OFF)
if(PORT_RUNNER_TESTS AND WIN32)
    # Runner tests inside a full build may use the real lift, but they must
    # never silently inherit a stale test-only cache entry.
    set(PORT_TESTS_USE_SYNTHETIC OFF CACHE BOOL
        "Compile native fixtures against tests/native/synthetic_recomp" FORCE)
    enable_testing()
    add_subdirectory("${PORT_ROOT}/tests/native" "${CMAKE_BINARY_DIR}/runner-tests")
endif()

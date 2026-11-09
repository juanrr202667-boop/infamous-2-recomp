# Top-level entry point.
#
# The native runner lives in src/port/. Keeping the build logic there preserves
# the existing `cmake -S src/port -B build/dev` workflow, while this wrapper
# makes `cmake --preset ...` and `cmake -S . -B build` work from the root.
cmake_minimum_required(VERSION 3.25)

project(inFAMOUS2Recomp LANGUAGES NONE)

# CTest only descends from directories that called enable_testing(). The
# runner subdirectory does that for its own tests; the root must do it too so
# `ctest --test-dir build/...` discovers them.
enable_testing()

add_subdirectory(src/port)

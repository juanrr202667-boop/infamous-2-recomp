cmake_minimum_required(VERSION 3.20)
project(SpursAbiProbe LANGUAGES C CXX)
get_filename_component(ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../.." ABSOLUTE)
set(SDK "${ROOT}/tools/ps3recomp")
add_executable(spurs_abi_probe probe.cpp "${SDK}/libs/spurs/cellSpurs.c"
  "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
  "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spurs_abi_probe PRIVATE cxx_std_20)
target_compile_definitions(spurs_abi_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_compile_options(spurs_abi_probe PRIVATE /EHsc)
target_include_directories(spurs_abi_probe PRIVATE "${ROOT}/tests/native/synthetic_recomp"
  "${SDK}/include" "${SDK}/runtime/platform" "${SDK}/runtime/spu"
  "${SDK}/runtime/ppu" "${SDK}/runtime/memory" "${SDK}/libs/spurs")
target_link_libraries(spurs_abi_probe PRIVATE "${ROOT}/build/windows-tests/ps3recomp_sdk/ps3recomp_runtime.lib" dbghelp bcrypt)
enable_testing()
foreach(mode IN ITEMS 5 1 null workload-1 workload-5 workload-6 workload-null generic-eight)
  add_test(NAME spurs_attribute_abi_${mode} COMMAND spurs_abi_probe ${mode})
endforeach()

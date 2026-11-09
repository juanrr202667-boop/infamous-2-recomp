cmake_minimum_required(VERSION 3.20)
project(SyntheticMicrostep LANGUAGES C CXX)
get_filename_component(PORT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../.." ABSOLUTE)
set(SDK "${PORT_ROOT}/tools/ps3recomp")
set(TEST_RECOMP_INCLUDE "${PORT_ROOT}/tests/native/synthetic_recomp")
add_executable(microstep_probe microstep_probe.cpp
    "${SDK}/runtime/spu/spu_channels.c"
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(microstep_probe PRIVATE cxx_std_20)
target_compile_definitions(microstep_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
if(MSVC)
  target_compile_options(microstep_probe PRIVATE /EHsc)
endif()
target_include_directories(microstep_probe PRIVATE "${TEST_RECOMP_INCLUDE}"
    "${SDK}/include" "${SDK}/runtime/platform" "${SDK}/runtime/spu"
    "${SDK}/runtime/ppu" "${SDK}/runtime/memory" "${SDK}/libs/spurs")
target_link_libraries(microstep_probe PRIVATE
    "${PORT_ROOT}/build/windows-tests/ps3recomp_sdk/ps3recomp_runtime.lib" dbghelp bcrypt)

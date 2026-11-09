cmake_minimum_required(VERSION 3.20)
project(LFQueueSignalContract LANGUAGES CXX)

get_filename_component(PORT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../.." ABSOLUTE)
set(SDK "${PORT_ROOT}/tools/ps3recomp")
set(TEST_RECOMP_INCLUDE "${PORT_ROOT}/tests/native/synthetic_recomp")
set(PS3RECOMP_RUNTIME_LIB
    "${PORT_ROOT}/build/windows-tests/ps3recomp_sdk/ps3recomp_runtime.lib"
    CACHE FILEPATH "Path to the already-built ps3recomp runtime library")

add_executable(lfqueue_signal_contract_probe
    lfqueue_signal_contract_probe.cpp
    "${PORT_ROOT}/src/port/stubs.cpp"
    "${PORT_ROOT}/src/port/lwcond.cpp"
    "${PORT_ROOT}/src/port/lfqueue.cpp"
    "${SDK}/runtime/ppu/ppu_loader.cpp"
    "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp"
    "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(lfqueue_signal_contract_probe PRIVATE cxx_std_20)
target_compile_definitions(lfqueue_signal_contract_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
if(MSVC)
    target_compile_options(lfqueue_signal_contract_probe PRIVATE /EHsc)
endif()
target_include_directories(lfqueue_signal_contract_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory"
    "${SDK}/libs/spurs")
set_source_files_properties("${PORT_ROOT}/src/port/lfqueue.cpp" PROPERTIES
    INCLUDE_DIRECTORIES "${SDK}/libs/spurs")
target_link_libraries(lfqueue_signal_contract_probe PRIVATE
    "${PS3RECOMP_RUNTIME_LIB}" dbghelp bcrypt)

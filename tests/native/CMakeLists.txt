cmake_minimum_required(VERSION 3.20)
project(Infamous2RunnerTests LANGUAGES CXX)
get_filename_component(PORT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
if(DEFINED PS3RECOMP_DIR AND PS3RECOMP_DIR)
    set(SDK "${PS3RECOMP_DIR}")
else()
    set(SDK "${PORT_ROOT}/tools/ps3recomp")
endif()
get_filename_component(SDK "${SDK}" ABSOLUTE)
if(NOT DEFINED PORT_TESTS_USE_SYNTHETIC)
    set(PORT_TESTS_USE_SYNTHETIC OFF)
endif()

if(PORT_TESTS_USE_SYNTHETIC)
    set(TEST_RECOMP_INCLUDE "${CMAKE_CURRENT_SOURCE_DIR}/synthetic_recomp")
    message(STATUS "Runner fixture header: synthetic (no game code)")
elseif(EXISTS "${PORT_ROOT}/build/game/recomp/ppu_recomp.h")
    set(TEST_RECOMP_INCLUDE "${PORT_ROOT}/build/game/recomp")
    message(STATUS "Runner fixture header: local lift")
else()
    set(TEST_RECOMP_INCLUDE "${CMAKE_CURRENT_SOURCE_DIR}/synthetic_recomp")
    message(STATUS "Runner fixture header: synthetic fallback (no local lift found)")
endif()

if(MSVC)
    set(I2_PROBE_COMPILE_OPTIONS /Gy /Gw /O1)
    set(I2_PROBE_LINK_OPTIONS /OPT:REF)
else()
    set(I2_PROBE_COMPILE_OPTIONS -O1)
    set(I2_PROBE_LINK_OPTIONS -Wl,--gc-sections)
endif()

add_executable(frame_clock_probe frame_clock_probe.cpp)
target_compile_features(frame_clock_probe PRIVATE cxx_std_20)
target_compile_definitions(frame_clock_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(frame_clock_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include"
    "${SDK}/runtime/ppu" "${SDK}/runtime/spu" "${SDK}/runtime/platform"
    "${SDK}/runtime/memory" "${SDK}/libs/video")
# Drop the unused game entry function included from the runner. The executable
# contains only the clock and synthetic FIFO callbacks, without lifted code.
target_compile_options(frame_clock_probe PRIVATE ${I2_PROBE_COMPILE_OPTIONS})
target_link_options(frame_clock_probe PRIVATE ${I2_PROBE_LINK_OPTIONS})
target_link_libraries(frame_clock_probe PRIVATE winmm)
enable_testing()
add_executable(wws_target_scan_probe wws_target_scan_probe.cpp)
target_compile_features(wws_target_scan_probe PRIVATE cxx_std_17)
target_compile_definitions(wws_target_scan_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(wws_target_scan_probe PRIVATE
    "${PORT_ROOT}/src/port" "${SDK}/runtime/platform")
add_test(NAME wws_target_scan_bounds COMMAND wws_target_scan_probe)
set_tests_properties(wws_target_scan_bounds PROPERTIES TIMEOUT 5)
add_executable(spin_snapshot_probe spin_snapshot_probe.cpp)
target_compile_features(spin_snapshot_probe PRIVATE cxx_std_17)
add_test(NAME spin_snapshot_pending_markers COMMAND spin_snapshot_probe)
set_tests_properties(spin_snapshot_pending_markers PROPERTIES TIMEOUT 5)
add_test(NAME frame_clock_single_flip COMMAND frame_clock_probe)
set_tests_properties(frame_clock_single_flip PROPERTIES TIMEOUT 5)

# Fragment-program pack/unpack opcodes and their sRGB helper emission.
add_executable(rsx_vertex_cmp_probe rsx_vertex_cmp_probe.c "${SDK}/libs/video/rsx_vertex_fetch.c")
target_include_directories(rsx_vertex_cmp_probe PRIVATE
    "${SDK}/include" "${SDK}/runtime/platform" "${SDK}/libs/video")
target_compile_definitions(rsx_vertex_cmp_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
add_test(NAME rsx_vertex_cmp_fetch COMMAND rsx_vertex_cmp_probe)
set_tests_properties(rsx_vertex_cmp_fetch PROPERTIES TIMEOUT 5)

add_executable(fp_pack_probe fp_pack_probe.c "${SDK}/libs/video/rsx_fp_decompiler.c")
target_include_directories(fp_pack_probe PRIVATE
    "${SDK}/include" "${SDK}/runtime/platform" "${SDK}/libs/video")
target_compile_definitions(fp_pack_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
add_test(NAME fp_pack_opcodes COMMAND fp_pack_probe)
set_tests_properties(fp_pack_opcodes PROPERTIES TIMEOUT 5)

# Real dispatcher with a capture backend; no GPU or proprietary input required.
add_executable(rsx_immediate_probe rsx_immediate_probe.c
    "${SDK}/libs/video/rsx_commands.c")
target_include_directories(rsx_immediate_probe PRIVATE
    "${SDK}/include" "${SDK}/runtime/platform" "${SDK}/libs/video")
target_compile_definitions(rsx_immediate_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
foreach(mode movie constants terminal arrays indexed overflow inline)
    add_test(NAME rsx_immediate_${mode} COMMAND rsx_immediate_probe ${mode})
    set_tests_properties(rsx_immediate_${mode} PROPERTIES TIMEOUT 5)
endforeach()

# Real PortOpen/Start/mixer/Close over synthetic big-endian guest memory.
add_executable(audio_port_level_probe audio_port_level_probe.c)
target_include_directories(audio_port_level_probe PRIVATE
    "${SDK}/include" "${SDK}/runtime/platform" "${SDK}/libs/audio")
target_compile_definitions(audio_port_level_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_compile_options(audio_port_level_probe PRIVATE ${I2_PROBE_COMPILE_OPTIONS})
target_link_options(audio_port_level_probe PRIVATE ${I2_PROBE_LINK_OPTIONS})
target_link_libraries(audio_port_level_probe PRIVATE ole32)
add_test(NAME audio_port_initial_level COMMAND audio_port_level_probe
    "${CMAKE_CURRENT_BINARY_DIR}/audio-port-level.log")
set_tests_properties(audio_port_initial_level PROPERTIES TIMEOUT 5)

# Real NV3089 copies and metadata geometry; synthetic guest memory, no GPU.
add_executable(nv3089_probe nv3089_probe.c)
target_include_directories(nv3089_probe PRIVATE
    "${SDK}/include" "${SDK}/runtime/platform" "${SDK}/libs/video")
target_compile_definitions(nv3089_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_compile_options(nv3089_probe PRIVATE ${I2_PROBE_COMPILE_OPTIONS})
target_link_options(nv3089_probe PRIVATE ${I2_PROBE_LINK_OPTIONS})
foreach(mode copy disabled cap clip layout origin target writeback)
    add_test(NAME nv3089_probe_${mode} COMMAND nv3089_probe ${mode})
    set_tests_properties(nv3089_probe_${mode} PROPERTIES TIMEOUT 5)
endforeach()

# Real GCM flip insertion and FIFO decoding; synthetic guest memory, no GPU.
add_executable(gcm_flip_wrap_probe gcm_flip_wrap_probe.c)
target_include_directories(gcm_flip_wrap_probe PRIVATE
    "${SDK}/include" "${SDK}/runtime/platform" "${SDK}/libs/video")
target_compile_definitions(gcm_flip_wrap_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_compile_options(gcm_flip_wrap_probe PRIVATE ${I2_PROBE_COMPILE_OPTIONS})
target_link_options(gcm_flip_wrap_probe PRIVATE ${I2_PROBE_LINK_OPTIONS})
foreach(mode runtime-ring title-context title-tail ample-room rejected zero-callback
        small-title small-null exact-fit park-retry park-long-retry park-at-put normal-jump)
    add_test(NAME gcm_flip_wrap_${mode} COMMAND gcm_flip_wrap_probe ${mode})
    set_tests_properties(gcm_flip_wrap_${mode} PROPERTIES TIMEOUT 5)
endforeach()

foreach(mode budget-park budget-circle)
    add_test(NAME gcm_flip_wrap_${mode} COMMAND gcm_flip_wrap_probe ${mode})
    set_tests_properties(gcm_flip_wrap_${mode} PROPERTIES TIMEOUT 15)
endforeach()

# Keep optional diagnostics from changing real list DMA or policy handoffs.
add_executable(light_callsite_probe light_callsite_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(light_callsite_probe PRIVATE cxx_std_20)
target_compile_definitions(light_callsite_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(light_callsite_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(light_callsite_probe PRIVATE ps3recomp_runtime dbghelp)
add_test(NAME light_callsite_nested COMMAND light_callsite_probe)
add_test(NAME light_callsite_default_off COMMAND light_callsite_probe off)
set_tests_properties(light_callsite_nested light_callsite_default_off PROPERTIES TIMEOUT 5)

add_executable(light_wait_probe light_wait_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(light_wait_probe PRIVATE cxx_std_20)
target_compile_definitions(light_wait_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(light_wait_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(light_wait_probe PRIVATE ps3recomp_runtime dbghelp)
add_test(NAME light_wait_observed COMMAND light_wait_probe)
add_test(NAME light_wait_default_off COMMAND light_wait_probe off)
add_test(NAME light_wait_second_lane COMMAND light_wait_probe second)
add_test(NAME light_wait_invalid_pointer COMMAND light_wait_probe invalid)
set_tests_properties(light_wait_observed light_wait_default_off light_wait_second_lane light_wait_invalid_pointer PROPERTIES TIMEOUT 5)

add_executable(light_loop_probe light_loop_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(light_loop_probe PRIVATE cxx_std_20)
target_compile_definitions(light_loop_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(light_loop_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(light_loop_probe PRIVATE ps3recomp_runtime dbghelp)
add_test(NAME light_loop_observe COMMAND light_loop_probe)
add_test(NAME light_loop_default_off COMMAND light_loop_probe off)
set_tests_properties(light_loop_observe light_loop_default_off PROPERTIES TIMEOUT 5)

add_executable(pm_quantum_probe pm_quantum_probe.cpp)
target_include_directories(pm_quantum_probe PRIVATE "${SDK}/runtime/spu")
target_compile_definitions(pm_quantum_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
add_test(NAME pm_quantum_observe COMMAND pm_quantum_probe)
add_test(NAME pm_quantum_default_off COMMAND pm_quantum_probe off)
set_tests_properties(pm_quantum_observe pm_quantum_default_off PROPERTIES TIMEOUT 5)

add_executable(wws_wait_probe wws_wait_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(wws_wait_probe PRIVATE cxx_std_20)
target_compile_definitions(wws_wait_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(wws_wait_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(wws_wait_probe PRIVATE ps3recomp_runtime dbghelp)
add_test(NAME wws_wait_limits COMMAND wws_wait_probe)
add_test(NAME wws_wait_default_off COMMAND wws_wait_probe off)
set_tests_properties(wws_wait_limits wws_wait_default_off PROPERTIES TIMEOUT 5)

add_executable(sampler_direct_probe sampler_direct_probe.cpp
    "${SDK}/runtime/ppu/ppu_hle.cpp" "${SDK}/runtime/ppu/ppu_fs.cpp"
    "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(sampler_direct_probe PRIVATE cxx_std_20)
target_compile_definitions(sampler_direct_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(sampler_direct_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(sampler_direct_probe PRIVATE ps3recomp_runtime dbghelp)
add_test(NAME sampler_stack_default COMMAND sampler_direct_probe)
add_test(NAME sampler_stack_direct_only COMMAND sampler_direct_probe direct)
add_test(NAME sampler_direct_guest COMMAND sampler_direct_probe guest)
add_test(NAME sampler_rip_windows COMMAND sampler_direct_probe rip)
add_test(NAME sampler_rip_boundaries COMMAND sampler_direct_probe boundaries)
set_tests_properties(sampler_stack_default sampler_stack_direct_only sampler_direct_guest sampler_rip_windows sampler_rip_boundaries PROPERTIES TIMEOUT 5)

add_executable(hle_snapshot_probe hle_snapshot_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(hle_snapshot_probe PRIVATE cxx_std_20)
target_compile_definitions(hle_snapshot_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(hle_snapshot_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(hle_snapshot_probe PRIVATE ps3recomp_runtime dbghelp)
add_test(NAME hle_snapshot_inflight COMMAND hle_snapshot_probe)
add_test(NAME hle_snapshot_default_off COMMAND hle_snapshot_probe off)
set_tests_properties(hle_snapshot_inflight hle_snapshot_default_off PROPERTIES TIMEOUT 5)

add_executable(sc_snapshot_probe sc_snapshot_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(sc_snapshot_probe PRIVATE cxx_std_20)
target_compile_definitions(sc_snapshot_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(sc_snapshot_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory" "${SDK}/runtime/syscalls")
target_link_libraries(sc_snapshot_probe PRIVATE ps3recomp_runtime dbghelp)
add_test(NAME sc_snapshot_inflight COMMAND sc_snapshot_probe)
set_tests_properties(sc_snapshot_inflight PROPERTIES TIMEOUT 5)
add_test(NAME sc_snapshot_default_off COMMAND sc_snapshot_probe off)
set_tests_properties(sc_snapshot_default_off PROPERTIES TIMEOUT 5)

add_executable(spu_hot_trace_probe spu_hot_trace_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_hot_trace_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_hot_trace_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_hot_trace_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(spu_hot_trace_probe PRIVATE ps3recomp_runtime dbghelp)
foreach(mode dma-off dma-on dma-zero pm-off pm-on pm-zero)
    add_test(NAME spu_hot_trace_${mode} COMMAND spu_hot_trace_probe ${mode})
    set_tests_properties(spu_hot_trace_${mode} PROPERTIES TIMEOUT 5)
endforeach()

add_executable(lfqueue_probe lfqueue_probe.cpp "${PORT_ROOT}/src/port/stubs.cpp"
    "${PORT_ROOT}/src/port/lwcond.cpp"
    "${PORT_ROOT}/src/port/lfqueue.cpp"
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(lfqueue_probe PRIVATE cxx_std_20)
target_compile_definitions(lfqueue_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(lfqueue_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory" "${SDK}/libs/spurs")
set_source_files_properties("${PORT_ROOT}/src/port/lfqueue.cpp" PROPERTIES
    INCLUDE_DIRECTORIES "${SDK}/libs/spurs")
target_link_libraries(lfqueue_probe PRIVATE ps3recomp_runtime dbghelp)
add_test(NAME lfqueue_guest_initialize COMMAND lfqueue_probe)
set_tests_properties(lfqueue_guest_initialize PROPERTIES TIMEOUT 5)
add_test(NAME lfqueue_guest_push COMMAND lfqueue_probe push)
set_tests_properties(lfqueue_guest_push PROPERTIES TIMEOUT 5)
add_test(NAME lfqueue_guest_notify COMMAND lfqueue_probe notify)
set_tests_properties(lfqueue_guest_notify PROPERTIES TIMEOUT 5)
add_test(NAME lfqueue_guest_tagged_owner COMMAND lfqueue_probe tagged)
set_tests_properties(lfqueue_guest_tagged_owner PROPERTIES TIMEOUT 5)
add_test(NAME lfq_review_invalid_init COMMAND lfqueue_probe invalid-init)
set_tests_properties(lfq_review_invalid_init PROPERTIES TIMEOUT 5)
add_test(NAME lfq_review_destroy COMMAND lfqueue_probe destroy)
add_test(NAME lfq_review_shutdown COMMAND lfqueue_probe shutdown)
set_tests_properties(lfq_review_destroy lfq_review_shutdown PROPERTIES TIMEOUT 5)
add_test(NAME lfq_review_full_pending_pop COMMAND lfqueue_probe full)
add_test(NAME lfqueue_full_registered_taskset COMMAND lfqueue_probe full-taskset)
set_tests_properties(lfqueue_full_registered_taskset PROPERTIES TIMEOUT 5)
add_test(NAME lfq_review_wrap COMMAND lfqueue_probe wrap)
add_test(NAME lfq_review_unsupported COMMAND lfqueue_probe unsupported)
set_tests_properties(lfq_review_full_pending_pop lfq_review_wrap lfq_review_unsupported PROPERTIES TIMEOUT 5)

add_executable(fs_entries_probe fs_entries_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(fs_entries_probe PRIVATE cxx_std_20)
target_compile_definitions(fs_entries_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(fs_entries_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(fs_entries_probe PRIVATE ps3recomp_runtime dbghelp)
add_test(NAME fs_entries_single COMMAND fs_entries_probe single)
add_test(NAME fs_entries_batch_eof COMMAND fs_entries_probe batch)
add_test(NAME fs_entries_invalid_no_consume COMMAND fs_entries_probe invalid)
set_tests_properties(fs_entries_single fs_entries_batch_eof fs_entries_invalid_no_consume PROPERTIES TIMEOUT 5)

add_executable(spurs_events_probe spurs_events_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spurs_events_probe PRIVATE cxx_std_20)
target_compile_definitions(spurs_events_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spurs_events_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory"
    "${SDK}/runtime/syscalls" "${SDK}/libs/spurs")
target_link_libraries(spurs_events_probe PRIVATE ps3recomp_runtime dbghelp)
add_test(NAME spurs_explicit_events COMMAND spurs_events_probe)
add_test(NAME spurs_explicit_events_attribute COMMAND spurs_events_probe attribute)
set_tests_properties(spurs_explicit_events spurs_explicit_events_attribute PROPERTIES TIMEOUT 5)

add_executable(spu_handoff_probe spu_handoff_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_handoff_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_handoff_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_handoff_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(spu_handoff_probe PRIVATE ps3recomp_runtime dbghelp)
add_test(NAME spu_handoff_rejoin COMMAND spu_handoff_probe)
add_test(NAME spu_handoff_caller_return COMMAND spu_handoff_probe return)
add_test(NAME spu_handoff_stop COMMAND spu_handoff_probe stop)
set_tests_properties(spu_handoff_rejoin spu_handoff_caller_return spu_handoff_stop PROPERTIES TIMEOUT 5)
add_test(NAME spu_handoff_absolute_load COMMAND spu_handoff_probe absolute-load)
add_test(NAME spu_handoff_absolute_store COMMAND spu_handoff_probe absolute-store)
set_tests_properties(spu_handoff_absolute_load spu_handoff_absolute_store PROPERTIES TIMEOUT 5)

add_executable(spu_multiply_probe spu_multiply_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_multiply_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_multiply_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_multiply_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(spu_multiply_probe PRIVATE ps3recomp_runtime dbghelp)
foreach(mode observed negative minimum maximum zero alias signed)
    add_test(NAME spu_multiply_${mode} COMMAND spu_multiply_probe ${mode})
    set_tests_properties(spu_multiply_${mode} PROPERTIES TIMEOUT 5)
endforeach()

find_package(Python3 REQUIRED COMPONENTS Interpreter)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/receive_emitted.h"
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/emit_spu_receive.py"
        "${CMAKE_CURRENT_BINARY_DIR}/receive_emitted.h"
    DEPENDS emit_spu_receive.py "${SDK}/tools/spu_lifter.py")
add_executable(spu_receive_probe spu_receive_probe.cpp
    "${CMAKE_CURRENT_BINARY_DIR}/receive_emitted.h"
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_receive_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_receive_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_receive_probe PRIVATE
    "${CMAKE_CURRENT_BINARY_DIR}"
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory"
    "${SDK}/runtime/syscalls")
target_link_libraries(spu_receive_probe PRIVATE ps3recomp_runtime dbghelp)
add_test(NAME spu_receive_queued COMMAND spu_receive_probe)
add_test(NAME spu_receive_delayed COMMAND spu_receive_probe delayed)
add_test(NAME spu_receive_cancel COMMAND spu_receive_probe cancel)
add_test(NAME spu_receive_invalid COMMAND spu_receive_probe invalid)
add_test(NAME spu_receive_interpreted COMMAND spu_receive_probe interpreted)
add_test(NAME spu_receive_large_key COMMAND spu_receive_probe large-key)
add_test(NAME spu_receive_try_empty COMMAND spu_receive_probe try-empty)
add_test(NAME spu_receive_try_queued COMMAND spu_receive_probe try-queued)
set_tests_properties(spu_receive_try_empty spu_receive_try_queued PROPERTIES TIMEOUT 5)
set_tests_properties(spu_receive_large_key PROPERTIES TIMEOUT 5)
set_tests_properties(spu_receive_interpreted PROPERTIES TIMEOUT 5)
set_tests_properties(spu_receive_queued spu_receive_delayed spu_receive_cancel spu_receive_invalid PROPERTIES TIMEOUT 5)

# Real task WAIT_SIGNAL and atomic channels, with event-coordinated OS waits.
add_executable(spu_mfc_isolation_probe spu_mfc_isolation_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_mfc_isolation_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_mfc_isolation_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_mfc_isolation_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(spu_mfc_isolation_probe PRIVATE ps3recomp_runtime dbghelp)
foreach(mode private overflow-same overflow-other overflow-reclaim)
    add_test(NAME spu_mfc_isolation_${mode} COMMAND spu_mfc_isolation_probe ${mode})
    set_tests_properties(spu_mfc_isolation_${mode} PROPERTIES TIMEOUT 10)
endforeach()

add_executable(spu_task_wake_probe spu_task_wake_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_task_wake_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_task_wake_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_task_wake_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(spu_task_wake_probe PRIVATE ps3recomp_runtime dbghelp)
foreach(mode putllc putlluc putqlluc failed-putllc unrelated-putllc unrelated-putlluc
        no-target-putllc no-target-putlluc early-putllc early-putlluc)
    add_test(NAME spu_task_wake_${mode} COMMAND spu_task_wake_probe ${mode})
    set_tests_properties(spu_task_wake_${mode} PROPERTIES TIMEOUT 6)
endforeach()

add_executable(lwcond_probe lwcond_probe.cpp "${PORT_ROOT}/src/port/stubs.cpp"
    "${PORT_ROOT}/src/port/lwcond.cpp"
    "${PORT_ROOT}/src/port/lfqueue.cpp"
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(lwcond_probe PRIVATE cxx_std_20)
target_compile_definitions(lwcond_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(lwcond_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory" "${SDK}/libs/spurs")
target_link_libraries(lwcond_probe PRIVATE ps3recomp_runtime dbghelp)
foreach(mode timeout delayed one all targeted invalid destroy stale-signal recursive infinite huge-timeout)
    add_test(NAME lwcond_${mode} COMMAND lwcond_probe ${mode})
    set_tests_properties(lwcond_${mode} PROPERTIES TIMEOUT 6)
endforeach()

add_executable(spu_thread_event_probe spu_thread_event_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_thread_event_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_thread_event_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_thread_event_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory"
    "${SDK}/runtime/syscalls")
target_link_libraries(spu_thread_event_probe PRIVATE ps3recomp_runtime dbghelp)
foreach(mode send throw precedence group unbound identity)
    add_test(NAME spu_thread_event_${mode} COMMAND spu_thread_event_probe ${mode})
    set_tests_properties(spu_thread_event_${mode} PROPERTIES TIMEOUT 5)
endforeach()

add_executable(spu_shift_probe spu_shift_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_shift_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_shift_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_shift_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(spu_shift_probe PRIVATE ps3recomp_runtime dbghelp)
foreach(mode observed zero last-bit wide modular positive alias)
    add_test(NAME spu_shift_${mode} COMMAND spu_shift_probe ${mode})
    set_tests_properties(spu_shift_${mode} PROPERTIES TIMEOUT 5)
endforeach()

add_executable(spu_dma_zero_probe spu_dma_zero_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_dma_zero_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_dma_zero_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_dma_zero_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(spu_dma_zero_probe PRIVATE ps3recomp_runtime dbghelp)
foreach(mode zero-get zero-put zero-put-low zero-dmachk oversized unaligned nonmultiple low-put valid-get valid-put)
    add_test(NAME spu_dma_${mode} COMMAND spu_dma_zero_probe ${mode})
    set_tests_properties(spu_dma_${mode} PROPERTIES TIMEOUT 5)
endforeach()

add_executable(spu_orbi_probe spu_orbi_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_orbi_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_orbi_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_orbi_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(spu_orbi_probe PRIVATE ps3recomp_runtime dbghelp)
foreach(mode observed negative minimum maximum high-only zero signed-byte alias word-positive word-negative)
    add_test(NAME spu_orbi_${mode} COMMAND spu_orbi_probe ${mode})
    set_tests_properties(spu_orbi_${mode} PROPERTIES TIMEOUT 5)
endforeach()

add_executable(spu_policy_argument_probe spu_policy_argument_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_policy_argument_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_policy_argument_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_policy_argument_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(spu_policy_argument_probe PRIVATE ps3recomp_runtime dbghelp)
foreach(mode distinct-high zero-high)
    add_test(NAME spu_policy_argument_${mode} COMMAND spu_policy_argument_probe ${mode})
    set_tests_properties(spu_policy_argument_${mode} PROPERTIES TIMEOUT 5)
endforeach()

add_executable(spu_overlay_lookup_probe spu_overlay_lookup_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_overlay_lookup_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_overlay_lookup_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_overlay_lookup_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(spu_overlay_lookup_probe PRIVATE ps3recomp_runtime dbghelp)
foreach(mode resident-code resident-ovl ownership owner-unlifted resident-task base null-context
        full-wws1-owner-unlifted full-wws2-owner-unlifted full-wws-signature-owner-unlifted
        full-wws-no-owner full-owner-lifted full-non-wws-owner-unlifted)
    add_test(NAME spu_overlay_lookup_${mode} COMMAND spu_overlay_lookup_probe ${mode})
    set_tests_properties(spu_overlay_lookup_${mode} PROPERTIES TIMEOUT 5)
endforeach()

add_executable(spu_rotmah_probe spu_rotmah_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_rotmah_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_rotmah_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_rotmah_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(spu_rotmah_probe PRIVATE ps3recomp_runtime dbghelp)
foreach(mode observed negative wide modular positive zero alias-source alias-count)
    add_test(NAME spu_rotmah_${mode} COMMAND spu_rotmah_probe ${mode})
    set_tests_properties(spu_rotmah_${mode} PROPERTIES TIMEOUT 5)
endforeach()

add_executable(spu_xorbi_probe spu_xorbi_probe.cpp
    "${SDK}/runtime/ppu/ppu_loader.cpp" "${SDK}/runtime/ppu/ppu_hle.cpp"
    "${SDK}/runtime/ppu/ppu_fs.cpp" "${SDK}/runtime/ppu/ppu_sysprx.cpp")
target_compile_features(spu_xorbi_probe PRIVATE cxx_std_20)
target_compile_definitions(spu_xorbi_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(spu_xorbi_probe PRIVATE
    "${TEST_RECOMP_INCLUDE}" "${SDK}/include" "${SDK}/runtime/platform"
    "${SDK}/runtime/spu" "${SDK}/runtime/ppu" "${SDK}/runtime/memory")
target_link_libraries(spu_xorbi_probe PRIVATE ps3recomp_runtime dbghelp)
foreach(mode observed second observed-alias negative minimum maximum high-only signed-byte zero alias word-positive word-negative)
    add_test(NAME spu_xorbi_${mode} COMMAND spu_xorbi_probe ${mode})
    set_tests_properties(spu_xorbi_${mode} PROPERTIES TIMEOUT 5)
endforeach()

# Use the same guest root signature and descriptor indexing as the renderer.
# WARP exercises actual PSO validation and pixels without a window or game data.
add_executable(rsx_guest_texture_probe rsx_guest_texture_probe.cpp)
target_compile_features(rsx_guest_texture_probe PRIVATE cxx_std_20)
target_compile_definitions(rsx_guest_texture_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(rsx_guest_texture_probe PRIVATE
    "${SDK}/include" "${SDK}/runtime/platform" "${SDK}/libs/video")
target_link_libraries(rsx_guest_texture_probe PRIVATE d3d12 dxgi d3dcompiler dxguid)
foreach(unit 0 3 4 15)
    add_test(NAME rsx_guest_texture_${unit} COMMAND rsx_guest_texture_probe ${unit})
    set_tests_properties(rsx_guest_texture_${unit} PROPERTIES TIMEOUT 20)
endforeach()

add_executable(rsx_mrt_display_probe rsx_mrt_display_probe.cpp)
target_compile_features(rsx_mrt_display_probe PRIVATE cxx_std_20)
target_compile_definitions(rsx_mrt_display_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(rsx_mrt_display_probe PRIVATE
    "${SDK}/include" "${SDK}/runtime/platform" "${SDK}/libs/video")
target_link_libraries(rsx_mrt_display_probe PRIVATE d3d12 dxgi d3dcompiler dxguid)
foreach(mode detection composite)
    add_test(NAME rsx_mrt_display_${mode} COMMAND rsx_mrt_display_probe ${mode})
    set_tests_properties(rsx_mrt_display_${mode} PROPERTIES TIMEOUT 20)
endforeach()

# Actual cellAudio mixer with synthetic BE PCM and no device/thread startup.
add_executable(audio_amplitude_probe audio_amplitude_probe.c)
target_include_directories(audio_amplitude_probe PRIVATE
    "${SDK}/include" "${SDK}/runtime/platform" "${SDK}/libs/audio")
target_compile_definitions(audio_amplitude_probe PRIVATE _CRT_SECURE_NO_WARNINGS)
target_compile_options(audio_amplitude_probe PRIVATE ${I2_PROBE_COMPILE_OPTIONS})
target_link_options(audio_amplitude_probe PRIVATE ${I2_PROBE_LINK_OPTIONS})
target_link_libraries(audio_amplitude_probe PRIVATE ole32)
foreach(mode enabled disabled eight-enabled eight-disabled nonfinite-enabled nonfinite-disabled)
    add_test(NAME audio_amplitude_${mode} COMMAND audio_amplitude_probe ${mode}
        "${CMAKE_CURRENT_BINARY_DIR}/audio-amplitude-${mode}.log")
    set_tests_properties(audio_amplitude_${mode} PROPERTIES TIMEOUT 5)
endforeach()

# ---------------------------------------------------------------------------
# Test taxonomy and environment
# ---------------------------------------------------------------------------
# Every fixture here is CPU-only or uses the D3D12 WARP software adapter. The
# categories let CI and local runs select a subset without pretending that an
# excluded test ran:
#   cpu          - no GPU, no game files
#   sdk          - compiles or links the pinned PS3Recomp runtime
#   windows      - native Windows executable
#   gpu-optional - D3D12/WARP, may be excluded on headless runners
#   game-local   - requires local game files (not present in this directory)
get_property(I2_ALL_TESTS DIRECTORY PROPERTY TESTS)
if(I2_ALL_TESTS)
    file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/tmp")
    set_tests_properties(${I2_ALL_TESTS} PROPERTIES
        LABELS "cpu;windows;sdk"
        ENVIRONMENT "TMP=${CMAKE_BINARY_DIR}/tmp;TEMP=${CMAKE_BINARY_DIR}/tmp")
    foreach(I2_TEST IN LISTS I2_ALL_TESTS)
        if(I2_TEST MATCHES "^rsx_guest_texture_|^rsx_mrt_display_")
            set_property(TEST ${I2_TEST} APPEND PROPERTY LABELS "gpu-optional")
            set_property(TEST ${I2_TEST} APPEND PROPERTY RESOURCE_LOCK "d3d12-warp")
        endif()
    endforeach()
    list(LENGTH I2_ALL_TESTS I2_TEST_COUNT)
    message(STATUS "Runner tests:    ${I2_TEST_COUNT} registered (cpu/windows/sdk; gpu-optional marked)")
endif()

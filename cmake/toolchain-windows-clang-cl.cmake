# Toolchain for the Windows x64 clang-cl build used by this project.
#
# The build needs four tools that CMake does not always discover together:
# clang-cl, lld-link, a resource compiler (rc.exe) and mt.exe. The resource
# compiler matters even for a C-only project: CMake's MSVC-like link rule
# embeds a manifest and invokes rc.exe during its compiler ABI check. A bare
# `rc` from PATH is not enough on machines where the Windows SDK bin directory
# is not on PATH, so this file resolves the tools before `project()` runs.
#
# Override any choice with a cache variable, for example:
#   cmake -DCMAKE_C_COMPILER=C:/LLVM/bin/clang-cl.exe ...
# or with an environment variable:
#   I2_CLANG_CL, I2_RC, I2_MT

cmake_minimum_required(VERSION 3.20)

if(NOT WIN32)
    message(FATAL_ERROR "cmake/toolchain-windows-clang-cl.cmake is Windows-only")
endif()

set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR AMD64)

get_filename_component(_i2_root "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)

# ---------------------------------------------------------------------------
# clang-cl
# ---------------------------------------------------------------------------
if(NOT CMAKE_C_COMPILER)
    set(_i2_clang_cl "")
    if(DEFINED ENV{I2_CLANG_CL} AND EXISTS "$ENV{I2_CLANG_CL}")
        set(_i2_clang_cl "$ENV{I2_CLANG_CL}")
    elseif(EXISTS "${_i2_root}/tools/llvm/bin/clang-cl.exe")
        set(_i2_clang_cl "${_i2_root}/tools/llvm/bin/clang-cl.exe")
    else()
        find_program(_i2_clang_cl NAMES clang-cl)
    endif()
    if(NOT _i2_clang_cl)
        message(FATAL_ERROR
            "clang-cl was not found. Put LLVM in tools/llvm, set I2_CLANG_CL, "
            "or pass -DCMAKE_C_COMPILER=<path to clang-cl.exe>.")
    endif()
    file(TO_CMAKE_PATH "${_i2_clang_cl}" _i2_clang_cl)
    set(CMAKE_C_COMPILER "${_i2_clang_cl}" CACHE FILEPATH "C compiler")
    set(CMAKE_CXX_COMPILER "${_i2_clang_cl}" CACHE FILEPATH "C++ compiler")
endif()

if(NOT CMAKE_CXX_COMPILER)
    set(CMAKE_CXX_COMPILER "${CMAKE_C_COMPILER}" CACHE FILEPATH "C++ compiler")
endif()

get_filename_component(_i2_llvm_bin "${CMAKE_C_COMPILER}" DIRECTORY)

# ---------------------------------------------------------------------------
# Linker and manifest tool
# ---------------------------------------------------------------------------
if(NOT CMAKE_LINKER)
    if(EXISTS "${_i2_llvm_bin}/lld-link.exe")
        file(TO_CMAKE_PATH "${_i2_llvm_bin}/lld-link.exe" CMAKE_LINKER)
    else()
        find_program(CMAKE_LINKER NAMES lld-link link)
    endif()
    set(CMAKE_LINKER "${CMAKE_LINKER}" CACHE FILEPATH "Linker")
endif()

if(NOT CMAKE_MT)
    if(EXISTS "${_i2_llvm_bin}/llvm-mt.exe")
        file(TO_CMAKE_PATH "${_i2_llvm_bin}/llvm-mt.exe" CMAKE_MT)
    else()
        find_program(CMAKE_MT NAMES mt)
    endif()
    set(CMAKE_MT "${CMAKE_MT}" CACHE FILEPATH "Manifest tool")
endif()

# ---------------------------------------------------------------------------
# Resource compiler
# ---------------------------------------------------------------------------
# Prefer the Windows SDK rc.exe. llvm-rc is a valid fallback, but on some
# machines its temporary-file handling is blocked by endpoint-security policy.
if(NOT CMAKE_RC_COMPILER)
    set(_i2_rc "")
    if(DEFINED ENV{I2_RC} AND EXISTS "$ENV{I2_RC}")
        set(_i2_rc "$ENV{I2_RC}")
    elseif(DEFINED ENV{WindowsSdkVerBinPath} AND
           EXISTS "$ENV{WindowsSdkVerBinPath}/x64/rc.exe")
        set(_i2_rc "$ENV{WindowsSdkVerBinPath}/x64/rc.exe")
    elseif(DEFINED ENV{WindowsSdkDir} AND
           EXISTS "$ENV{WindowsSdkDir}/bin/${CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION}/x64/rc.exe")
        set(_i2_rc "$ENV{WindowsSdkDir}/bin/${CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION}/x64/rc.exe")
    endif()
    if(NOT _i2_rc)
        file(GLOB _i2_rc_candidates
            "C:/Program Files (x86)/Windows Kits/10/bin/*/x64/rc.exe"
            "$ENV{ProgramFiles}/Windows Kits/10/bin/*/x64/rc.exe")
        if(_i2_rc_candidates)
            list(SORT _i2_rc_candidates COMPARE NATURAL ORDER DESCENDING)
            list(GET _i2_rc_candidates 0 _i2_rc)
        endif()
    endif()
    if(NOT _i2_rc)
        find_program(_i2_rc NAMES rc)
    endif()
    if(NOT _i2_rc AND EXISTS "${_i2_llvm_bin}/llvm-rc.exe")
        set(_i2_rc "${_i2_llvm_bin}/llvm-rc.exe")
    endif()
    if(NOT _i2_rc)
        message(FATAL_ERROR
            "No resource compiler found. Install the Windows SDK or set I2_RC "
            "to rc.exe. CMake needs it for every executable on Windows.")
    endif()
    file(TO_CMAKE_PATH "${_i2_rc}" _i2_rc)
    set(CMAKE_RC_COMPILER "${_i2_rc}" CACHE FILEPATH "Resource compiler")
endif()

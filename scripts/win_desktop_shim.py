"""Windows desktop safety shim.

When running under certain automation runners or sandboxes, the parent process
may specify a non-existent or inaccessible desktop string in STARTUPINFO
(e.g., 'exebox-...'). Windows processes linking USER32.dll attempt to connect
to that desktop during DllMain (UserClientDllInitialize) and fail with
STATUS_DLL_INIT_FAILED (0xC0000142).

This shim ensures child processes launched via subprocess specify
WinSta0\\Default so USER32 initializes cleanly.
"""
from __future__ import annotations

import sys


def apply_desktop_shim() -> None:
    if sys.platform != "win32":
        return
    try:
        import _winapi
        import ctypes
        import ctypes.wintypes as w

        orig_create_process = getattr(_winapi, "CreateProcess", None)
        if not orig_create_process or getattr(_winapi, "_desktop_shim_applied", False):
            return

        class STARTUPINFO_W(ctypes.Structure):
            _fields_ = [
                ("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
                ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
                ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", w.HANDLE),
                ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE),
            ]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD),
            ]

        k32 = ctypes.windll.kernel32
        k32.CreateProcessW.argtypes = [
            w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
            w.BOOL, w.DWORD, ctypes.c_void_p, w.LPCWSTR,
            ctypes.POINTER(STARTUPINFO_W), ctypes.POINTER(PROCESS_INFORMATION)
        ]
        k32.CreateProcessW.restype = w.BOOL

        def patched_create_process(app_name, cmd_line, proc_attrs, thread_attrs,
                                   inherit_handles, creation_flags, env_mapping,
                                   current_dir, startup_info):
            si = STARTUPINFO_W()
            si.cb = ctypes.sizeof(si)
            si.lpDesktop = r"WinSta0\Default"
            if startup_info:
                si.dwFlags = getattr(startup_info, "dwFlags", 0)
                si.wShowWindow = getattr(startup_info, "wShowWindow", 0)
                si.hStdInput = getattr(startup_info, "hStdInput", None)
                si.hStdOutput = getattr(startup_info, "hStdOutput", None)
                si.hStdError = getattr(startup_info, "hStdError", None)

            env_buf = None
            if env_mapping:
                env_lines = [f"{k}={v}" for k, v in env_mapping.items()]
                env_str = "\0".join(env_lines) + "\0\0"
                env_buf = ctypes.create_unicode_buffer(env_str)

            pi = PROCESS_INFORMATION()
            c_flags = creation_flags | 0x00000400  # CREATE_UNICODE_ENVIRONMENT
            cmd_buf = ctypes.create_unicode_buffer(cmd_line) if cmd_line else None

            res = k32.CreateProcessW(
                app_name, cmd_buf, None, None, bool(inherit_handles),
                c_flags, env_buf, current_dir,
                ctypes.byref(si), ctypes.byref(pi)
            )
            if not res:
                err = k32.GetLastError()
                raise OSError(None, f"CreateProcess failed: {err}", None, err)
            return int(pi.hProcess), int(pi.hThread), int(pi.dwProcessId), int(pi.dwThreadId)

        _winapi.CreateProcess = patched_create_process
        _winapi._desktop_shim_applied = True
    except Exception:
        pass

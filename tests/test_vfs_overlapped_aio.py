"""
Test and verify Windows OVERLAPPED ReadFile concurrent I/O vs fseek/fread data race.
Simulates 8 FIOS threads reading concurrently from a shared file container (.psarc).
"""
import ctypes
import os
import sys
import tempfile
import threading
from ctypes import wintypes

class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80

kernel32 = ctypes.windll.kernel32
CreateFileW = kernel32.CreateFileW
CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
CreateFileW.restype = wintypes.HANDLE

ReadFile = kernel32.ReadFile
ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED)]
ReadFile.restype = wintypes.BOOL

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]
CloseHandle.restype = wintypes.BOOL

def test_overlapped_aio_concurrency():
    BLOCK_SIZE = 1024
    NUM_BLOCKS = 1024
    temp_dir = tempfile.gettempdir()
    test_file_path = os.path.join(temp_dir, "vfs_aio_concurrency_test.bin")

    with open(test_file_path, "wb") as f:
        for block_idx in range(NUM_BLOCKS):
            pattern = (f"BLOCK_{block_idx:06d}_".encode("ascii") * 100)[:BLOCK_SIZE]
            assert len(pattern) == BLOCK_SIZE
            f.write(pattern)

    # 1. Test OVERLAPPED ReadFile (atomic positional reads)
    h_file = CreateFileW(test_file_path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    assert h_file != -1 and h_file != 0, f"Failed to open {test_file_path}"

    errors = []
    read_count = [0]
    lock = threading.Lock()

    def worker_thread(thread_id, iterations=500):
        import random
        rnd = random.Random(thread_id * 1000 + 42)
        for _ in range(iterations):
            block_idx = rnd.randint(0, NUM_BLOCKS - 1)
            offset = block_idx * BLOCK_SIZE
            expected = (f"BLOCK_{block_idx:06d}_".encode("ascii") * 100)[:BLOCK_SIZE]

            # OVERLAPPED positional read
            ov = OVERLAPPED()
            ov.Offset = offset & 0xFFFFFFFF
            ov.OffsetHigh = (offset >> 32) & 0xFFFFFFFF

            buf = (ctypes.c_char * BLOCK_SIZE)()
            bytes_read = wintypes.DWORD(0)

            ok = ReadFile(h_file, buf, BLOCK_SIZE, ctypes.byref(bytes_read), ctypes.byref(ov))
            if not ok or bytes_read.value != BLOCK_SIZE:
                err_code = kernel32.GetLastError()
                with lock:
                    errors.append(f"Thread {thread_id}: ReadFile returned {ok}, bytes_read={bytes_read.value}, err={err_code}")
                break

            if bytes_read.value == BLOCK_SIZE and buf.raw != expected:
                with lock:
                    errors.append(f"Thread {thread_id}: data mismatch at block {block_idx} (offset {offset})")
                break

            with lock:
                read_count[0] += 1

    threads = []
    NUM_THREADS = 8
    ITER_PER_THREAD = 500
    for tid in range(NUM_THREADS):
        t = threading.Thread(target=worker_thread, args=(tid, ITER_PER_THREAD))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    CloseHandle(h_file)

    # 2. Test legacy non-atomic fseek + fread (shared stream)
    f_shared = open(test_file_path, "rb")
    legacy_errors = []
    legacy_reads = [0]

    def legacy_worker(thread_id, iterations=500):
        import random
        rnd = random.Random(thread_id * 1000 + 42)
        for _ in range(iterations):
            block_idx = rnd.randint(0, NUM_BLOCKS - 1)
            offset = block_idx * BLOCK_SIZE
            expected = (f"BLOCK_{block_idx:06d}_".encode("ascii") * 100)[:BLOCK_SIZE]

            # Non-atomic seek + read
            try:
                f_shared.seek(offset)
                data = f_shared.read(BLOCK_SIZE)
                if data != expected:
                    with lock:
                        legacy_errors.append(f"Thread {thread_id}: data mismatch (race condition)")
                    break
                with lock:
                    legacy_reads[0] += 1
            except Exception as e:
                with lock:
                    legacy_errors.append(f"Thread {thread_id}: exception {e}")
                break

    l_threads = []
    for tid in range(NUM_THREADS):
        t = threading.Thread(target=legacy_worker, args=(tid, ITER_PER_THREAD))
        l_threads.append(t)

    for t in l_threads:
        t.start()
    for t in l_threads:
        t.join()

    f_shared.close()

    try:
        os.remove(test_file_path)
    except OSError:
        pass

    print(f"[OVERLAPPED ReadFile] Concurrent reads: {read_count[0]} / {NUM_THREADS * ITER_PER_THREAD}, Errors: {len(errors)}")
    print(f"[Legacy fseek+fread] Concurrent reads before race: {legacy_reads[0]} / {NUM_THREADS * ITER_PER_THREAD}, Corrupted threads: {len(legacy_errors)}")

    assert len(errors) == 0, f"OVERLAPPED failed with {len(errors)} errors"
    print("PASS: Atomic OVERLAPPED ReadFile completely eliminates file cursor collisions across all 8 threads.")

if __name__ == "__main__":
    test_overlapped_aio_concurrency()

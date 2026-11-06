"""Windows timing / priority helpers (no-ops on other platforms).

Windows is not real-time; these only reduce jitter while the bus is connected:
timeBeginPeriod(1) raises the system timer resolution, a HIGH process priority class lets the
cyclic thread (THREAD_PRIORITY_HIGHEST) pre-empt normal applications.
"""
from __future__ import annotations

import sys

_IS_WIN = sys.platform == "win32"
if _IS_WIN:
    import ctypes
    from ctypes import wintypes

    _winmm = ctypes.WinDLL("winmm")
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.GetCurrentThread.restype = wintypes.HANDLE
    _k32.GetPriorityClass.argtypes = [wintypes.HANDLE]
    _k32.GetPriorityClass.restype = wintypes.DWORD
    _k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.SetPriorityClass.restype = wintypes.BOOL
    _k32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
    _k32.SetThreadPriority.restype = wintypes.BOOL

HIGH_PRIORITY_CLASS = 0x00000080
THREAD_PRIORITY_ABOVE_NORMAL = 1
THREAD_PRIORITY_HIGHEST = 2


def time_begin_period(ms: int = 1) -> None:
    if _IS_WIN:
        _winmm.timeBeginPeriod(ms)


def time_end_period(ms: int = 1) -> None:
    if _IS_WIN:
        _winmm.timeEndPeriod(ms)


def raise_process_priority() -> int | None:
    """Sets HIGH_PRIORITY_CLASS; returns the previous class (for restore) or None on failure."""
    if not _IS_WIN:
        return None
    h = _k32.GetCurrentProcess()
    prev = _k32.GetPriorityClass(h)
    if prev and _k32.SetPriorityClass(h, HIGH_PRIORITY_CLASS):
        return prev
    return None


def restore_process_priority(prev: int | None) -> None:
    if _IS_WIN and prev:
        _k32.SetPriorityClass(_k32.GetCurrentProcess(), prev)


def set_current_thread_priority(level: int) -> bool:
    """Call from the thread itself (Python threads are native threads)."""
    if not _IS_WIN:
        return False
    return bool(_k32.SetThreadPriority(_k32.GetCurrentThread(), level))

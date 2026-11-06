"""Locating Npcap and importing pysoem.

Npcap installs wpcap.dll / Packet.dll under System32\\Npcap, which is not on the DLL search
path unless "WinPcap API-compatible mode" was chosen at install time. Python 3.8+ on Windows
also ignores PATH for dependent DLLs of extension modules, so the directory must be added with
os.add_dll_directory() *before* pysoem is imported.

The import error usually mentions Npcap, but a missing Microsoft Visual C++ runtime gives the
same "DLL load failed" message (see docs/TROUBLESHOOTING.md).
"""
from __future__ import annotations

import os
import sys
from types import ModuleType

_dll_dir_handle = None
_pysoem: ModuleType | None = None


def npcap_dir() -> str | None:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    d = os.path.join(root, "System32", "Npcap")
    return d if os.path.isdir(d) else None


def add_npcap_dll_directory() -> str | None:
    """Adds System32\\Npcap to the DLL search path (idempotent). Returns the directory or None."""
    global _dll_dir_handle
    if sys.platform != "win32":
        return None
    d = npcap_dir()
    if d and _dll_dir_handle is None:
        _dll_dir_handle = os.add_dll_directory(d)
    return d


def load_pysoem() -> ModuleType:
    """Imports pysoem after preparing the DLL search path; raises ImportError with a hint."""
    global _pysoem
    if _pysoem is not None:
        return _pysoem
    add_npcap_dll_directory()
    try:
        import pysoem  # noqa: PLC0415  (deliberately late)
    except ImportError as e:
        raise ImportError(
            f"無法載入 pysoem：{e}\n"
            "可能原因：未安裝 Npcap、Npcap 版本過舊，或缺少 Microsoft Visual C++ 2015–2022 "
            "Redistributable (x64)。請執行 tools\\check_prerequisites.cmd 檢查。") from e
    _pysoem = pysoem
    return pysoem

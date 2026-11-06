# PyInstaller spec: onedir build (dist\PySoemTool\PySoemTool.exe + _internal\).
# Build:  uv run pyinstaller --noconfirm PySoemTool.spec
# Npcap is NOT bundled (licence); the app adds System32\Npcap to the DLL path at startup.
from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / "launcher.py")],
    pathex=[str(root)],
    datas=[(str(root / "pysoem_tool" / "design" / "tokens.json"), "pysoem_tool/design"),
           (str(root / "docs"), "docs")],
    hiddenimports=["pysoem.pysoem"],
    excludes=["pytest", "PIL"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PySoemTool", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="PySoemTool")

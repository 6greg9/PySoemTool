"""Static check: views stay layout-only, ViewModels stay tk-free.

Usage:
    python tools/check_view_rules.py app/screens [--viewmodels app/viewmodels]

Fails (exit 1) if a view module imports a forbidden layer or does I/O / sleeps, or if a
ViewModel module imports tkinter (directly or through app.mvvm / app.theme).
Adjust the prefix tuples (and APP) to the project's package names.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import sys

APP = "pysoem_tool"
VIEW_FORBIDDEN_IMPORTS = (f"{APP}.model", f"{APP}.services", f"{APP}.comms",
                          "serial", "pymodbus", "socket", "requests", "sqlite3", "sqlmodel")
VIEW_FORBIDDEN_CALLS = {"time.sleep", "open", "threading.Thread", "asyncio.run"}
VM_FORBIDDEN_IMPORTS = ("tkinter", "_tkinter", f"{APP}.mvvm", f"{APP}.theme", "PIL.ImageTk")


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    return ""


def imported_modules(n: ast.AST) -> list[str]:
    """Module names an import statement pulls in. Relative imports are assumed to resolve
    inside the APP package (from ..mvvm import X -> app.mvvm), which is a best effort."""
    if isinstance(n, ast.Import):
        return [a.name for a in n.names]
    if isinstance(n, ast.ImportFrom):
        if n.level == 0:
            return [n.module] if n.module else []
        names = [n.module] if n.module else [a.name for a in n.names]
        return [f"{APP}.{m}" for m in names]
    return []


def check(path: pathlib.Path, forbidden_imports: tuple[str, ...], forbidden_calls: set[str],
          what: str) -> list[str]:
    errs, tree = [], ast.parse(path.read_text(encoding="utf-8"), str(path))
    for n in ast.walk(tree):
        for m in imported_modules(n):
            if m.startswith(forbidden_imports):
                errs.append(f"{path}:{n.lineno}: {what} imports '{m}'")
        if isinstance(n, ast.Call) and dotted(n.func) in forbidden_calls:
            errs.append(f"{path}:{n.lineno}: {what} calls '{dotted(n.func)}'")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("screens", nargs="?", default=f"{APP}/screens")
    ap.add_argument("--viewmodels", help="directory of ViewModel modules that must stay tk-free")
    a = ap.parse_args()

    errors = [e for f in sorted(pathlib.Path(a.screens).rglob("*.py"))
              for e in check(f, VIEW_FORBIDDEN_IMPORTS, VIEW_FORBIDDEN_CALLS, "view")]
    if a.viewmodels:
        errors += [e for f in sorted(pathlib.Path(a.viewmodels).rglob("*.py"))
                   for e in check(f, VM_FORBIDDEN_IMPORTS, set(), "viewmodel")]
    print("\n".join(errors) or "view rules OK")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

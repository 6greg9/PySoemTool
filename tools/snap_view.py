"""Render a screen's preview scenarios to PNG files for visual review.

Usage:
    python tools/snap_view.py app.screens.loadport [--sizes 800x480,1280x800] [--out shots]
Linux/CI (headless):
    xvfb-run -a -s "-screen 0 1920x1080x24" python tools/snap_view.py app.screens.loadport

Contract: the screen module defines
    def preview(master, ui) -> tuple[tk.Widget, dict[str, Callable[[], None]]]
returning the root widget of the screen and named scenarios. Each scenario mutates the
(fake or real) ViewModel; bindings push the change to widgets through `ui`.
preview() is called again for every scenario, so scenarios never inherit each other's state.

Theme: if --tokens exists (default app/design/tokens.json), app.theme.apply_theme runs before
any view is built, exactly as at app startup, and the Theme is stored as `root.theme` so
preview() can pass it to views. Without it, `root.theme` is None.

Requires Pillow. On Linux it grabs through XCB (works under Xvfb); on Windows/macOS it grabs
the real screen, so the window is kept topmost while rendering.
"""
from __future__ import annotations

import argparse
import importlib
import pathlib
import sys
import time
import tkinter as tk

from PIL import ImageGrab

if sys.platform == "win32":
    import ctypes
    try:
        # Physical pixels, so winfo_* coordinates match what ImageGrab captures at 125-200%
        # display scaling. Must happen before the first Tk() is created.
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pysoem_tool.mvvm import UiDispatcher


def settle(root: tk.Tk, ms: int = 150) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:          # let the dispatcher pump and geometry settle
        root.update()
        time.sleep(0.01)


def clipped(w: tk.Misc) -> list[str]:
    """Mapped widgets narrower than they asked to be (text cut off, controls squeezed)."""
    out = []
    for c in w.winfo_children():
        if c.winfo_ismapped() and c.winfo_reqwidth() > c.winfo_width() + 1:
            out.append(str(c))
        out += clipped(c)
    return out


def grab(root: tk.Tk, path: pathlib.Path) -> None:
    root.lift()
    root.update_idletasks()
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)


def new_root(size: str, tokens: str) -> tk.Tk:
    root = tk.Tk()
    root.geometry(f"{size}+0+0")
    root.attributes("-topmost", True)      # nothing may cover the window while it is grabbed
    root.theme = None
    if tokens and pathlib.Path(tokens).exists():
        from pysoem_tool.theme import apply_theme
        root.theme = apply_theme(root, tokens)
    return root


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("--sizes", default="1024x768")
    ap.add_argument("--out", default="shots")
    ap.add_argument("--only", help="comma-separated scenario names")
    ap.add_argument("--tokens", default="pysoem_tool/design/tokens.json",
                    help="design tokens applied before building views; '' to skip")
    a = ap.parse_args()

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    mod = importlib.import_module(a.module)
    stem = a.module.rsplit(".", 1)[-1]
    report = []
    for size in a.sizes.split(","):
        root = new_root(size, a.tokens)
        ui = UiDispatcher(root)
        probe, scenarios = mod.preview(root, ui)
        probe.destroy()
        names = a.only.split(",") if a.only else list(scenarios)
        for name in names:
            widget, scenarios = mod.preview(root, ui)   # fresh view + VM per scenario
            widget.pack(fill="both", expand=True)
            settle(root)
            scenarios[name]()
            settle(root)
            p = out / f"{stem}__{name}__{size}.png"
            grab(root, p)
            cut = clipped(widget)
            report.append(f"{p}" + (f"  WARN clipped: {cut}" if cut else ""))
            widget.destroy()
        ui.close()
        root.destroy()
    themed = "themed" if a.tokens and pathlib.Path(a.tokens).exists() else "no theme"
    print(f"({themed}, Tk {tk.TkVersion})")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

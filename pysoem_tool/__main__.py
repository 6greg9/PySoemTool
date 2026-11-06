"""Entry point: python -m pysoem_tool

Startup order: DPI awareness -> Npcap DLL directory (before pysoem is imported anywhere) ->
Tk root + theme -> dispatcher -> view model -> views. Unhandled exceptions are written to
<program folder>/error.log and shown in one dialog at a time (handoff §6.23).
"""
from __future__ import annotations

import sys
import threading
import traceback
from datetime import datetime


def main(argv: list[str] | None = None) -> int:
    """``--smoke``: start the GUI, attach the simulated axis, enable it, log the result to
    tool-yyyyMMdd.log and exit (deployment check; exit code 0 = OK)."""
    argv = sys.argv[1:] if argv is None else argv
    smoke = "--smoke" in argv
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass

    from .npcap import add_npcap_dll_directory
    add_npcap_dll_directory()

    import tkinter as tk
    from tkinter import messagebox

    from .mvvm import UiDispatcher
    from .screens.main_window import MainView, bind_main
    from .theme import apply_theme
    from .viewmodels.base import app_dir
    from .viewmodels.main_vm import MainViewModel

    root = tk.Tk()
    root.title("PySoemTool — EtherCAT 測試工具")
    root.geometry("1280x800")
    root.minsize(1024, 700)
    from pathlib import Path
    theme = apply_theme(root, Path(__file__).with_name("design") / "tokens.json")
    root.theme = theme
    ui = UiDispatcher(root)

    error_log = app_dir() / "error.log"
    showing = {"v": False}

    def report(exc_type, exc, tb, where: str) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            with open(error_log, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} [{where}]\n{text}\n")
        except OSError:
            pass

        def show():
            # timers keep firing while a dialog is open; show only one at a time
            if showing["v"]:
                return
            showing["v"] = True
            try:
                messagebox.showerror("未處理的例外", f"{exc}\n\n詳細內容：{error_log}", parent=root)
            finally:
                showing["v"] = False
        ui.post(show)

    root.report_callback_exception = lambda t, e, tb: report(t, e, tb, "tk")
    sys.excepthook = lambda t, e, tb: report(t, e, tb, "main")
    threading.excepthook = lambda a: report(a.exc_type, a.exc_value, a.exc_traceback,
                                            a.thread.name if a.thread else "thread")

    vm = MainViewModel()
    view = MainView(root, theme)
    view.pack(fill="both", expand=True)
    bind_main(view, vm, ui)
    vm.start()

    def on_close():
        try:
            vm.shutdown()          # axis, cyclic thread, master, workers
        finally:
            ui.close()
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    result = {"rc": 0}
    if smoke:
        def smoke_step(n=0):
            ax = vm.axis
            if n == 0:
                ax.attach()
                ax.cmd_enable()
            st = ax.state_text.value
            if st == "Operation enabled" or n > 40:
                ok = st == "Operation enabled"
                import pysoem_tool.npcap as npc
                try:
                    npc.load_pysoem()
                    ps_ok = "pysoem OK"
                except ImportError as e:
                    ps_ok = f"pysoem FAIL: {e}"
                    ok = False
                vm.log(f"[smoke] 模擬軸 {st}；{ps_ok}；結果 {'OK' if ok else 'FAIL'}")
                result["rc"] = 0 if ok else 1
                on_close()
                return
            root.after(100, smoke_step, n + 1)
        root.after(1000, smoke_step)
    root.mainloop()
    return result["rc"]


if __name__ == "__main__":
    sys.exit(main())

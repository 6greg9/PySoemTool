"""Extra binders for this tool (on top of pysoem_tool.mvvm). Binders are the only code that
touches both widgets and observables; every widget update goes through the UiDispatcher."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Sequence

from ..mvvm import Command, Observable, UiDispatcher


def bind_text_latest(w: tk.Misc, obs: Observable, ui: UiDispatcher, fmt: Callable[[Any], str] = str):
    """Like bind_text but coalesced (for values updated every 50 ms)."""
    return obs.subscribe(lambda v: ui.post_latest((id(w), "text"), lambda t=fmt(v): w.configure(text=t)))


def bind_style_latest(w: ttk.Widget, obs: Observable, ui: UiDispatcher, style_for: Callable[[Any], str]):
    return obs.subscribe(lambda v: ui.post_latest((id(w), "style"), lambda s=style_for(v): w.configure(style=s)))


def bind_combobox(cmb: ttk.Combobox, items: Observable[Sequence], selected: Observable, ui: UiDispatcher):
    """Readonly combobox over objects; the text is str(item) (handoff §6.26)."""
    cmb.configure(state="readonly")

    def refresh(_=None):
        its = list(items.value)
        cmb.configure(values=[str(x) for x in its])
        sel = selected.value
        idx = next((i for i, x in enumerate(its) if x == sel), -1)
        if idx >= 0:
            cmb.current(idx)
        else:
            cmb.set("")

    def on_pick(_e):
        i = cmb.current()
        its = list(items.value)
        if 0 <= i < len(its):
            selected.value = its[i]

    cmb.bind("<<ComboboxSelected>>", on_pick, add="+")
    d1 = items.subscribe(lambda _: ui.post(refresh))
    d2 = selected.subscribe(lambda _: ui.post(refresh), immediate=False)

    def dispose():
        d1()
        d2()
    return dispose


def bind_check(chk: ttk.Checkbutton, obs: Observable[bool], ui: UiDispatcher):
    var = tk.BooleanVar(chk, obs.value)
    chk.configure(variable=var)
    from_view = False

    def on_view(*_):
        nonlocal from_view
        from_view = True
        try:
            obs.value = bool(var.get())
        finally:
            from_view = False
    tid = var.trace_add("write", on_view)
    d = obs.subscribe(lambda v: None if from_view else ui.post(var.set, v), immediate=False)

    def dispose():
        d()
        var.trace_remove("write", tid)
    return dispose


def bind_lines(lb: tk.Listbox, obs: Observable[Sequence[str]], ui: UiDispatcher):
    def show(lines):
        lb.delete(0, "end")
        for ln in lines:
            lb.insert("end", ln)
    return obs.subscribe(lambda v: ui.post(show, tuple(v)))


def bind_enabled_any(widgets: Sequence[tk.Misc], obs: Observable[bool], ui: UiDispatcher):
    def apply(ok):
        for w in widgets:
            try:
                w.state(["!disabled"] if ok else ["disabled"])
            except AttributeError:
                w.configure(state="normal" if ok else "disabled")
    return obs.subscribe(lambda ok: ui.post(apply, ok))


def bind_repeat(btn: ttk.Button, cmd: Command, ui: UiDispatcher, interval_ms: int = 100):
    """Press-and-hold button (WPF RepeatButton): runs ``cmd`` now and every ``interval_ms`` while
    held. Release, leaving the button, focus loss or disabling stops the repeats; the controller's
    dead-man timer then stops the axis."""
    state = {"id": None}

    def tick():
        if not cmd.can_execute.value or "disabled" in btn.state():
            stop()
            return
        cmd()
        state["id"] = btn.after(interval_ms, tick)

    def start(_e=None):
        stop()
        tick()

    def stop(_e=None):
        if state["id"] is not None:
            try:
                btn.after_cancel(state["id"])
            except tk.TclError:
                pass
            state["id"] = None

    btn.bind("<ButtonPress-1>", start, add="+")
    for ev in ("<ButtonRelease-1>", "<Leave>", "<FocusOut>", "<Destroy>"):
        btn.bind(ev, stop, add="+")
    btn.winfo_toplevel().bind("<FocusOut>", stop, add="+")
    from ..mvvm import bind_enabled
    return bind_enabled(btn, cmd.can_execute, ui)

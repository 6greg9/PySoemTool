"""tkinter side of MVVM: UiDispatcher, Bindings bag and binders.

Copy to app/mvvm.py next to app/observable.py. Views, binders and screen modules import from
here (Observable, Command and computed are re-exported for them); ViewModels import from
app.observable only.
"""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Hashable

from .observable import Command, Observable, computed

__all__ = [
    "Observable", "Command", "computed", "UiDispatcher", "Bindings",
    "bind_text", "bind_enabled", "bind_entry", "bind_grid_visible", "bind_command", "bind_style",
]

log = logging.getLogger(__name__)

# TclError messages that only mean "the widget was destroyed before the callback ran".
_DESTROYED = ("invalid command name", "bad window path name")


class UiDispatcher:
    """Runs callables on the Tk thread: a queue drained by an `after` pump.

    Create it on the Tk thread. `post` / `post_latest` are safe from any thread.
    `post_latest` coalesces high-frequency updates, so a 10 ms PLC poll doesn't flood the UI.
    Within one pump, coalesced calls run before queued `post` calls.
    """

    def __init__(self, root: tk.Misc, interval_ms: int = 16, max_batch: int = 500):
        self._root = root
        self._q: "queue.SimpleQueue[tuple[Callable, tuple, dict]]" = queue.SimpleQueue()
        self._latest: dict[Hashable, tuple[Callable, tuple]] = {}
        self._latest_lock = threading.Lock()
        self._interval = interval_ms
        self._max_batch = max_batch
        self._after_id: str | None = self._root.after(self._interval, self._pump)

    def close(self) -> None:
        """Call on the Tk thread before root.destroy(), or Tk logs 'invalid command name ..._pump'."""
        if self._after_id is not None:
            self._root.after_cancel(self._after_id)
            self._after_id = None

    def post(self, fn: Callable, *args: Any, **kwargs: Any) -> None:
        """Safe from any thread. FIFO."""
        self._q.put((fn, args, kwargs))

    def post_latest(self, key: Hashable, fn: Callable, *args: Any) -> None:
        """Safe from any thread. Only the newest call per key survives until the next pump."""
        with self._latest_lock:
            self._latest[key] = (fn, args)

    def _pump(self) -> None:
        with self._latest_lock:
            latest, self._latest = self._latest, {}
        for fn, args in latest.values():
            self._run(fn, args, {})
        for _ in range(self._max_batch):
            try:
                fn, args, kwargs = self._q.get_nowait()
            except queue.Empty:
                break
            self._run(fn, args, kwargs)
        if self._after_id is not None:          # close() not called meanwhile
            self._after_id = self._root.after(self._interval, self._pump)

    @staticmethod
    def _run(fn: Callable, args: tuple, kwargs: dict) -> None:
        # Never let one callback raise out of the pump: the pump would stop rescheduling and
        # every later UI update would silently disappear.
        try:
            fn(*args, **kwargs)
        except tk.TclError as e:
            if not str(e).startswith(_DESTROYED):   # destroyed between post and pump: expected
                log.exception("UI callback %r failed", fn)
        except Exception:
            log.exception("UI callback %r failed", fn)


class Bindings:
    """Collects disposers and runs them when `owner` is destroyed."""

    def __init__(self, owner: tk.Misc):
        self._disposers: list[Callable[[], None]] = []
        self._owner = owner
        owner.bind("<Destroy>", self._on_destroy, add="+")

    def add(self, disposer: Callable[[], None]) -> None:
        self._disposers.append(disposer)

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self._owner:      # <Destroy> also fires for every child
            for d in self._disposers:
                d()
            self._disposers.clear()


# All binders take the UiDispatcher, so they stay correct when the VM is updated from a worker
# thread. Each returns a disposer; add it to the view's Bindings bag.

def bind_text(w: tk.Misc, obs: Observable, ui: UiDispatcher, fmt: Callable[[Any], str] = str):
    return obs.subscribe(lambda v: ui.post(w.configure, text=fmt(v)))


def bind_enabled(w: ttk.Widget, obs: Observable[bool], ui: UiDispatcher):
    return obs.subscribe(
        lambda ok: ui.post(w.state, ["!disabled"] if ok else ["disabled"]))


def bind_style(w: ttk.Widget, obs: Observable, ui: UiDispatcher, style_for: Callable[[Any], str]):
    """State -> ttk style name, e.g. style_for=lambda s: E90_STATE_STYLE.get(s, "StateIdle.TLabel")."""
    return obs.subscribe(lambda v: ui.post(w.configure, style=style_for(v)))


def bind_entry(entry: ttk.Entry, obs: Observable[str], ui: UiDispatcher):
    """Two-way. View -> VM on the Tk thread; VM -> view marshalled via the dispatcher."""
    var = tk.StringVar(entry, obs.value)
    entry.configure(textvariable=var)
    from_view = False

    def on_view_write(*_):
        nonlocal from_view
        from_view = True                 # notify is synchronous on this (Tk) thread
        try:
            obs.value = var.get()
        finally:
            from_view = False

    def on_vm_change(v):
        if from_view:
            return                       # don't echo the user's own keystroke back:
        ui.post(var.set, v)              # a deferred echo would overwrite newer typing

    tid = var.trace_add("write", on_view_write)
    d = obs.subscribe(on_vm_change, immediate=False)

    def dispose():
        d()
        var.trace_remove("write", tid)
    return dispose


def bind_grid_visible(w: tk.Widget, obs: Observable[bool], ui: UiDispatcher):
    """Widget must already be gridded once so grid_remove() remembers its options."""
    return obs.subscribe(lambda show: ui.post(w.grid if show else w.grid_remove))


def bind_command(btn: ttk.Button, cmd: Command, ui: UiDispatcher):
    btn.configure(command=cmd)
    return bind_enabled(btn, cmd.can_execute, ui)

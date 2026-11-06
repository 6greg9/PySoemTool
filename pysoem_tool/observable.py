"""tk-free MVVM core: Observable, Command, computed.

Copy to app/observable.py. ViewModels import from here and never from tkinter or app.mvvm, so
they stay unit-testable without a display and reusable under another toolkit (e.g. PySide6).
"""
from __future__ import annotations

import threading
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class Observable(Generic[T]):
    """Thread-safe value holder. Notifies subscribers on the thread that set the value."""

    def __init__(self, value: T):
        self._value = value
        self._subs: list[Callable[[T], None]] = []
        self._lock = threading.Lock()

    @property
    def value(self) -> T:
        return self._value

    @value.setter
    def value(self, new: T) -> None:
        with self._lock:
            if new == self._value:
                return                      # equality guard also breaks two-way binding loops
            self._value = new
            subs = list(self._subs)
        for cb in subs:
            cb(new)

    def subscribe(self, cb: Callable[[T], None], immediate: bool = True) -> Callable[[], None]:
        with self._lock:
            self._subs.append(cb)
        if immediate:
            cb(self._value)

        def dispose() -> None:
            with self._lock:
                if cb in self._subs:
                    self._subs.remove(cb)
        return dispose


class Command:
    def __init__(self, execute: Callable[[], None], can_execute: Observable[bool] | None = None):
        self.execute = execute
        self.can_execute = can_execute or Observable(True)

    def __call__(self) -> None:
        if self.can_execute.value:
            self.execute()


def computed(fn: Callable[..., T], *sources: Observable) -> Observable[T]:
    """Derived observable, recomputed whenever any source changes.

    The subscriptions live as long as the sources; create computed values in the VM, whose
    lifetime matches its sources, not per view.
    """
    out: Observable[T] = Observable(fn(*(s.value for s in sources)))

    def recompute(_=None) -> None:
        out.value = fn(*(s.value for s in sources))
    for s in sources:
        s.subscribe(recompute, immediate=False)
    return out

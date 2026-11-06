"""tk-free helpers for ViewModels: background worker, periodic ticker, log sink, app folder."""
from __future__ import annotations

import concurrent.futures as cf
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..observable import Observable

log = logging.getLogger(__name__)


def app_dir() -> Path:
    """Program folder: next to the .exe when frozen (PyInstaller), else the project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


class Worker:
    """Runs blocking calls (pysoem, SDO, state changes) off the Tk thread.

    ``on_done`` / ``on_error`` run on the worker thread; they may only set Observables or post.
    """

    def __init__(self, max_workers: int = 4) -> None:
        self._pool = cf.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="worker")

    def run(self, fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
            on_error: Callable[[BaseException], None] | None = None) -> cf.Future:
        def job():
            try:
                r = fn()
            except BaseException as e:  # noqa: BLE001 - reported to the user
                if on_error:
                    on_error(e)
                else:
                    log.exception("background job failed")
                return None
            if on_done:
                on_done(r)
            return r
        return self._pool.submit(job)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


class Ticker:
    """Calls ``fn`` every ``period_s`` on its own daemon thread while started."""

    def __init__(self, period_s: float, fn: Callable[[], None], name: str) -> None:
        self._period = period_s
        self._fn = fn
        self._name = name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t and t is not threading.current_thread():
            t.join(1.0)

    def _run(self) -> None:
        nxt = time.monotonic()
        while not self._stop.is_set():
            try:
                self._fn()
            except Exception:  # noqa: BLE001
                log.exception("ticker %s failed", self._name)
            nxt += self._period
            self._stop.wait(max(0.0, nxt - time.monotonic()))


class LogSink:
    """Log lines for the screen plus the full log in <program folder>/tool-yyyyMMdd.log.

    Subscribers get each new line (on the calling thread); the view keeps the last 500.
    """
    SCREEN_LINES = 500

    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder or app_dir()
        self._subs: list[Callable[[str], None]] = []
        self._lock = threading.Lock()
        self.last_line: Observable[str] = Observable("")

    @property
    def path(self) -> Path:
        return self.folder / f"tool-{datetime.now():%Y%m%d}.log"

    def subscribe(self, cb: Callable[[str], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(cb)

        def dispose():
            with self._lock:
                if cb in self._subs:
                    self._subs.remove(cb)
        return dispose

    def __call__(self, message: str) -> None:
        line = f"{datetime.now():%H:%M:%S.%f}"[:-3] + f"  {message}"
        with self._lock:
            subs = list(self._subs)
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass                      # logging must never break the tool
        for cb in subs:
            cb(line)
        self.last_line.value = line

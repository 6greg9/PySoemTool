import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pysoem_tool.motion import cia402 as c  # noqa: E402
from pysoem_tool.motion.channel import SimulatedAxisChannel  # noqa: E402
from pysoem_tool.motion.cia402 import DriveState, OperationMode  # noqa: E402
from pysoem_tool.motion.controller import AxisController  # noqa: E402
from pysoem_tool.motion.sim_drive import SimulatedCia402Drive  # noqa: E402

DT = 0.001


class Rig:
    """Headless test rig: controller + simulated drive stepped at a fixed 1 ms."""

    def __init__(self, cycle_s: float = DT):
        self.drive = SimulatedCia402Drive()
        self.ch = SimulatedAxisChannel(self.drive)
        self.ctl = AxisController(self.ch, cycle_s)
        self.cycle_s = cycle_s
        self.history: list = []

    @property
    def st(self):
        return self.ctl.status

    def run(self, cycles: int, comm_ok: bool = True, measured_dt=None, record=False):
        for i in range(cycles):
            mdt = measured_dt(i) if callable(measured_dt) else (measured_dt or self.cycle_s)
            self.ctl.on_cycle([], [], mdt, comm_ok)
            if record:
                self.history.append(self.ctl.status)

    def run_until(self, cond, max_cycles=20000, **kw):
        for n in range(max_cycles):
            if cond(self.st):
                return n
            self.run(1, **kw)
        raise AssertionError(f"condition not met within {max_cycles} cycles; status={self.st}")

    def enable(self):
        self.ctl.enable()
        self.run_until(lambda s: s.state == DriveState.OPERATION_ENABLED, 100)

    def switch_mode(self, mode: OperationMode):
        self.ctl.set_mode(mode)
        self.run_until(lambda s: s.mode_display == mode, 100)
        self.run(2)


@pytest.fixture
def rig():
    return Rig()


__all__ = ["Rig", "c", "DriveState", "OperationMode", "DT"]

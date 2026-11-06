"""The 28 headless simulation checks ported from SoemWpfTool: state machine, PP/PV/HM/CSP/CSV,
jog dead-man, fault handling, irregular dt and trajectory properties.
Numbers in the test names match the list in docs/ARCHITECTURE.md."""
import math
import random

import pytest

from conftest import DT, DriveState, OperationMode, Rig, c
from pysoem_tool.motion.controller import AxisError, COMM_LOSS_CYCLES, JOG_WATCHDOG_SECONDS
from pysoem_tool.motion.trajectory import TrajectoryGenerator

DS, OM = DriveState, OperationMode


# ---------------------------------------------------------------- state machine

def test_01_decode_statusword():
    assert c.decode(0x0000) == DS.NOT_READY_TO_SWITCH_ON
    assert c.decode(0x0250) == DS.SWITCH_ON_DISABLED
    assert c.decode(0x0231) == DS.READY_TO_SWITCH_ON
    assert c.decode(0x0233) == DS.SWITCHED_ON
    assert c.decode(0x0237) == DS.OPERATION_ENABLED
    assert c.decode(0x0217) == DS.QUICK_STOP_ACTIVE
    assert c.decode(0x021F) == DS.FAULT_REACTION_ACTIVE
    assert c.decode(0x0218) == DS.FAULT


def test_02_initial_state_switch_on_disabled(rig):
    rig.run(5)
    assert rig.st.state == DS.SWITCH_ON_DISABLED
    assert rig.st.controlword == c.CMD_DISABLE_VOLTAGE
    assert not rig.st.enable_requested


def test_03_enable_walks_state_machine(rig):
    rig.ctl.enable()
    seen = []
    for _ in range(20):
        rig.run(1)
        if not seen or seen[-1] != rig.st.state:
            seen.append(rig.st.state)
    # first cycle reads the state before the drive has seen Shutdown
    assert seen == [DS.SWITCH_ON_DISABLED, DS.READY_TO_SWITCH_ON, DS.SWITCHED_ON, DS.OPERATION_ENABLED]
    # PP mode (default) adds bit 5 "change set immediately" on top of Enable operation
    assert rig.st.controlword & 0x8F == c.CMD_ENABLE_OPERATION


def test_04_entering_op_targets_equal_actual(rig):
    # Drive somewhere first, disable, then re-enable in CSP: target must equal actual (no jump).
    rig.enable()
    rig.switch_mode(OM.CYCLIC_SYNC_VELOCITY)
    rig.ctl.run_velocity(5000, 50000, 50000)
    rig.run(300)
    rig.ctl.stop()
    rig.run_until(lambda s: not s.is_moving)
    rig.ctl.disable()
    rig.run(10)
    rig.ctl.set_mode(OM.CYCLIC_SYNC_POSITION)
    rig.ctl.enable()
    rig.run_until(lambda s: s.state == DS.OPERATION_ENABLED, 100)
    assert rig.st.target_position == rig.st.position_actual
    rig.run(5)
    assert rig.drive.state == DS.OPERATION_ENABLED


def test_05_disable_goes_to_ready(rig):
    rig.enable()
    rig.ctl.disable()
    rig.run(5)
    assert rig.st.state == DS.READY_TO_SWITCH_ON
    assert rig.st.controlword == c.CMD_SHUTDOWN


def test_06_quick_stop_and_reenable(rig):
    rig.enable()
    rig.switch_mode(OM.PROFILE_VELOCITY)
    rig.ctl.run_velocity(20000, 100000, 100000)
    rig.run(300)
    rig.ctl.quick_stop()
    rig.run(3)
    assert rig.st.state == DS.QUICK_STOP_ACTIVE
    rig.run_until(lambda s: s.velocity_actual == 0, 2000)
    rig.ctl.enable()
    rig.run_until(lambda s: s.state == DS.OPERATION_ENABLED, 100)


# ---------------------------------------------------------------- faults

def test_07_injected_fault_clears_enable(rig):
    rig.enable()
    rig.drive.inject_fault(0x2310)
    rig.run_until(lambda s: s.state == DS.FAULT, 50)
    assert rig.st.error_code == 0x2310
    assert not rig.st.enable_requested


def test_08_fault_reset_rising_edge_needs_reenable(rig):
    rig.enable()
    rig.drive.inject_fault(0x2310)
    rig.run_until(lambda s: s.state == DS.FAULT, 50)
    rig.ctl.fault_reset()
    cws = []
    for _ in range(20):
        rig.run(1)
        cws.append(rig.st.controlword & c.CW_FAULT_RESET)
    assert cws[:10] == [0] * 10 and all(cws[10:])         # low 10, then high 10
    rig.run(5)
    assert rig.st.state == DS.SWITCH_ON_DISABLED          # not automatically re-enabled
    assert rig.st.error_code == 0
    rig.enable()


def test_09_motion_waits_for_mode_display(rig):
    rig.enable()
    rig.ctl.set_mode(OM.CYCLIC_SYNC_POSITION)
    # before any cycle the drive still shows the old mode -> command refused
    with pytest.raises(AxisError):
        rig.ctl.move(1000, False, 1000, 10000, 10000)
    rig.run_until(lambda s: s.mode_display == OM.CYCLIC_SYNC_POSITION, 50)
    rig.ctl.move(1000, False, 1000, 10000, 10000)


def test_10_set_mode_refused_while_moving(rig):
    rig.enable()
    rig.switch_mode(OM.PROFILE_VELOCITY)
    rig.ctl.run_velocity(5000, 50000, 50000)
    rig.run(100)
    with pytest.raises(AxisError):
        rig.ctl.set_mode(OM.PROFILE_POSITION)


# ---------------------------------------------------------------- PP / PV / HM

def test_11_pp_absolute_handshake(rig):
    rig.enable()
    rig.switch_mode(OM.PROFILE_POSITION)
    rig.ctl.move(10000, False, 10000, 50000, 50000)
    saw_bit4 = saw_ack = False
    for _ in range(3000):
        rig.run(1)
        saw_bit4 |= bool(rig.st.controlword & c.CW_NEW_SET_POINT)
        saw_ack |= bool(rig.st.statusword & c.SW_OP_MODE_SPECIFIC_12)
        if saw_ack and not rig.st.is_moving:
            break
    assert saw_bit4 and saw_ack
    assert not rig.st.controlword & c.CW_NEW_SET_POINT       # bit 4 released after ack
    assert rig.st.position_actual == 10000
    assert rig.st.statusword & c.SW_TARGET_REACHED


def test_12_pp_relative(rig):
    rig.enable()
    rig.switch_mode(OM.PROFILE_POSITION)
    rig.ctl.move(5000, False, 20000, 100000, 100000)
    rig.run(5)
    rig.run_until(lambda s: not s.is_moving, 3000)
    rig.ctl.move(-2000, True, 20000, 100000, 100000)
    rig.run(5)
    assert rig.st.controlword & c.CW_RELATIVE
    rig.run_until(lambda s: not s.is_moving, 3000)
    assert rig.st.position_actual == 3000


def test_13_pp_halt_stops_short(rig):
    rig.enable()
    rig.switch_mode(OM.PROFILE_POSITION)
    rig.ctl.move(100000, False, 10000, 50000, 50000)
    rig.run(500)
    rig.ctl.stop()
    rig.run(3)
    assert rig.st.controlword & c.CW_HALT
    rig.run_until(lambda s: s.velocity_actual == 0, 3000)
    assert 0 < rig.st.position_actual < 100000


def test_14_pv_runs_and_halts(rig):
    rig.enable()
    rig.switch_mode(OM.PROFILE_VELOCITY)
    rig.ctl.run_velocity(5000, 50000, 50000)
    rig.run(300)
    assert rig.st.velocity_actual == 5000
    rig.ctl.stop()
    rig.run(2)
    assert rig.st.controlword & c.CW_HALT
    rig.run_until(lambda s: s.velocity_actual == 0, 1000)


def test_15_homing_method_35(rig):
    rig.enable()
    rig.switch_mode(OM.HOMING)
    rig.ch.write_parameter(c.HOMING_METHOD, 0, 35, 1)
    rig.ch.write_parameter(c.HOME_OFFSET, 0, 123, 4)
    rig.ctl.start_homing()
    rig.run_until(lambda s: s.activity == "原點復歸完成", 500)
    assert rig.st.position_actual == 123


def test_16_homing_holds_bit4_50ms(rig):
    # Search method: completes after 20 000 units; previous "attained" must not end it early.
    rig.enable()
    rig.switch_mode(OM.HOMING)
    rig.ch.write_parameter(c.HOMING_METHOD, 0, 35, 1)
    rig.ctl.start_homing()
    rig.run_until(lambda s: s.activity == "原點復歸完成", 500)   # attained is now set
    rig.ch.write_parameter(c.HOMING_METHOD, 0, 2, 1)
    rig.ctl.start_homing()
    held = 0
    while True:
        rig.run(1)
        if rig.st.controlword & c.CW_NEW_SET_POINT:
            held += 1
        if rig.st.activity == "原點復歸完成":
            break
        assert held < 20000
    assert held * DT >= 0.05
    assert rig.st.position_actual == 0          # home offset 0
    assert rig.drive.read_object(c.STATUSWORD, 0) & c.SW_OP_MODE_SPECIFIC_12


def test_17_homing_invalid_method_reports_error(rig):
    rig.enable()
    rig.switch_mode(OM.HOMING)
    rig.ch.write_parameter(c.HOMING_METHOD, 0, 99, 1)
    rig.ctl.start_homing()
    rig.run_until(lambda s: s.activity == "原點復歸錯誤", 500)
    assert rig.st.statusword & c.SW_OP_MODE_SPECIFIC_13


# ---------------------------------------------------------------- CSP / CSV

def test_18_csp_reaches_target(rig):
    rig.enable()
    rig.switch_mode(OM.CYCLIC_SYNC_POSITION)
    rig.ctl.move(25000, False, 20000, 100000, 100000)
    rig.run(2)
    rig.run_until(lambda s: not s.is_moving, 5000)
    assert rig.st.position_actual == 25000
    assert rig.st.state == DS.OPERATION_ENABLED


def test_19_csp_no_overshoot_and_acc_limit(rig):
    rig.enable()
    rig.switch_mode(OM.CYCLIC_SYNC_POSITION)
    acc = 100000
    rig.ctl.move(30000, False, 20000, acc, acc)
    rig.run(2000, record=True)
    pos = [s.target_position for s in rig.history]
    assert max(pos) <= 30000
    vel = [(b - a) / DT for a, b in zip(pos, pos[1:])]
    accs = [abs(b - a) / DT for a, b in zip(vel, vel[1:])]
    # integer rounding of the target adds at most ~2 counts / dt^2 of apparent acceleration
    assert max(accs) <= 2 * acc + 2 / DT ** 2


def test_20_csv_ramp_and_stop(rig):
    rig.enable()
    rig.switch_mode(OM.CYCLIC_SYNC_VELOCITY)
    rig.ctl.run_velocity(8000, 40000, 40000)
    rig.run(100)
    assert 3000 < rig.st.target_velocity < 5000            # 0.1 s * 40000 = 4000
    rig.run(200)
    assert rig.st.target_velocity == 8000
    rig.ctl.stop()
    rig.run_until(lambda s: s.target_velocity == 0, 1000)


# ---------------------------------------------------------------- jog dead-man

@pytest.mark.parametrize("mode", [OM.CYCLIC_SYNC_POSITION, OM.PROFILE_VELOCITY])
def test_21_22_jog_dead_man_stops(rig, mode):
    rig.enable()
    rig.switch_mode(mode)
    rig.ctl.jog(5000, 100000)
    n_cycles = int(JOG_WATCHDOG_SECONDS / DT)
    rig.run(n_cycles - 10)
    assert rig.st.is_moving
    rig.run(400)                                           # no refresh -> stop
    assert rig.st.velocity_actual == 0
    assert not rig.st.is_moving


def test_23_jog_refreshed_keeps_moving(rig):
    rig.enable()
    rig.switch_mode(OM.CYCLIC_SYNC_VELOCITY)
    for _ in range(15):                                     # refresh every 100 ms for 1.5 s
        rig.ctl.jog(-3000, 100000)
        rig.run(100)
    assert rig.st.target_velocity == -3000


# ---------------------------------------------------------------- communication / dt

def test_24_comm_loss_holds_actual_position(rig):
    rig.enable()
    rig.switch_mode(OM.CYCLIC_SYNC_VELOCITY)
    rig.ctl.run_velocity(5000, 100000, 100000)
    rig.run(200)
    rig.run(COMM_LOSS_CYCLES - 1, comm_ok=False)
    assert rig.st.communication_ok                          # 19 bad cycles still tolerated
    rig.run(1, comm_ok=False)
    assert not rig.st.communication_ok
    assert rig.st.target_velocity == 0
    assert rig.st.target_position == rig.st.position_actual
    assert "通訊中斷" in rig.st.activity


def test_25_irregular_dt_does_not_cause_following_error():
    # Regression (handoff §6.10): Windows back-to-back catch-up cycles (0.05 ms after a 2 ms
    # one) must not turn the 1-count rounding into a huge velocity. The controller advances
    # CSP by the nominal period and ignores the measured dt.
    r = Rig()
    r.drive.max_velocity = 30000          # tight limit: 1.5x = 45000 counts/s
    rnd = random.Random(1)
    jitter = lambda i: rnd.choice([0.00005, 0.001, 0.002, 0.0003, 0.0017])
    r.enable()
    r.switch_mode(OM.CYCLIC_SYNC_POSITION)
    r.ctl.move(50000, False, 25000, 200000, 200000)
    r.run(4000, measured_dt=jitter)
    assert r.drive.state == DS.OPERATION_ENABLED, f"fault 0x{r.st.error_code:04X}"
    assert r.st.position_actual == 50000


def test_26_csp_jump_causes_following_error_8611(rig):
    rig.enable()
    rig.switch_mode(OM.CYCLIC_SYNC_POSITION)
    rig.ctl.move(1_000_000, False, 1_000_000, 1e9, 1e9)   # far beyond drive max velocity
    rig.run_until(lambda s: s.state == DS.FAULT, 200)
    assert rig.st.error_code == 0x8611


# ---------------------------------------------------------------- trajectory properties

def _run_traj(t: TrajectoryGenerator, cycles, dt=DT, on_step=None):
    xs = []
    for i in range(cycles):
        if on_step:
            on_step(i, t)
        t.step(dt)
        xs.append((t.position, t.velocity))
    return xs


@pytest.mark.parametrize("seed", range(40))
def test_27_trajectory_fixed_goal_never_overshoots_and_arrives(seed):
    rnd = random.Random(seed)
    t = TrajectoryGenerator()
    start = rnd.uniform(-1e5, 1e5)
    goal = start + rnd.choice([-1, 1]) * rnd.uniform(1, 2e5)
    vmax, acc, dec = rnd.uniform(100, 5e5), rnd.uniform(1e3, 1e7), rnd.uniform(1e3, 1e7)
    t.reset(start)
    t.move_to(goal, vmax, acc, dec)
    d = math.copysign(1, goal - start)
    xs = _run_traj(t, 200000)
    for (p, v) in xs:
        assert (goal - p) * d >= -1e-6, "overshoot"
        assert abs(v) <= vmax + 1e-6
    prev_v = 0.0
    for (_, v) in xs:
        # within limits except possibly the final landing step
        if v != 0:
            assert abs(v - prev_v) <= max(acc, dec) * DT * (1 + 1e-9)
        prev_v = v
    assert t.position == goal and t.velocity == 0, "did not arrive"


@pytest.mark.parametrize("seed", range(40))
def test_28_trajectory_random_retarget_bounded_acc_and_arrives(seed):
    rnd = random.Random(1000 + seed)
    t = TrajectoryGenerator()
    t.reset(0)
    acc = dec = rnd.uniform(1e4, 1e6)
    vmax = rnd.uniform(1e3, 1e5)
    goals = []

    def retarget(i, tr):
        if i % rnd.randint(50, 400) == 0 and i < 20000:
            g = rnd.uniform(-5e4, 5e4)
            goals.append(g)
            tr.move_to(g, vmax, acc, dec)
    xs = _run_traj(t, 20000, on_step=retarget)
    vs = [0.0] + [v for _, v in xs]
    for a, b in zip(vs, vs[1:]):
        assert abs(b - a) <= 2 * max(acc, dec) * DT * (1 + 1e-9), "acceleration above 2x limit"
    for _ in range(2_000_000):
        if not t.is_moving:
            break
        t.step(DT)
    assert t.position == goals[-1] and t.velocity == 0, "did not reach last goal"


def test_28b_trajectory_velocity_mode_ramp():
    t = TrajectoryGenerator()
    t.reset(0)
    t.run_at(1000, 10000, 20000)
    xs = _run_traj(t, 200)
    assert xs[49][1] == pytest.approx(500, abs=1e-6)
    assert xs[-1][1] == 1000
    t.stop()
    xs = _run_traj(t, 100)
    assert xs[-1][1] == 0

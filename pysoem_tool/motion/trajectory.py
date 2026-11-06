"""Online trapezoidal profile, stepped once per cycle (port of TrajectoryGenerator.cs).

Position goal: accelerate, cruise at vmax, decelerate to stop exactly at the goal.
Velocity goal: ramp to a target speed and keep moving. Goals may change at any time; the
profile always continues from the current position and velocity.
"""
from __future__ import annotations

import math


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


class TrajectoryGenerator:
    def __init__(self) -> None:
        self.position = 0.0
        self.velocity = 0.0
        self.position_mode = True
        self.goal_position = 0.0
        self.goal_velocity = 0.0
        self.max_velocity = 1000.0
        self.acceleration = 10000.0
        self.deceleration = 10000.0

    @property
    def is_moving(self) -> bool:
        return self.velocity != 0 or (self.position_mode and self.position != self.goal_position)

    def reset(self, position: float) -> None:
        """Jump to ``position`` at standstill (follow the actual position while disabled)."""
        self.position = float(position)
        self.velocity = 0.0
        self.position_mode = True
        self.goal_position = float(position)
        self.goal_velocity = 0.0

    def move_to(self, goal: float, vmax: float, acc: float, dec: float) -> None:
        self._set_limits(vmax, acc, dec)
        self.position_mode = True
        self.goal_position = float(goal)

    def run_at(self, velocity: float, acc: float, dec: float) -> None:
        self._set_limits(max(self.max_velocity, abs(velocity)), acc, dec)
        self.position_mode = False
        self.goal_velocity = float(velocity)

    def stop(self, dec: float | None = None) -> None:
        """Decelerate to standstill (with ``dec`` if given)."""
        if dec is not None and dec > 0:
            self.deceleration = float(dec)
        self.position_mode = False
        self.goal_velocity = 0.0

    def _set_limits(self, vmax: float, acc: float, dec: float) -> None:
        self.max_velocity = max(abs(vmax), 1e-9)
        self.acceleration = max(abs(acc), 1e-9)
        self.deceleration = max(abs(dec), 1e-9)

    def step(self, dt: float) -> None:
        if dt <= 0:
            return

        if self.position_mode:
            remaining = self.goal_position - self.position
            direction = _sign(remaining)
            # Highest end-of-step speed v' from which we can still stop exactly at the goal,
            # including the distance covered during this step (handoff §6.11):
            #   |rem| - (v + v')/2 * dt = v'^2 / (2 * dec)
            v_toward = direction * self.velocity
            inner = dt * dt / 4 + 2 * (abs(remaining) - v_toward * dt / 2) / self.deceleration
            v_stop = self.deceleration * (-dt / 2 + math.sqrt(inner)) if inner > 0 else 0.0
            target_vel = direction * min(self.max_velocity, max(v_stop, 0.0))

            # Close enough to finish this cycle without overshooting.
            if (abs(remaining) <= abs(self.velocity) * dt + 1e-9
                    and abs(self.velocity) <= self.deceleration * dt * 1.5):
                self.position = self.goal_position
                self.velocity = 0.0
                return
        else:
            target_vel = min(max(self.goal_velocity, -self.max_velocity), self.max_velocity)

        # Accelerate when speeding up in the current direction, decelerate otherwise.
        speeding_up = abs(target_vel) > abs(self.velocity) and _sign(target_vel) * _sign(self.velocity) >= 0
        rate = (self.acceleration if speeding_up else self.deceleration) * dt
        dv = target_vel - self.velocity
        new_vel = target_vel if abs(dv) <= rate else self.velocity + _sign(dv) * rate

        new_pos = self.position + (self.velocity + new_vel) * 0.5 * dt

        # Land exactly on the goal only when the speed before this step is within two
        # deceleration steps; otherwise (goal moved closer than the stopping distance) overshoot
        # and come back instead of stopping abruptly.
        if self.position_mode:
            dir_before = _sign(self.goal_position - self.position)
            dir_after = _sign(self.goal_position - new_pos)
            if dir_before != 0 and dir_after != dir_before and abs(self.velocity) <= 2 * self.deceleration * dt:
                self.position = self.goal_position
                self.velocity = 0.0
                return

        self.position = new_pos
        self.velocity = new_vel

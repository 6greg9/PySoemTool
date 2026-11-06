"""CiA 402 (IEC 61800-7-201) drive profile: object indices, modes and state machine."""
from __future__ import annotations

from enum import Enum, IntEnum

# ---- object dictionary indices -------------------------------------------------------------
ERROR_CODE = 0x603F
CONTROLWORD = 0x6040
STATUSWORD = 0x6041
QUICK_STOP_OPTION_CODE = 0x605A
MODES_OF_OPERATION = 0x6060
MODES_OF_OPERATION_DISPLAY = 0x6061
POSITION_ACTUAL_VALUE = 0x6064
VELOCITY_ACTUAL_VALUE = 0x606C
TARGET_TORQUE = 0x6071
TORQUE_ACTUAL_VALUE = 0x6077
TARGET_POSITION = 0x607A
HOME_OFFSET = 0x607C
PROFILE_VELOCITY = 0x6081
PROFILE_ACCELERATION = 0x6083
PROFILE_DECELERATION = 0x6084
QUICK_STOP_DECELERATION = 0x6085
HOMING_METHOD = 0x6098
HOMING_SPEEDS = 0x6099            # :01 search switch, :02 search zero
HOMING_ACCELERATION = 0x609A
FOLLOWING_ERROR_ACTUAL_VALUE = 0x60F4
DIGITAL_INPUTS = 0x60FD
TARGET_VELOCITY = 0x60FF
SUPPORTED_DRIVE_MODES = 0x6502
SYNC_MANAGER_OUTPUT_PARAMETER = 0x1C32   # :01 synchronization type

# ---- controlword ---------------------------------------------------------------------------
CW_SWITCH_ON = 1 << 0
CW_ENABLE_VOLTAGE = 1 << 1
CW_QUICK_STOP = 1 << 2            # active low
CW_ENABLE_OPERATION = 1 << 3
CW_NEW_SET_POINT = 1 << 4         # PP; homing start in HM
CW_CHANGE_SET_IMMEDIATELY = 1 << 5
CW_RELATIVE = 1 << 6
CW_FAULT_RESET = 1 << 7
CW_HALT = 1 << 8

CMD_DISABLE_VOLTAGE = 0x0000
CMD_QUICK_STOP = 0x0002
CMD_SHUTDOWN = 0x0006
CMD_SWITCH_ON = 0x0007
CMD_ENABLE_OPERATION = 0x000F

# ---- statusword ----------------------------------------------------------------------------
SW_READY_TO_SWITCH_ON = 1 << 0
SW_SWITCHED_ON = 1 << 1
SW_OPERATION_ENABLED = 1 << 2
SW_FAULT = 1 << 3
SW_VOLTAGE_ENABLED = 1 << 4
SW_QUICK_STOP = 1 << 5            # active low
SW_SWITCH_ON_DISABLED = 1 << 6
SW_WARNING = 1 << 7
SW_REMOTE = 1 << 9
SW_TARGET_REACHED = 1 << 10
SW_INTERNAL_LIMIT = 1 << 11
SW_OP_MODE_SPECIFIC_12 = 1 << 12  # PP: set-point ack, PV: speed 0, HM: attained, CSx: target followed
SW_OP_MODE_SPECIFIC_13 = 1 << 13  # PP/CSP: following error, HM: homing error


class DriveState(Enum):
    UNKNOWN = "Unknown"
    NOT_READY_TO_SWITCH_ON = "Not ready to switch on"
    SWITCH_ON_DISABLED = "Switch on disabled"
    READY_TO_SWITCH_ON = "Ready to switch on"
    SWITCHED_ON = "Switched on"
    OPERATION_ENABLED = "Operation enabled"
    QUICK_STOP_ACTIVE = "Quick stop active"
    FAULT_REACTION_ACTIVE = "Fault reaction active"
    FAULT = "Fault"

    def __str__(self) -> str:
        return self.value


class OperationMode(IntEnum):
    NONE = 0
    PROFILE_POSITION = 1
    PROFILE_VELOCITY = 3
    HOMING = 6
    CYCLIC_SYNC_POSITION = 8
    CYCLIC_SYNC_VELOCITY = 9
    CYCLIC_SYNC_TORQUE = 10


def mode_from_int(v: int) -> OperationMode | int:
    try:
        return OperationMode(v)
    except ValueError:
        return v


def decode(sw: int) -> DriveState:
    if (sw & 0x4F) == 0x00:
        return DriveState.NOT_READY_TO_SWITCH_ON
    if (sw & 0x4F) == 0x40:
        return DriveState.SWITCH_ON_DISABLED
    if (sw & 0x6F) == 0x21:
        return DriveState.READY_TO_SWITCH_ON
    if (sw & 0x6F) == 0x23:
        return DriveState.SWITCHED_ON
    if (sw & 0x6F) == 0x27:
        return DriveState.OPERATION_ENABLED
    if (sw & 0x6F) == 0x07:
        return DriveState.QUICK_STOP_ACTIVE
    if (sw & 0x4F) == 0x0F:
        return DriveState.FAULT_REACTION_ACTIVE
    if (sw & 0x4F) == 0x08:
        return DriveState.FAULT
    return DriveState.UNKNOWN


_MODE_NAMES = {
    OperationMode.PROFILE_POSITION: "PP 位置模式",
    OperationMode.PROFILE_VELOCITY: "PV 速度模式",
    OperationMode.HOMING: "HM 原點復歸",
    OperationMode.CYCLIC_SYNC_POSITION: "CSP 同步位置",
    OperationMode.CYCLIC_SYNC_VELOCITY: "CSV 同步速度",
    OperationMode.CYCLIC_SYNC_TORQUE: "CST 同步轉矩",
    OperationMode.NONE: "-",
}


def describe_mode(m: int) -> str:
    try:
        return _MODE_NAMES[OperationMode(m)]
    except (ValueError, KeyError):
        return f"模式 {m}"


def bit_name(bit: int, mode: int) -> str:
    """Statusword bit names; bits 12/13 interpreted for ``mode``."""
    fixed = {0: "Ready to switch on", 1: "Switched on", 2: "Operation enabled", 3: "Fault",
             4: "Voltage enabled", 5: "Quick stop (0 = active)", 6: "Switch on disabled",
             7: "Warning", 8: "(製造商定義)", 9: "Remote", 10: "Target reached",
             11: "Internal limit active"}
    if bit in fixed:
        return fixed[bit]
    if bit == 12:
        return {OperationMode.PROFILE_POSITION: "Set-point acknowledge",
                OperationMode.PROFILE_VELOCITY: "Speed = 0",
                OperationMode.HOMING: "Homing attained",
                OperationMode.CYCLIC_SYNC_POSITION: "Target value followed",
                OperationMode.CYCLIC_SYNC_VELOCITY: "Target value followed"}.get(mode, "Op mode specific")
    if bit == 13:
        return {OperationMode.PROFILE_POSITION: "Following error",
                OperationMode.CYCLIC_SYNC_POSITION: "Following error",
                OperationMode.HOMING: "Homing error"}.get(mode, "Op mode specific")
    return "(製造商定義)"


OBJECT_NAMES = {
    CONTROLWORD: "Controlword", STATUSWORD: "Statusword", MODES_OF_OPERATION: "Modes of operation",
    MODES_OF_OPERATION_DISPLAY: "Modes display", TARGET_POSITION: "Target position",
    TARGET_VELOCITY: "Target velocity", TARGET_TORQUE: "Target torque",
    POSITION_ACTUAL_VALUE: "Position actual", VELOCITY_ACTUAL_VALUE: "Velocity actual",
    TORQUE_ACTUAL_VALUE: "Torque actual", ERROR_CODE: "Error code",
    FOLLOWING_ERROR_ACTUAL_VALUE: "Following error", DIGITAL_INPUTS: "Digital inputs",
    PROFILE_VELOCITY: "Profile velocity", PROFILE_ACCELERATION: "Profile acceleration",
    PROFILE_DECELERATION: "Profile deceleration",
}

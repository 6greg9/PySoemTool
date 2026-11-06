"""Hardware acceptance run against the real bus (handoff §9), without the GUI.

    .venv\\Scripts\\python tools\\hw_acceptance.py --adapter "GbE"            # SAFE-OP only
    .venv\\Scripts\\python tools\\hw_acceptance.py --adapter "GbE" --allow-op # also OP tests

--allow-op puts the bus into OP with all outputs kept at 0 (outputs are never toggled here).
Without it, only checks below OP run. Prints a summary with measured numbers.
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pysoem_tool.motion.channel_ethercat import read_pdo_config  # noqa: E402
from pysoem_tool.services.ethercat import EcState, EtherCatService  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'OK' if ok else 'FAIL'}] {name}  {detail}")


def soak(svc: EtherCatService, seconds: float, label: str) -> None:
    svc.reset_stats()
    svc.drain_events()
    c0 = svc.get_stats().cycles
    time.sleep(seconds)
    st = svc.get_stats()
    ev = svc.drain_events()
    gc_ev = sum(1 for e in ev if e.gc_runs)
    check(f"{label}: {seconds:.0f} s WKC 不足 0 次", st.wkc_errors == 0,
          f"cycles {st.cycles - c0}, avg {st.avg_cycle_us:.0f} µs, max {st.max_cycle_us:.0f} µs, "
          f"掉 frame {st.lost_frames}, WKC 不足 {st.wkc_errors}, 異常週期事件 {len(ev)}（其中 GC {gc_ev}）"
          + (f", DC 偏差 {st.dc_deviation_ns / 1000:.0f} µs" if st.dc_sync_active else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True, help="substring of the adapter description")
    ap.add_argument("--cycle-ms", type=int, default=2)
    ap.add_argument("--soak", type=float, default=30.0)
    ap.add_argument("--allow-op", action="store_true")
    a = ap.parse_args()

    svc = EtherCatService()
    ad = next(x for x in svc.get_adapters() if a.adapter.lower() in x.desc.lower())
    slaves = svc.open(ad.name, a.cycle_ms)
    try:
        st = svc.get_stats()
        time.sleep(0.5)
        st = svc.get_stats()
        check("1. 掃描 / SAFE-OP / WKC", len(slaves) >= 1 and all(s.state & 0x0F == EcState.SAFEOP for s in slaves)
              and st.last_wkc == st.expected_wkc,
              f"{len(slaves)} 台 {[s.name for s in slaves]}, WKC {st.last_wkc}/{st.expected_wkc}")

        for s in slaves:
            v = struct.unpack("<I", svc.sdo_read(s.position, 0x1018, 1))[0]
            rx = read_pdo_config(svc, s.position, 0x1C12)
            tx = read_pdo_config(svc, s.position, 0x1C13)
            check(f"3. #{s.position} SDO 0x1018:01 與 PDO", True,
                  f"0x{v:08X}; Rx {[f'{e.index:04X}:{e.sub_index:02X}({e.bit_length})' for e in rx]}; "
                  f"Tx {[f'{e.index:04X}:{e.sub_index:02X}({e.bit_length})' for e in tx]}")
        try:
            svc.sdo_read(1, 0x6040, 0)
            check("SDO abort code", False, "0x6040 讀取竟然成功")
        except Exception as e:  # noqa: BLE001
            check("SDO abort code", "0x06020000" in str(e), str(e))

        soak(svc, min(a.soak, 10), "SAFE-OP")

        dc = [s.position for s in slaves if s.has_dc]
        for p in dc:
            svc.configure_dc_sync0(p, True, 0)
        regs = {p: (svc.read_register(p, 0x0981, 1)[0], struct.unpack("<I", svc.read_register(p, 0x09A0, 4))[0]) for p in dc}
        check("5a. DC SYNC0 啟用 (0x0981 / 0x09A0)", all(r[0] == 0x03 and r[1] == a.cycle_ms * 1_000_000 for r in regs.values()),
              ", ".join(f"#{p} 0x0981=0x{r[0]:02X} 0x09A0={r[1]}" for p, r in regs.items()))

        if a.allow_op:
            steps = svc.change_state(EcState.OP)
            check("2. SAFE-OP → OP", True, "、".join(steps))
            soak(svc, a.soak, "OP")
            for start, label in ((EcState.INIT, "INIT → OP"), (EcState.PREOP, "PRE-OP → OP")):
                svc.change_state(start)
                steps = svc.change_state(EcState.OP)
                check(f"4. {label}", True, "、".join(steps))
            s1 = svc.change_state(EcState.PREOP)
            v = struct.unpack("<I", svc.sdo_read(1, 0x1018, 1))[0]
            s2 = svc.change_state(EcState.OP)
            check("4. OP → PRE-OP（讀 SDO）→ OP", v == slaves[0].vendor,
                  f"PRE-OP 讀 0x1018:01 = 0x{v:08X}; " + "、".join(s1 + s2))
            regs = {p: svc.read_register(p, 0x0981, 1)[0] for p in dc}
            check("5b. INIT/PRE-OP → OP 後 SYNC0 仍有效", all(r == 0x03 for r in regs.values()),
                  ", ".join(f"#{p} 0x0981=0x{r:02X}" for p, r in regs.items()))
            soak(svc, 10, "OP + DC")
            svc.change_state(EcState.SAFEOP)
        else:
            for start, label in ((EcState.INIT, "INIT → SAFE-OP"), (EcState.PREOP, "PRE-OP → SAFE-OP")):
                svc.change_state(start)
                steps = svc.change_state(EcState.SAFEOP)
                check(f"4'. {label}（未允許 OP）", True, "、".join(steps))
            regs = {p: svc.read_register(p, 0x0981, 1)[0] for p in dc}
            check("5b'. 重新設定後 SYNC0 仍有效", all(r == 0x03 for r in regs.values()),
                  ", ".join(f"#{p} 0x0981=0x{r:02X}" for p, r in regs.items()))
        for p in dc:
            svc.configure_dc_sync0(p, False)
    finally:
        svc.close()
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

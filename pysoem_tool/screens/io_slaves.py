"""IO / Slaves tab: state control, slave list, IO panel, SDO."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..mvvm import Bindings, UiDispatcher, bind_command, bind_enabled, bind_entry, bind_text
from ..viewmodels.main_vm import MainViewModel, SlaveRow
from .common import bind_text_latest

COLUMNS = (("pos", "#", 4), ("name", "名稱", 16), ("state", "狀態", 12), ("al", "AL Status", 13),
           ("vendor", "Vendor", 11), ("product", "Product", 11), ("rev", "Revision", 11),
           ("addr", "位址", 7), ("out", "Out bits", 7), ("in", "In bits", 7), ("dc", "DC", 4))
BITS_PER_ROW = 8
STATE_BUTTONS = (("INIT", "INIT"), ("PREOP", "PRE-OP"), ("SAFEOP", "SAFE-OP"), ("OP", "OP"))


class IoSlavesView(ttk.Frame):
    def __init__(self, master: tk.Misc, theme):
        sp = theme.px
        super().__init__(master, padding=sp("space-2"))
        self.theme = theme
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, sp("space-2")))
        ttk.Label(bar, text="EtherCAT 狀態").pack(side="left", padx=(0, sp("space-2")))
        self.btn_state: dict[str, ttk.Button] = {}
        for key, text in STATE_BUTTONS:
            b = ttk.Button(bar, text=text, width=8)
            b.pack(side="left", padx=(0, sp("space-1")))
            self.btn_state[key] = b
        self.btn_ack = ttk.Button(bar, text="錯誤確認 (ACK)")
        self.btn_ack.pack(side="left", padx=(sp("space-3"), sp("space-1")))
        self.btn_all_off = ttk.Button(bar, text="全部輸出 OFF")
        self.btn_all_off.pack(side="left", padx=(sp("space-3"), 0))

        frm_tree = ttk.Frame(self)
        frm_tree.grid(row=1, column=0, sticky="nsew")
        frm_tree.columnconfigure(0, weight=1)
        frm_tree.rowconfigure(0, weight=1)
        self.tree_slaves = ttk.Treeview(frm_tree, columns=[c[0] for c in COLUMNS], show="headings",
                                        height=5, selectmode="browse")
        char = theme.font("label").measure("0")
        for key, text, width in COLUMNS:
            self.tree_slaves.heading(key, text=text, anchor="w")
            self.tree_slaves.column(key, width=width * char + sp("space-3"), minwidth=3 * char,
                                    stretch=key == "name", anchor="w")
        self.tree_slaves.tag_configure("error", background=theme.color("alarm-high"),
                                       foreground=theme.color("ink"))
        self.tree_slaves.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frm_tree, orient="vertical", command=self.tree_slaves.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree_slaves.configure(yscrollcommand=sb.set)

        bottom = ttk.Frame(self)
        bottom.grid(row=2, column=0, sticky="ew", pady=(sp("space-2"), 0))
        bottom.columnconfigure(0, weight=3)
        bottom.columnconfigure(1, weight=2)

        # IO panel
        io = ttk.Frame(bottom, style="Panel.TFrame", padding=sp("space-3"))
        io.grid(row=0, column=0, sticky="nsew", padx=(0, sp("space-2")))
        io.columnconfigure(1, weight=1)
        self.lbl_io_title = ttk.Label(io, text="IO", style="Panel.Section.TLabel")
        self.lbl_io_title.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, sp("space-2")))
        ttk.Label(io, text="輸出", style="Panel.TLabel").grid(row=1, column=0, sticky="nw", padx=(0, sp("space-2")))
        self.frm_out = ttk.Frame(io, style="PanelBody.TFrame")
        self.frm_out.grid(row=1, column=1, sticky="w")
        ttk.Label(io, text="輸入", style="Panel.TLabel").grid(row=2, column=0, sticky="nw",
                                                            padx=(0, sp("space-2")), pady=(sp("space-2"), 0))
        self.frm_in = ttk.Frame(io, style="PanelBody.TFrame")
        self.frm_in.grid(row=2, column=1, sticky="w", pady=(sp("space-2"), 0))
        ttk.Label(io, text="輸入 HEX", style="Panel.TLabel").grid(row=3, column=0, sticky="w",
                                                                padx=(0, sp("space-2")), pady=(sp("space-2"), 0))
        self.lbl_in_hex = ttk.Label(io, text="", style="Panel.Live.TLabel")
        self.lbl_in_hex.grid(row=3, column=1, sticky="w", pady=(sp("space-2"), 0))
        self.lbl_io_note = ttk.Label(io, text="", style="Panel.Caption.TLabel", wraplength=480)
        self.lbl_io_note.grid(row=4, column=0, columnspan=2, sticky="w", pady=(sp("space-2"), 0))
        self.btn_out: list[ttk.Button] = []
        self.lbl_in: list[ttk.Label] = []

        # SDO panel
        sdo = ttk.Frame(bottom, style="Panel.TFrame", padding=sp("space-3"))
        sdo.grid(row=0, column=1, sticky="nsew")
        sdo.columnconfigure(1, weight=1)
        ttk.Label(sdo, text="SDO（選取的 slave）", style="Panel.Section.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, sp("space-2")))
        for r, text in enumerate(("Index (hex)", "SubIndex", "資料 (LE hex)"), 1):
            ttk.Label(sdo, text=text, style="Panel.TLabel").grid(row=r, column=0, sticky="w",
                                                                 padx=(0, sp("space-2")), pady=sp("space-1"))
        self.ent_index = ttk.Entry(sdo, width=8, style="Setpoint.TEntry")
        self.ent_index.grid(row=1, column=1, sticky="w", pady=sp("space-1"))
        self.ent_sub = ttk.Entry(sdo, width=8, style="Setpoint.TEntry")
        self.ent_sub.grid(row=2, column=1, sticky="w", pady=sp("space-1"))
        self.ent_data = ttk.Entry(sdo, width=20, style="Setpoint.TEntry")
        self.ent_data.grid(row=3, column=1, sticky="ew", pady=sp("space-1"))
        self.btn_sdo_read = ttk.Button(sdo, text="讀取")
        self.btn_sdo_read.grid(row=1, column=2, sticky="ew", padx=(sp("space-2"), 0))
        self.btn_sdo_write = ttk.Button(sdo, text="寫入")
        self.btn_sdo_write.grid(row=3, column=2, sticky="ew", padx=(sp("space-2"), 0))
        self.lbl_sdo_result = ttk.Label(sdo, text="", style="Panel.Live.TLabel", justify="left",
                                        wraplength=360, font=theme.font("id"))
        self.lbl_sdo_result.grid(row=4, column=0, columnspan=3, sticky="nw", pady=(sp("space-2"), 0))

    def set_bit_counts(self, n_out: int, n_in: int) -> None:
        """Rebuilds the bit buttons / lamps when the selected slave's IO size changes."""
        sp = self.theme.px
        if len(self.btn_out) != n_out:
            for w in self.btn_out:
                w.destroy()
            self.btn_out = []
            for b in range(n_out):
                w = ttk.Button(self.frm_out, text=str(b), style="BitOff.TButton")
                w.grid(row=b // BITS_PER_ROW, column=b % BITS_PER_ROW, padx=(0, sp("space-1")), pady=(0, sp("space-1")))
                self.btn_out.append(w)
        if len(self.lbl_in) != n_in:
            for w in self.lbl_in:
                w.destroy()
            self.lbl_in = []
            for b in range(n_in):
                w = ttk.Label(self.frm_in, text=str(b), style="BitOff.TLabel", width=3)
                w.grid(row=b // BITS_PER_ROW, column=b % BITS_PER_ROW, padx=(0, sp("space-1")), pady=(0, sp("space-1")))
                self.lbl_in.append(w)


def bind_io_slaves(view: IoSlavesView, vm: MainViewModel, ui: UiDispatcher) -> Bindings:
    b = Bindings(view)
    for key, btn in view.btn_state.items():
        b.add(bind_command(btn, vm.cmd_state[key], ui))
    b.add(bind_command(view.btn_ack, vm.cmd_ack, ui))
    b.add(bind_command(view.btn_all_off, vm.cmd_all_off, ui))
    b.add(bind_command(view.btn_sdo_read, vm.cmd_sdo_read, ui))
    b.add(bind_command(view.btn_sdo_write, vm.cmd_sdo_write, ui))
    b.add(bind_entry(view.ent_index, vm.sdo_index, ui))
    b.add(bind_entry(view.ent_sub, vm.sdo_sub, ui))
    b.add(bind_entry(view.ent_data, vm.sdo_data, ui))
    b.add(bind_text(view.lbl_sdo_result, vm.sdo_result, ui))
    b.add(bind_text(view.lbl_io_title, vm.io_title, ui, fmt=lambda t: f"IO  {t}"))
    b.add(bind_text(view.lbl_io_note, vm.io_note, ui))
    b.add(bind_text_latest(view.lbl_in_hex, vm.in_hex, ui, fmt=lambda h: h or "-"))

    tree = view.tree_slaves
    updating = {"v": False}

    def show_rows(rows: tuple[SlaveRow, ...]):
        updating["v"] = True
        try:
            existing = set(tree.get_children())
            wanted = {str(r.position) for r in rows}
            for iid in existing - wanted:
                tree.delete(iid)
            for r in rows:
                vals = (r.position, r.name, r.state, r.al_status, r.vendor, r.product, r.revision,
                        r.address, r.out_bits, r.in_bits, r.dc)
                tags = ("error",) if r.has_error else ()
                if str(r.position) in existing:
                    tree.item(str(r.position), values=vals, tags=tags)
                else:
                    tree.insert("", "end", iid=str(r.position), values=vals, tags=tags)
            sel = vm.selected_slave.value
            if sel is not None and tree.exists(str(sel)) and tree.selection() != (str(sel),):
                tree.selection_set(str(sel))
        finally:
            updating["v"] = False

    def on_select(_e):
        if updating["v"]:
            return
        s = tree.selection()
        vm.selected_slave.value = int(s[0]) if s else None

    tree.bind("<<TreeviewSelect>>", on_select, add="+")
    b.add(vm.slaves.subscribe(lambda rows: ui.post_latest("slave rows", show_rows, rows)))
    b.add(vm.selected_slave.subscribe(lambda _: ui.post_latest("slave rows", show_rows, vm.slaves.value),
                                      immediate=False))

    def show_out(bits):
        view.set_bit_counts(len(bits), len(view.lbl_in))
        for i, (w, on) in enumerate(zip(view.btn_out, bits)):
            w.configure(style="BitOn.TButton" if on else "BitOff.TButton",
                        command=lambda i=i: vm.toggle_output(i))
        enable_out(vm.outputs_editable.value)

    def show_in(bits):
        view.set_bit_counts(len(view.btn_out), len(bits))
        for w, on in zip(view.lbl_in, bits):
            w.configure(style="BitOn.TLabel" if on else "BitOff.TLabel")

    def enable_out(ok):
        for w in view.btn_out:
            w.state(["!disabled"] if ok else ["disabled"])

    b.add(vm.out_bits.subscribe(lambda v: ui.post_latest("out bits", show_out, v)))
    b.add(vm.in_bits.subscribe(lambda v: ui.post_latest("in bits", show_in, v)))
    b.add(vm.outputs_editable.subscribe(lambda v: ui.post(enable_out, v)))
    return b


def preview(master, ui):
    view = IoSlavesView(master, master.theme)
    import tempfile
    from pathlib import Path
    from ..viewmodels.base import LogSink
    _preview_vm = lambda: MainViewModel(log=LogSink(Path(tempfile.gettempdir())))
    vm = _preview_vm()
    bind_io_slaves(view, vm, ui)

    def rows(err=False, n=2, name="DSNX16Y16L-MW"):
        return tuple(SlaveRow(i, name, "SAFE-OP + ERR" if err and i == 2 else "OP", "0x001B Sync manager watchdog" if err and i == 2 else "-",
                              "0xE2000044", "0x00000007", "0x00060001", f"0x{0x1000 + i:04X}", 16, 16, "有",
                              err and i == 2, not (err and i == 2)) for i in range(1, n + 1))

    def connected(err=False, **kw):
        vm.is_connected.value = True
        vm.slaves.value = rows(err, **kw)
        vm.selected_slave.value = 1
        vm.io_title.value = vm.slaves.value[0].title
        vm.out_bits.value = tuple(i in (0, 3, 9) for i in range(16))
        vm.in_bits.value = tuple(i in (1, 2, 15) for i in range(16))
        vm.in_hex.value = "0680"
        vm.io_note.value = "點擊輸出切換；只有 OP 狀態下輸出才會真正驅動現場設備。"
        vm.sdo_result.value = "HEX: 440000E2  (4 bytes)\nU32: 3791650884  (0xE2000044)"

    def axis_owned():
        connected()
        vm.axis.controlled_slave.value = 1
        vm.io_note.value = "此 slave 由軸分頁控制，輸出唯讀。"

    def busy():
        connected()
        vm.is_busy.value = True

    def long_text():
        connected(err=True, n=12, name="EK1100 EtherCAT-Koppler (2A E-Bus) very long product name")
        vm.sdo_result.value = ("SDO abort 0x06020000（The object does not exist in the object directory），"
                               "slave #1 0x6040:00")

    return view, {"empty": lambda: None, "connected": connected, "error": lambda: connected(err=True),
                  "axis_owned": axis_owned, "busy": busy, "long_text": long_text}

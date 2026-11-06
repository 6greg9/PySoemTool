"""Design-system tokens.json -> ttk styles, named fonts and Canvas helpers.

The tokens file is exported from the "Equipment HMI (tkinter)" Design System
(project/tokens.json). Every color, font and spacing value in the app comes from here;
views reference style names and theme lookups, never hex codes or font tuples.

Usage:
    theme = apply_theme(root, "app/design/tokens.json")
    ttk.Label(frm, text="101.3", style="Live.TLabel")
    frm.grid(padx=theme.px("space-3"))
    theme.color("state-active")
"""
from __future__ import annotations

import json
import pathlib
import re
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any, Callable

_PX = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(px)?\s*$")
_MS = re.compile(r"^\s*(\d+)\s*ms\s*$")


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class Theme:
    """Resolved tokens. Read-only lookups for views and binders."""

    def __init__(self, tokens: dict[str, Any], root: tk.Misc):
        self.tokens = tokens
        theme_id = tokens["color"]["themes"][0]["id"]
        raw: dict[str, str] = {}
        for t in tokens["color"]["tokens"]:
            v = t["value"]
            raw[t["name"]] = v if isinstance(v, str) else v.get(theme_id, next(iter(v.values())))
        self._colors = {name: self._resolve(name, raw, 0) for name in raw}
        self._lengths: dict[str, int] = {}
        self._timing: dict[str, int] = {}
        for family, block in tokens.items():
            if not isinstance(block, dict) or "tokens" not in block or family == "color":
                continue
            for t in block["tokens"]:
                val = str(t["value"])
                if m := _PX.match(val):
                    self._lengths[t["name"]] = int(round(float(m.group(1))))
                elif m := _MS.match(val):
                    self._timing[t["name"]] = int(m.group(1))
        self.fonts = self._build_fonts(root)

    def _resolve(self, name: str, raw: dict[str, str], depth: int) -> str:
        v = raw[name]
        if v.startswith("{") and v.endswith("}"):
            if depth > 16:
                raise ValueError(f"alias chain too deep at {name}")
            return self._resolve(v[1:-1], raw, depth + 1)
        return v

    def _build_fonts(self, root: tk.Misc) -> dict[str, tkfont.Font]:
        installed = {f.lower() for f in tkfont.families(root)}
        families = {}
        for key, stack in self.tokens["type"]["families"].items():
            names = [s.strip().strip('"') for s in stack.split(",")]
            generic = {"sans-serif": "TkDefaultFont", "monospace": "TkFixedFont", "serif": "Times"}
            chosen = next((n for n in names if n.lower() in installed), None)
            if chosen is None:
                chosen = tkfont.nametofont(generic.get(names[-1], "TkDefaultFont")).actual("family")
            families[key] = chosen
        fonts = {}
        for group in self.tokens["type"]["groups"]:
            for st in group["styles"]:
                size_px = int(round(float(_PX.match(str(st["fontSize"])).group(1))))
                weight = "bold" if int(str(st.get("fontWeight", 400)).split()[0]) >= 600 else "normal"
                fam = families[st.get("family", group["family"])]
                fname = f"hmi.{st['name']}"
                exists = fname in tkfont.names(root)      # re-applying the theme reconfigures
                font = tkfont.Font(root=root, name=fname, exists=exists)
                font.configure(family=fam, size=-size_px, weight=weight)
                fonts[st["name"]] = font
        return fonts

    def color(self, name: str) -> str:
        return self._colors[name]

    def px(self, name: str) -> int:
        return self._lengths[name]

    def ms(self, name: str) -> int:
        return self._timing[name]

    def font(self, name: str) -> tkfont.Font:
        return self.fonts[name]

    def on(self, fill_token: str) -> str:
        """Readable text color for a fill: ink or white, whichever contrasts more."""
        fill = self.color(fill_token)
        return max((self.color("ink"), "#ffffff"), key=lambda c: contrast(fill, c))


# ---- style table: token names in, ttk style names out --------------------------------------

TEXT_STYLES = {
    # style name           (font token,   foreground token)
    "TLabel":              ("label",        "ink"),
    "Title.TLabel":        ("screen-title", "ink"),
    "Section.TLabel":      ("section",      "ink"),
    "Caption.TLabel":      ("caption",      "ink-muted"),
    "Live.TLabel":         ("value",        "live"),
    "LiveLarge.TLabel":    ("value-large",  "live"),
    "Setpoint.TLabel":     ("value",        "setpoint"),
    "Id.TLabel":           ("id",           "ink"),
    "Running.TLabel":      ("label",        "running"),
}

FILL_STYLES = {
    # style name                  fill token          text token (None = auto)
    "AlarmCritical.TLabel":       ("alarm-critical",  "on-alarm-critical"),
    "AlarmHigh.TLabel":           ("alarm-high",      "on-alarm"),
    "AlarmMedium.TLabel":         ("alarm-medium",    "on-alarm"),
    "AlarmAdvisory.TLabel":       ("alarm-advisory",  "on-alarm"),
    "BadQuality.TLabel":          ("bad-quality",     "on-alarm-critical"),
    "Shelved.TLabel":             ("shelved",         "on-alarm"),
    "StateIdle.TLabel":           ("state-idle",      None),
    "StateActive.TLabel":         ("state-active",    None),
    "StateDone.TLabel":           ("state-done",      None),
    "StateAttention.TLabel":      ("state-attention", None),
    "StateAlarm.TLabel":          ("state-alarm",     None),
    # EtherCAT tool additions: IO bit lamps (on = live fill, off = field ground)
    "BitOn.TLabel":               ("state-active",    None),
    "BitOff.TLabel":              ("surface-field",   None),
    "BitOnSmall.TLabel":          ("state-active",    None),
    "BitOffSmall.TLabel":         ("surface-field",   None),
}

# Toggle buttons for IO outputs: (fill token)
BUTTON_FILL_STYLES = {
    "BitOn.TButton":  "state-active",
    "BitOff.TButton": "surface-field",
}

# SECS/GEM state name -> style. Keep this next to your state enums; extend per site.
E90_STATE_STYLE = {
    "NotAcquired": "StateIdle.TLabel", "WaitingForHost": "StateIdle.TLabel",
    "InCarrier": "StateIdle.TLabel", "NeedsProcessing": "StateActive.TLabel",
    "InProcess": "StateActive.TLabel", "Processed": "StateDone.TLabel",
    "Aborted": "StateAttention.TLabel", "Stopped": "StateAttention.TLabel",
    "Rejected": "StateAttention.TLabel", "Lost": "StateAlarm.TLabel", "Skipped": "StateAlarm.TLabel",
}


def apply_theme(root: tk.Misc, tokens: dict | str | pathlib.Path) -> Theme:
    if not isinstance(tokens, dict):
        tokens = json.loads(pathlib.Path(tokens).read_text(encoding="utf-8"))
    th = Theme(tokens, root)
    c, f, px = th.color, th.font, th.px

    # Default named fonts, so plain tk widgets, entries and dialogs match too. Copy the
    # configured options (negative pixel size), not actual(): actual() reports whole points,
    # which turns 14px into 10pt and makes every default-font widget smaller than the mockup.
    label = f("label").configure()
    tkfont.nametofont("TkDefaultFont").configure(**label)
    tkfont.nametofont("TkTextFont").configure(**label)
    tkfont.nametofont("TkHeadingFont").configure(**{**label, "weight": "bold"})
    tkfont.nametofont("TkFixedFont").configure(**f("id").configure())
    root.configure(background=c("surface"))  # type: ignore[call-arg]

    s = ttk.Style(root)
    s.theme_use("clam")  # clam honours background/field/border colors on every platform
    edge = dict(bordercolor=c("line"), lightcolor=c("surface-raised"), darkcolor=c("surface-raised"))
    s.configure(".", background=c("surface"), foreground=c("ink"), font=f("label"),
                troughcolor=c("surface-raised"), focuscolor=c("focus-ring"),
                selectbackground=c("live"), selectforeground="#ffffff",
                insertcolor=c("ink"), **edge)
    s.map(".", foreground=[("disabled", c("ink-disabled"))])

    s.configure("TFrame", background=c("surface"))
    # A panel: raised ground, 1px line border, title inside (Panel.Section.TLabel), as in the
    # design system. ttk.Labelframe draws its title ON the border instead; prefer this.
    s.configure("Panel.TFrame", background=c("surface-raised"), relief="solid",
                borderwidth=px("border-thin"), bordercolor=c("line"),
                lightcolor=c("line"), darkcolor=c("line"))
    s.configure("PanelBody.TFrame", background=c("surface-raised"), borderwidth=0)
    s.configure("TLabelframe", background=c("surface-raised"), relief="solid", labelmargins=(0, 0, 0, px("space-1")),
                borderwidth=px("border-thin"), padding=px("space-3"))
    s.configure("TLabelframe.Label", background=c("surface"), foreground=c("ink"),
                font=f("section"))

    for name, (font_tok, fg_tok) in TEXT_STYLES.items():
        s.configure(name, font=f(font_tok), foreground=c(fg_tok))
        # labels inside a raised panel: same style with the panel ground
        s.configure(f"Panel.{name}", background=c("surface-raised"), font=f(font_tok),
                    foreground=c(fg_tok))

    pad = (px("space-2"), px("space-1"))
    for name, (fill_tok, text_tok) in FILL_STYLES.items():
        fg = c(text_tok) if text_tok else th.on(fill_tok)
        s.configure(name, background=c(fill_tok), foreground=fg,
                    padding=(px("space-1"), 0) if "Small" in name else pad,
                    relief="solid", borderwidth=px("border-thin"), bordercolor=c("outline"),
                    anchor="center")

    # Unacknowledged alarm: two visible shades of the border, text never blinks (see blink()).
    for name, (fill_tok, text_tok) in FILL_STYLES.items():
        if name.startswith("Alarm"):
            base = name.removesuffix(".TLabel")
            for phase, border in (("A", c("outline")), ("B", "#ffffff")):
                s.configure(f"{base}Unack{phase}.TLabel", background=c(fill_tok),
                            foreground=c(text_tok), padding=pad, relief="solid",
                            borderwidth=px("border-alarm"), bordercolor=border,
                            lightcolor=border, darkcolor=border,  # clam paints inner edges with these
                            anchor="center")

    s.configure("TButton", background=c("surface-raised"), padding=(px("space-3"), px("space-2")),
                **edge)
    s.map("TButton",
          background=[("disabled", c("surface")), ("pressed", c("surface-field")),
                      ("active", c("surface-field"))],
          bordercolor=[("focus", c("focus-ring"))])
    # same button with less horizontal padding, for dense command groups
    s.configure("Compact.TButton", padding=(px("space-2"), px("space-2")))

    for name, fill_tok in BUTTON_FILL_STYLES.items():
        fg = th.on(fill_tok)
        s.configure(name, background=c(fill_tok), foreground=fg, padding=(px("space-1"), px("space-1")),
                    width=3, anchor="center", **edge)
        s.map(name, background=[("disabled", c("surface")), ("active", c(fill_tok))],
              foreground=[("disabled", c("ink-disabled"))], bordercolor=[("focus", c("focus-ring"))])

    s.configure("Panel.TCheckbutton", background=c("surface-raised"), indicatorbackground=c("surface-field"))
    s.map("Panel.TCheckbutton", background=[("active", c("surface-raised"))])

    field = dict(fieldbackground=c("surface-field"), foreground=c("ink"),
                 bordercolor=c("line"), lightcolor=c("surface-field"), darkcolor=c("surface-field"))
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        s.configure(w, padding=px("space-1"), **field)
        s.map(w, bordercolor=[("focus", c("focus-ring"))],
              fieldbackground=[("disabled", c("surface")), ("readonly", c("surface-raised"))])
    s.configure("Setpoint.TEntry", foreground=c("setpoint"), bordercolor=c("setpoint"))
    s.configure("Setpoint.TSpinbox", foreground=c("setpoint"), bordercolor=c("setpoint"))

    s.configure("Treeview", background=c("surface-field"), fieldbackground=c("surface-field"),
                foreground=c("ink"), rowheight=f("label").metrics("linespace") + px("space-2"),
                **{k: v for k, v in edge.items()})
    s.configure("Treeview.Heading", background=c("surface-raised"), foreground=c("ink"),
                font=f("label"), relief="solid", borderwidth=px("border-thin"))
    s.map("Treeview", background=[("selected", c("live"))], foreground=[("selected", "#ffffff")])

    s.configure("TNotebook", background=c("surface"), bordercolor=c("line"))
    s.configure("TNotebook.Tab", background=c("surface"), padding=(px("space-3"), px("space-1")))
    s.map("TNotebook.Tab", background=[("selected", c("surface-raised"))])
    s.configure("TProgressbar", background=c("live"), troughcolor=c("surface-field"),
                bordercolor=c("line"), lightcolor=c("live"), darkcolor=c("live"))
    s.configure("TSeparator", background=c("line"))
    return th


# ---- Tier 2 helpers: things ttk cannot do natively -----------------------------------------

def blink(widget: ttk.Widget, styles: tuple[str, str], period_ms: int) -> Callable[[], None]:
    """Alternate two styles (two visible shades) until the returned stop() is called.
    Call on the Tk thread. stop() restores the first style."""
    state = {"i": 0, "id": None}

    def tick() -> None:
        state["i"] ^= 1
        try:
            widget.configure(style=styles[state["i"]])
            state["id"] = widget.after(period_ms // 2, tick)
        except tk.TclError:
            state["id"] = None  # widget destroyed

    def stop() -> None:
        if state["id"] is not None:
            widget.after_cancel(state["id"])
            state["id"] = None
        try:
            widget.configure(style=styles[0])
        except tk.TclError:
            pass

    state["id"] = widget.after(period_ms // 2, tick)
    return stop


def rounded_rect(canvas: tk.Canvas, x0: int, y0: int, x1: int, y1: int, r: int, **kw) -> int:
    """Rounded rectangle on a Canvas (radius-sm tiles, radius-pill pills with r = height/2)."""
    r = max(0, min(r, (x1 - x0) // 2, (y1 - y0) // 2))
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, **kw)

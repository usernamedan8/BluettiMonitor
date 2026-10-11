"""Modern Tkinter GUI for Bluetti Monitor on Linux.
Features real-time circular battery ring, power flow cards, rolling history graph,
all decoded readings, raw registers viewer, and live event log.
"""

import math
import os
import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Dict, List, Optional

from .protocol import FIELDS, BLOCKS, decode_registers, is_private

# Color Palette matching macOS app
BG_DARK = "#18181b"          # Zinc 900
BG_CARD = "#27272a"          # Zinc 800
BG_CARD_HOVER = "#3f3f46"    # Zinc 700
TEXT_WHITE = "#f4f4f5"       # Zinc 100
TEXT_MUTED = "#a1a1aa"       # Zinc 400
TEXT_DIM = "#71717a"         # Zinc 500
BORDER = "#3f3f46"           # Zinc 700

COLOR_SOLAR = "#f59e0b"      # Amber 500
COLOR_MAINS = "#3b82f6"      # Blue 500
COLOR_ACOUT = "#10b981"      # Emerald 500
COLOR_DCOUT = "#8b5cf6"      # Violet 500
COLOR_BAT_GOOD = "#22c55e"   # Green 500
COLOR_BAT_MED = "#eab308"    # Yellow 500
COLOR_BAT_LOW = "#ef4444"    # Red 500


class BatteryRingCanvas(tk.Canvas):
    """Circular gauge showing SOC percentage and charge state."""

    def __init__(self, parent, size=180, **kwargs):
        super().__init__(parent, width=size, height=size, bg=BG_CARD, highlightthickness=0, **kwargs)
        self.size = size
        self.center = size / 2
        self.radius = (size / 2) - 16
        self.soc = 0.0
        self.state_str = "Idle"
        self.draw()

    def update_value(self, soc: float, state_str: str):
        self.soc = max(0.0, min(100.0, soc))
        self.state_str = state_str
        self.draw()

    def draw(self):
        self.delete("all")
        cx, cy, r = self.center, self.center, self.radius

        # Background track
        self.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#374151", width=12)

        # Progress Arc
        if self.soc > 0:
            angle = (self.soc / 100.0) * 360.0
            color = COLOR_BAT_GOOD if self.soc > 30 else (COLOR_BAT_MED if self.soc > 15 else COLOR_BAT_LOW)
            self.create_arc(
                cx - r, cy - r, cx + r, cy + r,
                start=90, extent=-angle,
                outline=color, width=12, style="arc"
            )

        # Center Text (Percentage)
        self.create_text(
            cx, cy - 10,
            text=f"{int(self.soc)}%",
            fill=TEXT_WHITE,
            font=("Helvetica", 24, "bold")
        )

        # Center Subtext (Status)
        self.create_text(
            cx, cy + 18,
            text=self.state_str,
            fill=TEXT_MUTED,
            font=("Helvetica", 10, "bold")
        )


class HistoryGraph(tk.Canvas):
    """Rolling telemetry chart plotting recent power flow trends."""

    def __init__(self, parent, width=440, height=130, **kwargs):
        super().__init__(parent, width=width, height=height, bg=BG_CARD, highlightthickness=0, **kwargs)
        self.w = width
        self.h = height
        self.samples = []  # List of (time, solar, ac_out, soc)
        self.draw_grid()

    def add_sample(self, solar: float, ac_out: float, soc: float):
        self.samples.append((time.time(), solar, ac_out, soc))
        if len(self.samples) > 60:
            self.samples.pop(0)
        self.redraw()

    def draw_grid(self):
        self.delete("all")
        # Grid lines
        for y in (30, 65, 100):
            self.create_line(10, y, self.w - 10, y, fill="#374151", dash=(2, 4))
        self.create_text(25, 12, text="Trend (Last 60 readings)", fill=TEXT_MUTED, font=("Helvetica", 8))
        self.create_text(self.w - 110, 12, text="■ Solar  ■ AC Out", fill=TEXT_MUTED, font=("Helvetica", 8))

    def redraw(self):
        self.draw_grid()
        if len(self.samples) < 2:
            return

        max_val = max(max(s[1], s[2], 100.0) for s in self.samples) * 1.15
        dx = (self.w - 40) / max(1, len(self.samples) - 1)

        # Draw Solar curve
        pts_solar = []
        pts_ac = []
        for i, s in enumerate(self.samples):
            x = 20 + i * dx
            y_solar = self.h - 15 - (s[1] / max_val) * (self.h - 35)
            y_ac = self.h - 15 - (s[2] / max_val) * (self.h - 35)
            pts_solar.extend([x, y_solar])
            pts_ac.extend([x, y_ac])

        if len(pts_solar) >= 4:
            self.create_line(*pts_solar, fill=COLOR_SOLAR, width=2, smooth=True)
        if len(pts_ac) >= 4:
            self.create_line(*pts_ac, fill=COLOR_ACOUT, width=2, smooth=True)


class BluettiApp(tk.Tk):
    """Main Application Window."""

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.title("Bluetti Monitor")
        self.geometry("980x680")
        self.minsize(860, 580)
        self.configure(bg=BG_DARK)

        self.telemetry_history = []
        self.current_registers = {}
        self.current_decoded = {}

        self._setup_styles()
        self._build_header()
        self._build_tabs()
        self._build_statusbar()

        # Start periodic queue polling (main.py starts live or demo mode)
        self.after(100, self._process_events)

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(".", background=BG_DARK, foreground=TEXT_WHITE, font=("Helvetica", 10))
        style.configure("TNotebook", background=BG_DARK, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG_CARD, foreground=TEXT_MUTED, padding=[16, 8], font=("Helvetica", 10, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", "#3f3f46")],
                  foreground=[("selected", TEXT_WHITE)])

        style.configure("Card.TFrame", background=BG_CARD, relief="flat")
        style.configure("Treeview",
                        background=BG_CARD,
                        foreground=TEXT_WHITE,
                        fieldbackground=BG_CARD,
                        rowheight=26,
                        bordercolor=BORDER,
                        borderwidth=0)
        style.map("Treeview", background=[("selected", "#4f46e5")], foreground=[("selected", TEXT_WHITE)])
        style.configure("Treeview.Heading", background=BG_CARD_HOVER, foreground=TEXT_WHITE, font=("Helvetica", 9, "bold"))

    def _build_header(self):
        header = tk.Frame(self, bg=BG_DARK, height=56)
        header.pack(fill="x", padx=16, pady=(12, 6))

        # Title & Icon
        title_box = tk.Frame(header, bg=BG_DARK)
        title_box.pack(side="left")

        title_lbl = tk.Label(title_box, text="⚡ Bluetti Monitor", font=("Helvetica", 16, "bold"), fg=TEXT_WHITE, bg=BG_DARK)
        title_lbl.pack(side="left")

        self.status_pill = tk.Label(
            title_box, text="Demo Mode", font=("Helvetica", 9, "bold"),
            bg="#0284c7", fg=TEXT_WHITE, padx=8, pady=2
        )
        self.status_pill.pack(side="left", padx=12)

        # Controls right
        ctrl_box = tk.Frame(header, bg=BG_DARK)
        ctrl_box.pack(side="right")

        # Demo toggle button
        self.demo_btn = tk.Button(
            ctrl_box, text="Switch to Demo Mode", font=("Helvetica", 9),
            bg=BG_CARD, fg=TEXT_WHITE, activebackground=BG_CARD_HOVER,
            command=self._toggle_demo_live, relief="flat", padx=8, pady=4
        )
        self.demo_btn.pack(side="left", padx=6)

        # Pause button (free Bluetooth)
        self.pause_btn = tk.Button(
            ctrl_box, text="Pause (Free BLE)", font=("Helvetica", 9),
            bg=BG_CARD, fg=TEXT_WHITE, activebackground=BG_CARD_HOVER,
            command=self._toggle_pause, relief="flat", padx=8, pady=4
        )
        self.pause_btn.pack(side="left", padx=6)

        # Poll interval dropdown
        tk.Label(ctrl_box, text="Poll:", fg=TEXT_MUTED, bg=BG_DARK, font=("Helvetica", 9)).pack(side="left", padx=(8, 2))
        self.poll_var = tk.StringVar(value="3s")
        poll_combo = ttk.Combobox(ctrl_box, textvariable=self.poll_var, values=["1s", "2s", "3s", "5s", "10s", "30s"], width=4, state="readonly")
        poll_combo.pack(side="left", padx=4)
        poll_combo.bind("<<ComboboxSelected>>", self._on_poll_change)

    def _build_tabs(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=6)

        # Tab 1: Overview
        self.tab_overview = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(self.tab_overview, text="Overview")
        self._build_tab_overview()

        # Tab 2: All Readings
        self.tab_readings = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(self.tab_readings, text="All Readings")
        self._build_tab_readings()

        # Tab 3: Registers
        self.tab_registers = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(self.tab_registers, text="Registers")
        self._build_tab_registers()

        # Tab 4: Log
        self.tab_log = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(self.tab_log, text="Event Log")
        self._build_tab_log()

    def _build_tab_overview(self):
        # Two columns layout
        col_left = tk.Frame(self.tab_overview, bg=BG_DARK, width=280)
        col_left.pack(side="left", fill="y", padx=(0, 10), pady=8)

        # Battery Card (Left)
        bat_card = tk.Frame(col_left, bg=BG_CARD, padx=16, pady=16)
        bat_card.pack(fill="x", pady=(0, 10))

        tk.Label(bat_card, text="Battery Storage", font=("Helvetica", 11, "bold"), fg=TEXT_WHITE, bg=BG_CARD).pack(anchor="w")

        self.bat_gauge = BatteryRingCanvas(bat_card, size=170)
        self.bat_gauge.pack(pady=10)

        # Battery Stats Grid
        stats_frame = tk.Frame(bat_card, bg=BG_CARD)
        stats_frame.pack(fill="x", pady=4)

        self.lbl_pack_v = self._add_stat_row(stats_frame, "Voltage", "— V")
        self.lbl_pack_a = self._add_stat_row(stats_frame, "Current", "— A")
        self.lbl_pack_soh = self._add_stat_row(stats_frame, "Health (SOH)", "— %")
        self.lbl_pack_temp = self._add_stat_row(stats_frame, "Temperature", "— °C")
        self.lbl_time_rem = self._add_stat_row(stats_frame, "Est. Time", "—")

        # Right Column (Power flows + Graphs)
        col_right = tk.Frame(self.tab_overview, bg=BG_DARK)
        col_right.pack(side="right", fill="both", expand=True, pady=8)

        # 4 Power Cards (Grid 2x2)
        flows_grid = tk.Frame(col_right, bg=BG_DARK)
        flows_grid.pack(fill="x", pady=(0, 10))
        flows_grid.columnconfigure(0, weight=1)
        flows_grid.columnconfigure(1, weight=1)

        self.card_solar, self.lbl_solar_val = self._create_power_card(flows_grid, 0, 0, "☀️ Solar Input", COLOR_SOLAR)
        self.card_mains, self.lbl_mains_val = self._create_power_card(flows_grid, 0, 1, "🔌 Mains Input", COLOR_MAINS)
        self.card_acout, self.lbl_acout_val = self._create_power_card(flows_grid, 1, 0, "⚡ AC Output", COLOR_ACOUT)
        self.card_dcout, self.lbl_dcout_val = self._create_power_card(flows_grid, 1, 1, "🔋 DC Output", COLOR_DCOUT)

        # Rolling history graph
        graph_card = tk.Frame(col_right, bg=BG_CARD, padx=12, pady=10)
        graph_card.pack(fill="both", expand=True)

        self.history_graph = HistoryGraph(graph_card)
        self.history_graph.pack(fill="both", expand=True)

    def _create_power_card(self, parent, row, col, title, accent_color):
        card = tk.Frame(parent, bg=BG_CARD, padx=16, pady=12)
        card.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")

        tk.Label(card, text=title, font=("Helvetica", 10, "bold"), fg=TEXT_MUTED, bg=BG_CARD).pack(anchor="w")
        val_lbl = tk.Label(card, text="0 W", font=("Helvetica", 20, "bold"), fg=accent_color, bg=BG_CARD)
        val_lbl.pack(anchor="w", pady=(6, 2))
        return card, val_lbl

    def _add_stat_row(self, parent, label, default):
        row = tk.Frame(parent, bg=BG_CARD)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, font=("Helvetica", 9), fg=TEXT_MUTED, bg=BG_CARD).pack(side="left")
        val = tk.Label(row, text=default, font=("Helvetica", 9, "bold"), fg=TEXT_WHITE, bg=BG_CARD)
        val.pack(side="right")
        return val

    def _build_tab_readings(self):
        # Search filter bar
        top_bar = tk.Frame(self.tab_readings, bg=BG_DARK, pady=6)
        top_bar.pack(fill="x")

        tk.Label(top_bar, text="Search fields:", fg=TEXT_MUTED, bg=BG_DARK).pack(side="left", padx=(0, 8))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self._filter_readings())
        search_entry = tk.Entry(top_bar, textvariable=self.search_var, bg=BG_CARD, fg=TEXT_WHITE, insertbackground=TEXT_WHITE, width=32)
        search_entry.pack(side="left")

        # Readings Treeview
        cols = ("group", "label", "register", "value", "unit")
        self.tree_readings = ttk.Treeview(self.tab_readings, columns=cols, show="headings")
        self.tree_readings.heading("group", text="Category")
        self.tree_readings.heading("label", text="Field Name")
        self.tree_readings.heading("register", text="Register")
        self.tree_readings.heading("value", text="Current Value")
        self.tree_readings.heading("unit", text="Unit")

        self.tree_readings.column("group", width=120)
        self.tree_readings.column("label", width=220)
        self.tree_readings.column("register", width=80, anchor="center")
        self.tree_readings.column("value", width=140, anchor="e")
        self.tree_readings.column("unit", width=60)

        self.tree_readings.pack(fill="both", expand=True, pady=6)

    def _build_tab_registers(self):
        top_bar = tk.Frame(self.tab_registers, bg=BG_DARK, pady=6)
        top_bar.pack(fill="x")

        tk.Label(top_bar, text="Register Block:", fg=TEXT_MUTED, bg=BG_DARK).pack(side="left", padx=(0, 8))
        self.block_var = tk.StringVar(value="Home data (100–170)")
        block_names = [f"{b['name']} ({b['start']}–{b['start']+b['count']-1})" for b in BLOCKS]
        self.block_combo = ttk.Combobox(top_bar, textvariable=self.block_var, values=block_names, width=32, state="readonly")
        self.block_combo.pack(side="left", padx=4)
        self.block_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_registers_view())

        cols = ("reg", "hex", "dec", "name")
        self.tree_regs = ttk.Treeview(self.tab_registers, columns=cols, show="headings")
        self.tree_regs.heading("reg", text="Address")
        self.tree_regs.heading("hex", text="Hex")
        self.tree_regs.heading("dec", text="Decimal")
        self.tree_regs.heading("name", text="Description / Mapping")

        self.tree_regs.column("reg", width=90, anchor="center")
        self.tree_regs.column("hex", width=110, anchor="center")
        self.tree_regs.column("dec", width=110, anchor="center")
        self.tree_regs.column("name", width=340)

        self.tree_regs.pack(fill="both", expand=True, pady=6)

    def _build_tab_log(self):
        ctrls = tk.Frame(self.tab_log, bg=BG_DARK, pady=4)
        ctrls.pack(fill="x")

        tk.Button(ctrls, text="Clear Log", bg=BG_CARD, fg=TEXT_WHITE, command=self._clear_log, relief="flat", padx=8).pack(side="left", padx=4)
        tk.Button(ctrls, text="Export Log...", bg=BG_CARD, fg=TEXT_WHITE, command=self._export_log, relief="flat", padx=8).pack(side="left", padx=4)

        self.log_text = tk.Text(self.tab_log, bg=BG_CARD, fg=TEXT_WHITE, font=("Courier", 9), relief="flat", padx=8, pady=8)
        self.log_text.pack(fill="both", expand=True, pady=6)

    def _build_statusbar(self):
        status_bar = tk.Frame(self, bg=BG_CARD, height=28)
        status_bar.pack(fill="x", side="bottom")

        self.lbl_device_info = tk.Label(status_bar, text="Device: Disconnected", bg=BG_CARD, fg=TEXT_MUTED, font=("Helvetica", 9))
        self.lbl_device_info.pack(side="left", padx=12)

        self.lbl_sec_info = tk.Label(status_bar, text="Security: Ready", bg=BG_CARD, fg=TEXT_MUTED, font=("Helvetica", 9))
        self.lbl_sec_info.pack(side="left", padx=12)

        self.lbl_last_update = tk.Label(status_bar, text="Last update: —", bg=BG_CARD, fg=TEXT_MUTED, font=("Helvetica", 9))
        self.lbl_last_update.pack(side="right", padx=12)

    def _toggle_demo_live(self):
        if self.engine.demo_mode:
            self.engine.start_live()
            self.demo_btn.config(text="Switch to Demo Mode")
        else:
            self.engine.start_demo()
            self.demo_btn.config(text="Switch to Live BLE")

    def _toggle_pause(self):
        paused = not self.engine.paused
        self.engine.set_paused(paused)
        self.pause_btn.config(text="Resume" if paused else "Pause (Free BLE)")

    def _on_poll_change(self, event=None):
        val_str = self.poll_var.get().replace("s", "")
        try:
            sec = float(val_str)
            self.engine.set_poll_interval(sec)
        except ValueError:
            pass

    def _clear_log(self):
        self.log_text.delete("1.0", tk.END)

    def _export_log(self):
        fn = filedialog.asksaveasfilename(defaultextension=".log", filetypes=[("Log files", "*.log")])
        if fn:
            with open(fn, "w") as f:
                f.write(self.log_text.get("1.0", tk.END))
            messagebox.showinfo("Exported", f"Log saved to {fn}")

    def _process_events(self):
        """Processes events queued by the background engine thread."""
        while not self.engine.queue.empty():
            try:
                ev = self.engine.queue.get_nowait()
                ev_type = ev["type"]
                data = ev["data"]

                if ev_type == "log":
                    ts = time.strftime("%H:%M:%S", time.localtime(ev["time"]))
                    self.log_text.insert(tk.END, f"[{ts}] {data}\n")
                    self.log_text.see(tk.END)

                elif ev_type == "phase":
                    self.status_pill.config(text=data)
                    if "Live" in data:
                        self.status_pill.config(bg="#16a34a")
                    elif "Demo" in data:
                        self.status_pill.config(bg="#0284c7")
                    elif "Paused" in data:
                        self.status_pill.config(bg="#71717a")
                    else:
                        self.status_pill.config(bg="#d97706")

                elif ev_type == "device_info":
                    name = data.get("name", "Unknown")
                    addr = data.get("address", "")
                    self.lbl_device_info.config(text=f"Device: {name} ({addr})")

                elif ev_type == "security":
                    if data.get("encrypted"):
                        sig = "station verified" if data.get("signature") else "station NOT verified"
                        self.lbl_sec_info.config(text=f"Security: encrypted, {sig}")
                    else:
                        self.lbl_sec_info.config(text="Security: unencrypted link")

                elif ev_type == "telemetry":
                    self.current_registers = data.get("registers", {})
                    self.current_decoded = data.get("decoded", {})
                    self._update_telemetry_ui(data)

            except queue.Empty:
                break

        self.after(100, self._process_events)

    def _update_telemetry_ui(self, data: dict):
        soc = data.get("soc", 0.0)
        pv = data.get("pvInPower", 0.0)
        grid = data.get("gridInPower", 0.0)
        ac_out = data.get("acOutPower", 0.0)
        dc_out = data.get("dcOutPower", 0.0)

        # Update Battery Ring
        state_str = self.current_decoded.get("chargeState", "Idle")
        self.bat_gauge.update_value(soc, state_str)

        # Update Power Cards
        self.lbl_solar_val.config(text=f"{int(pv)} W")
        self.lbl_mains_val.config(text=f"{int(grid)} W")
        self.lbl_acout_val.config(text=f"{int(ac_out)} W")
        self.lbl_dcout_val.config(text=f"{int(dc_out)} W")

        # Battery details
        pack_v = data.get("packVoltage", 0.0)
        pack_a = data.get("packCurrent", 0.0)
        self.lbl_pack_v.config(text=f"{pack_v:.2f} V")
        self.lbl_pack_a.config(text=f"{pack_a:.1f} A")

        soh = self.current_decoded.get("packSoh", 99)
        self.lbl_pack_soh.config(text=f"{soh}%")

        temp = self.current_decoded.get("packTemp", 25)
        self.lbl_pack_temp.config(text=f"{temp} °C")

        time_full = self.current_decoded.get("timeToFull")
        time_empty = self.current_decoded.get("timeToEmpty")
        if time_full and time_full != "—":
            self.lbl_time_rem.config(text=f"{time_full} to full")
        elif time_empty and time_empty != "—":
            self.lbl_time_rem.config(text=f"{time_empty} to empty")
        else:
            self.lbl_time_rem.config(text="Standby")

        # History Graph
        self.history_graph.add_sample(pv, ac_out, soc)

        # Status bar
        self.lbl_last_update.config(text=f"Last update: {time.strftime('%H:%M:%S')}")

        # Update table tabs if active
        cur_tab = self.notebook.index("current")
        if cur_tab == 1:
            self._filter_readings()
        elif cur_tab == 2:
            self._refresh_registers_view()

    def _filter_readings(self):
        query = self.search_var.get().lower()
        self.tree_readings.delete(*self.tree_readings.get_children())

        for f in FIELDS:
            k = f["key"]
            label = f["label"]
            group = f["group"]
            reg = f["reg"]
            unit = f.get("unit", "")
            val = self.current_decoded.get(k, "—")

            if query and (query not in label.lower() and query not in group.lower() and query not in str(reg)):
                continue

            self.tree_readings.insert("", tk.END, values=(group, label, str(reg), str(val), unit))

    def _refresh_registers_view(self):
        self.tree_regs.delete(*self.tree_regs.get_children())
        selected_idx = self.block_combo.current()
        if selected_idx < 0 or selected_idx >= len(BLOCKS):
            selected_idx = 0
        blk = BLOCKS[selected_idx]

        start = blk["start"]
        count = blk["count"]

        # Map register to field name
        field_names = {f["reg"]: f["label"] for f in FIELDS}

        for r in range(start, start + count):
            if is_private(r):
                self.tree_regs.insert("", tk.END, values=(str(r), "hidden", "hidden", "Bluetooth password"))
                continue
            v = self.current_registers.get(r, 0)
            hex_str = f"0x{v:04X}"
            desc = field_names.get(r, "")
            self.tree_regs.insert("", tk.END, values=(str(r), hex_str, str(v), desc))

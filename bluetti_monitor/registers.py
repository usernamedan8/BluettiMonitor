"""Register map for BLUETTI "V2" stations (Elite 200 V2, Elite 30 V2, …).

Python port of the macOS app's RegisterMap.swift. Sources: bluetti-bt-lib device
files, nhurman/bluetti_mqtt, and raw data read from a real Elite 200 V2 and
Elite 30 V2 (Oct 2026). Fields marked tentative come from other V2 models and
aren't confirmed on the Elite 200 V2 yet.
"""

import re
from typing import Any, Dict, List, Optional

GROUPS = ["Battery", "Power in / out", "Mains input", "AC output", "DC outputs",
          "Solar input", "Energy totals", "Status", "Settings", "Device"]

BLOCKS = [
    {"name": "Home data", "start": 100, "count": 71, "fast": True},
    {"name": "Inverter PV", "start": 1200, "count": 50, "fast": True},
    {"name": "Inverter grid", "start": 1300, "count": 31, "fast": True},
    {"name": "Inverter load", "start": 1400, "count": 48, "fast": True},
    {"name": "Inverter output", "start": 1500, "count": 30, "fast": True},
    {"name": "Battery pack", "start": 6000, "count": 31, "fast": True},
    {"name": "Base config", "start": 1, "count": 31, "fast": False},
    {"name": "Inverter base info", "start": 1100, "count": 50, "fast": False},
    {"name": "Settings", "start": 2000, "count": 70, "fast": False},
    {"name": "Advanced settings", "start": 2200, "count": 60, "fast": False},
    {"name": "Grid limits", "start": 2400, "count": 40, "fast": False},
    {"name": "Fault history", "start": 3000, "count": 40, "fast": False},
    {"name": "Energy totals", "start": 3500, "count": 40, "fast": False},
    {"name": "This year's energy", "start": 3600, "count": 40, "fast": False},
    {"name": "Pack details", "start": 6100, "count": 90, "fast": False},
    {"name": "Cell data", "start": 6300, "count": 40, "fast": False},
    {"name": "Pack settings", "start": 7000, "count": 40, "fast": False},
    {"name": "IoT info", "start": 11000, "count": 40, "fast": False},
    {"name": "IoT settings", "start": 12000, "count": 40, "fast": False},
    {"name": "Display (HMI)", "start": 14000, "count": 40, "fast": False},
]

# The Elite 200 V2 fills unused registers with 404: "not available".
NOT_AVAILABLE = 404

# Secrets: Bluetooth pairing password (6–14) and the Wi-Fi PASSWORD in plain
# text (12018–12033, next to the SSID). Never shown, logged or saved.
PRIVATE_RANGES = [range(6, 15), range(12018, 12034)]


def is_private(register: int) -> bool:
    return any(register in r for r in PRIVATE_RANGES)


CHARGING_MODES = {0: "Standard", 1: "Silent", 2: "Turbo", 4: "Custom"}
ECO_HOURS = {1: "1 h", 2: "2 h", 3: "3 h", 4: "4 h"}
DISPLAY_TIMEOUTS = {2: "30 s", 3: "1 min", 4: "5 min", 5: "Never"}
CHARGE_STATES = {0: "Idle", 1: "Charging", 2: "Discharging"}


def F(key, label, group, reg, kind, div=1, unit="", offset=0, valid=None, tentative=False, **extra):
    d = {"key": key, "label": label, "group": group, "reg": reg, "kind": kind, "div": div,
         "unit": unit, "offset": offset, "valid": valid, "tentative": tentative}
    d.update(extra)
    return d


B, P, M, A, D, S, E, ST, SE, DV = GROUPS

FIELDS: List[Dict[str, Any]] = [
    # Home data (100+)
    F("soc", "Battery level", B, 102, "u16", 1, "%"),
    F("packVoltage", "Battery voltage", B, 100, "u16", 100, "V"),
    F("packCurrent", "Battery current", B, 101, "u16", 10, "A"),
    F("chargeState", "Charge state", B, 103, "choice", choices=CHARGE_STATES, tentative=True),
    F("timeToFull", "Time to full", B, 104, "minutes", tentative=True),
    F("timeToEmpty", "Time to empty", B, 105, "minutes", tentative=True),
    F("packCount", "Battery packs", B, 107, "u8l"),
    F("packsOnline", "Packs online (bitmap)", B, 108, "hex", tentative=True),
    F("canFaults", "CAN bus faults (bitmap)", ST, 109, "hex", tentative=True),
    F("model", "Model", DV, 110, "string", span=6),
    F("serial", "Serial number", DV, 116, "serial"),
    F("inverterCount", "Inverters", DV, 120, "u8l", tentative=True),
    F("inverterPowerType", "Inverter power type", DV, 122, "u8l", tentative=True),
    F("energyLines", "Energy flows (screen arrows)", ST, 123, "flags", tentative=True, names=[
        "Solar → battery", "Mains → battery", "Battery → mains", "AC load", "DC load",
        "Battery → inverter", "Inverter → battery", "Mains → load", "Solar icon", "Mains icon",
        "AC load icon", "Solar → mains", "Solar → AC load", "Battery → AC load"]),
    F("ctrlStatus", "Enabled functions", ST, 124, "flags", tentative=True, names=[
        "Power", "AC output", "DC output", "Inverter", "Mains", "Solar", "Feed-in",
        "Meter", "Light", "ECO", "Power lifting"]),
    F("dcOutPower", "DC output", P, 140, "u32", 1, "W"),
    F("acOutPower", "AC output", P, 142, "u32", 1, "W"),
    F("pvInPower", "Solar input", P, 144, "u32", 1, "W"),
    F("gridInPower", "Mains input", P, 146, "u32", 1, "W"),
    F("inverterPower", "Inverter power", P, 148, "u32", 1, "W", tentative=True),
    F("dcOutEnergy", "DC output, lifetime", E, 150, "u32", 10, "kWh", tentative=True),
    F("acOutEnergy", "AC output, lifetime", E, 152, "u32", 10, "kWh", tentative=True),
    F("pvEnergy", "Solar charged, lifetime", E, 154, "u32", 10, "kWh", tentative=True),
    F("gridEnergy", "Mains charged, lifetime", E, 156, "u32", 10, "kWh", tentative=True),
    F("feedEnergy", "Fed back to mains, lifetime", E, 158, "u32", 10, "kWh", tentative=True),
    F("chargingModeHome", "Charging mode", ST, 160, "choice", choices=CHARGING_MODES, tentative=True),
    F("inverterStatus", "Inverter status code", ST, 161, "u8l", tentative=True),
    F("selfSufficiency", "Self-sufficiency", E, 164, "u8l", 1, "%", valid=(0, 100), tentative=True),
    F("ratedVoltage", "Rated voltage", DV, 169, "u16", 1, "V", tentative=True),
    F("ratedFrequency", "Rated frequency", DV, 170, "u16", 1, "Hz", tentative=True),

    # Solar (1200+)
    F("pvGeneration", "Solar generated", S, 1202, "u16", 10, "kWh", tentative=True),
    F("pv1Power", "Solar 1 power", S, 1212, "u16", 1, "W"),
    F("pv1Voltage", "Solar 1 voltage", S, 1213, "u16", 10, "V"),
    F("pv1Current", "Solar 1 current", S, 1214, "u16", 10, "A"),
    F("pv2Power", "Solar 2 power", S, 1220, "u16", 1, "W", tentative=True),
    F("pv2Voltage", "Solar 2 voltage", S, 1221, "u16", 10, "V", tentative=True),
    F("pv2Current", "Solar 2 current", S, 1222, "u16", 10, "A", tentative=True),

    # Mains (1300+)
    F("gridFrequency", "Mains frequency", M, 1300, "u16", 10, "Hz"),
    F("gridPower", "Mains power", M, 1313, "u16", 1, "W"),
    F("gridVoltage", "Mains voltage", M, 1314, "u16", 10, "V"),
    F("gridCurrent", "Mains current", M, 1315, "u16", 10, "A"),
    F("gridChargeEnergy", "Mains charged, lifetime", M, 1303, "u32", 10, "kWh", tentative=True),
    F("gridFeedEnergy", "Fed back, lifetime", M, 1305, "u32", 10, "kWh", tentative=True),

    # Loads (1400+)
    F("dcLoadPower", "DC load total", D, 1400, "u32", 1, "W", tentative=True),
    F("dcLoadEnergy", "DC load, lifetime", D, 1402, "u32", 10, "kWh", tentative=True),
    F("usbPower", "USB (5 V) power", D, 1404, "u16", 1, "W", tentative=True),
    F("usbCurrent", "USB (5 V) current", D, 1405, "u16", 10, "A", tentative=True),
    F("dc12Power", "12 V power", D, 1406, "u16", 1, "W", tentative=True),
    F("dc12Current", "12 V current", D, 1407, "u16", 10, "A", tentative=True),
    F("dc24Power", "24 V power", D, 1408, "u16", 1, "W", tentative=True),
    F("dc24Current", "24 V current", D, 1409, "u16", 10, "A", tentative=True),
    F("acLoadPower", "AC load power", A, 1430, "u16", 1, "W", tentative=True),
    F("acLoadVoltage", "AC load voltage", A, 1431, "u16", 10, "V", tentative=True),
    F("acLoadCurrent", "AC load current", A, 1432, "u16", 10, "A", tentative=True),

    # Inverter output (1500+)
    F("acOutFrequency", "AC output frequency", A, 1500, "u16", 10, "Hz"),
    F("acOutOn", "Inverter running", A, 1509, "onoff", tentative=True),
    F("inverterP1Power", "Inverter power", A, 1510, "u16", 1, "W"),
    F("acOutVoltage", "AC output voltage", A, 1511, "u16", 10, "V"),
    F("inverterCurrent", "Inverter current", A, 1512, "u16", 10, "A"),

    # Settings (2000+), read-only
    F("setAC", "AC output switch", SE, 2011, "onoff"),
    F("setDC", "DC output switch", SE, 2012, "onoff"),
    F("ecoDC", "DC ECO mode", SE, 2014, "onoff"),
    F("ecoDCHours", "DC ECO shut-off after", SE, 2015, "choice", choices=ECO_HOURS),
    F("ecoDCMin", "DC ECO threshold", SE, 2016, "u16", 1, "W"),
    F("ecoAC", "AC ECO mode", SE, 2017, "onoff"),
    F("ecoACHours", "AC ECO shut-off after", SE, 2018, "choice", choices=ECO_HOURS),
    F("ecoACMin", "AC ECO threshold", SE, 2019, "u16", 1, "W"),
    F("chargingMode", "Charging mode", SE, 2020, "choice", choices=CHARGING_MODES),
    F("powerLifting", "Power lifting", SE, 2021, "onoff"),
    F("socStart", "Battery range, low", SE, 2022, "u16", 1, "%"),
    F("socEnd", "Battery range, high", SE, 2023, "u16", 1, "%"),
    F("displayTimeout", "Screen timeout", SE, 2067, "choice", choices=DISPLAY_TIMEOUTS),
    F("gridVoltMin", "Mains voltage, min", SE, 2435, "u16", 10, "V", tentative=True),
    F("gridVoltMax", "Mains voltage, max", SE, 2436, "u16", 10, "V", tentative=True),
    F("gridFreqMin", "Mains frequency, min", SE, 2437, "u16", 100, "Hz", tentative=True),
    F("gridFreqMax", "Mains frequency, max", SE, 2438, "u16", 100, "Hz", tentative=True),

    # Battery pack (6000+)
    F("packSoh", "Battery health (SOH)", B, 6006, "u8l", 1, "%"),
    F("packTemp", "Battery temperature", B, 6115, "u16", 1, "°C", offset=-40, valid=(-40, 100), tentative=True),
    F("cellMinV", "Lowest cell voltage", B, 6116, "u16", 1000, "V", valid=(1, 5)),
    F("cellMaxV", "Highest cell voltage", B, 6117, "u16", 1000, "V", valid=(1, 5)),
    F("cellMinT", "Coolest cell", B, 6120, "u16", 1, "°C", offset=-40, valid=(-40, 100)),
    F("cellMaxT", "Warmest cell", B, 6121, "u16", 1, "°C", offset=-40, valid=(-40, 100)),
    F("packMaxChgV", "Max charge voltage", B, 6010, "u16", 10, "V", valid=(10, 100), tentative=True),
    F("packMaxChgA", "Charge current limit", B, 6011, "u16", 10, "A", tentative=True),
    F("packMaxDsgA", "Discharge current limit", B, 6012, "u16", 10, "A", tentative=True),
    F("packType", "Pack type", DV, 6101, "string", span=6, tentative=True),
    F("packSerial", "Pack serial", DV, 6107, "serial", tentative=True),
    F("verARM", "Controller firmware", DV, 1114, "version", tentative=True),
    F("verDSP", "Inverter firmware", DV, 1117, "version", tentative=True),
    F("verBCU", "BMS firmware", DV, 6175, "version"),
    F("verIoT", "Wi-Fi module firmware", DV, 11014, "version", tentative=True),
    F("verBMU", "BMU firmware", DV, 6178, "version", valid=(0.01, 99999), tentative=True),
    F("verSafety", "Safety module firmware", DV, 6181, "version", valid=(0.01, 99999), tentative=True),
    F("verHV", "HV module firmware", DV, 6184, "version", valid=(0.01, 99999), tentative=True),

    # Base config (1+)
    F("modbusVersion", "Modbus version", DV, 15, "u16", tentative=True),
    F("protocolVersion", "Protocol version", DV, 16, "u16", tentative=True),

    # IoT (12000+)
    F("wifiName", "Wi-Fi network", DV, 12002, "string", span=16),
]

FIELDS_BY_KEY = {f["key"]: f for f in FIELDS}


def _span(f) -> int:
    k = f["kind"]
    if k in ("u32", "version"):
        return 2
    if k == "serial":
        return 4
    if k == "string":
        return f.get("span", 6)
    return 1


FIELD_FOR_REGISTER: Dict[int, Dict[str, Any]] = {}
for _f in FIELDS:
    for _i in range(_span(_f)):
        FIELD_FOR_REGISTER.setdefault(_f["reg"] + _i, _f)


def decode(f, regs: Dict[int, int]):
    """Decoded value: float, str, list[str] (flags), bool — or None if absent/unavailable."""
    r, k = f["reg"], f["kind"]
    v = regs.get(r)
    if v is None:
        return None
    if k in ("u8l", "u8h", "u16", "s16", "minutes", "choice", "onoff") and v == NOT_AVAILABLE:
        return None
    if k == "u32" and v == NOT_AVAILABLE and regs.get(r + 1, 0) == 0:
        return None

    if k in ("u8l", "u8h", "u16", "s16", "minutes"):
        raw = (v & 0xFF) if k == "u8l" else (v >> 8) if k == "u8h" else \
              (v - 0x10000 if v >= 0x8000 else v) if k == "s16" else v
        out = raw / f["div"] + f["offset"]
    elif k == "u32":
        if r + 1 not in regs:
            return None
        out = ((regs[r + 1] << 16) | v) / f["div"] + f["offset"]
    elif k == "version":
        if r + 1 not in regs:
            return None
        out = ((regs[r + 1] << 16) | v) / 100
    elif k == "serial":
        words = [regs.get(r + i) for i in range(4)]
        if None in words:
            return None
        return str(sum(w << (16 * i) for i, w in enumerate(words)))
    elif k == "string":
        b = bytearray()
        for i in range(f.get("span", 6)):
            w = regs.get(r + i)
            if w is None:
                return None
            b += bytes([w & 0xFF, w >> 8])           # bytes swapped within each register
        b = b.split(b"\0")[0]
        return "".join(chr(c) for c in b if 0x20 <= c < 0x7F)
    elif k == "flags":
        return [n for i, n in enumerate(f["names"]) if v & (1 << i)]
    elif k == "choice":
        return f["choices"].get(v, f"Code {v}")
    elif k == "onoff":
        return v != 0
    elif k == "hex":
        return f"0x{v:04X}"
    else:
        return None

    valid = f.get("valid")
    if valid and not (valid[0] <= out <= valid[1]):
        return None
    return out


def format_minutes(m: int) -> str:
    if m <= 0:
        return "—"
    d, h, mi = m // 1440, (m % 1440) // 60, m % 60
    if d:
        return f"{d} d {h} h"
    return f"{h} h {mi:02d} m" if h else f"{mi} m"


def fmt(f, regs) -> Optional[str]:
    """Human-readable value with unit, or None if the station didn't report it."""
    v = decode(f, regs)
    if v is None:
        return None
    k = f["kind"]
    if isinstance(v, bool):
        return "On" if v else "Off"
    if isinstance(v, list):
        return ", ".join(v) if v else "None"
    if isinstance(v, str):
        return v or "—"
    if k == "minutes":
        return format_minutes(int(v))
    if k == "version":
        return f"{v:.2f}"
    div = f["div"]
    text = f"{v:.3f}" if div >= 1000 else f"{v:.2f}" if div >= 100 else f"{v:.1f}" if div >= 10 else f"{round(v):d}"
    return f"{text} {f['unit']}" if f["unit"] else text


def number(key: str, regs) -> Optional[float]:
    f = FIELDS_BY_KEY.get(key)
    v = decode(f, regs) if f else None
    return v if isinstance(v, float) else None


def text(key: str, regs) -> Optional[str]:
    f = FIELDS_BY_KEY.get(key)
    return fmt(f, regs) if f else None


def decode_registers(regs) -> Dict[str, Any]:
    """{key: value} for every field present (numbers as floats, others formatted)."""
    out = {}
    for f in FIELDS:
        v = decode(f, regs)
        if v is None:
            continue
        out[f["key"]] = round(v, 3) if isinstance(v, float) and f["kind"] not in ("minutes",) else fmt(f, regs)
    return out


# Cells: block 6300 = [cell count n, sensor count m, n × mV, 2 unknown, m × (°C + 40)]
CELL_BASE = 6300


def cells(regs) -> Optional[Dict[str, List[float]]]:
    n, m = regs.get(CELL_BASE), regs.get(CELL_BASE + 1)
    if n is None or m is None or not (1 <= n <= 32) or m > 16:
        return None
    volts = []
    for i in range(n):
        raw = regs.get(CELL_BASE + 2 + i)
        if raw is None or not (1000 <= raw <= 5000):
            return None
        volts.append(raw / 1000)
    temps = []
    for i in range(m):
        raw = regs.get(CELL_BASE + 2 + n + 2 + i)
        if raw is not None and raw != NOT_AVAILABLE and 1 <= raw <= 140:
            temps.append(raw - 40)
    return {"voltages": volts, "temperatures": temps}


def label_for_register(addr: int, regs) -> str:
    f = FIELD_FOR_REGISTER.get(addr)
    if f:
        return f["label"]
    if addr == CELL_BASE:
        return "Cell count"
    if addr == CELL_BASE + 1:
        return "Temperature sensor count"
    c = cells(regs)
    if c:
        n = len(c["voltages"])
        if 0 <= addr - CELL_BASE - 2 < n:
            return f"Cell {addr - CELL_BASE - 1} voltage (mV)"
        t = addr - CELL_BASE - 2 - n - 2
        if 0 <= t < len(c["temperatures"]):
            return f"Temperature sensor {t + 1} (+40)"
    return ""


# Names: "EL30V2" → "Elite 30 V2"; "PR200V2" is BLUETTI's internal code for the Elite 200 V2.
def friendly_model(code: str) -> str:
    if code == "PR200V2":
        return "Elite 200 V2"
    m = re.fullmatch(r"EL(\d+)V2", code)
    if m:
        return f"Elite {m.group(1)} V2"
    m = re.fullmatch(r"EL(\d+)", code)
    if m:
        return f"Elite {m.group(1)}"
    return code


def split_advertised_name(name: str):
    """Bluetooth names are model + serial: 'EL30V22500000000001', 'Elite 200 V22500000000002'."""
    m = re.fullmatch(r"(Elite \d+ V\d)(\d{6,})", name) or \
        re.fullmatch(r"((?:EL|PR)\d+V2|EL\d+|AC\d+[A-Z]?|EB\d+[A-Z]|EP\d+[A-Z]?|AP\d+)(\d{6,})", name)
    return (m.group(1), m.group(2)) if m else None


def friendly_name(advertised: str) -> str:
    parts = split_advertised_name(advertised)
    return friendly_model(parts[0]) if parts else advertised

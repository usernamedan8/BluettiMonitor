"""BLE Connection and Device Polling Engine for Linux Bluetti Monitor.
Supports live Bluetooth LE via bleak (BlueZ) as well as a realistic Demo Mode.
"""

import asyncio
import json
import os
import queue
import random
import threading
import time
from typing import Dict, List, Optional, Callable

from .protocol import (
    BLOCKS,
    BluettiCrypto,
    BluettiLink,
    modbus_read_frame,
    modbus_parse_read,
    decode_registers,
)

# Characteristic UUIDs
UUID_NOTIFY = "0000ff01-0000-1000-8000-00805f9b34fb"
UUID_WRITE = "0000ff02-0000-1000-8000-00805f9b34fb"

NAME_PREFIXES = ("AC", "EB", "EP", "EL", "PR", "AP", "AORA", "HANDSFREE", "FP", "BALCO", "ELITE")

try:
    import bleak
    HAVE_BLEAK = True
except ImportError:
    HAVE_BLEAK = False


class MonitorEngine:
    """Orchestrates BLE scanning, connection, polling, and status events."""

    def __init__(self, event_queue: queue.Queue):
        self.queue = event_queue
        self.crypto = BluettiCrypto()
        self.registers: Dict[int, int] = {}
        self.running = False
        self.paused = False
        self.demo_mode = False
        self.poll_interval = 3.0
        self.worker_thread: Optional[threading.Thread] = None
        self.selected_device: Optional[str] = None
        self.discovered_devices: Dict[str, dict] = {}
        self.client = None
        self.current_address: Optional[str] = None
        self._reconnect = False
        self._deep_scan_requested = False

    def post_event(self, event_type: str, data: any):
        self.queue.put({"type": event_type, "data": data, "time": time.time()})

    def log(self, msg: str):
        self.post_event("log", msg)

    def set_poll_interval(self, sec: float):
        self.poll_interval = max(1.0, float(sec))

    def set_paused(self, paused: bool):
        self.paused = paused
        if paused:
            self.post_event("phase", "Paused (Bluetooth freed for phone app)")
            self.log("Paused connection: Bluetooth freed for mobile app")
        else:
            self.post_event("phase", "Resuming connection")
            self.log("Resuming Bluetooth monitoring")

    def switch_to(self, address: str):
        """Drop the current connection and connect to another station."""
        self.selected_device = address
        self._reconnect = True
        if not self.running or self.demo_mode:
            self.start_live(address)

    def request_deep_scan(self):
        """Read every register 0–19,999 once (read-only), then resume polling."""
        self._deep_scan_requested = True

    def start_demo(self):
        """Starts realistic simulation mode without needing Bluetooth."""
        self.stop()
        self.demo_mode = True
        self.running = True
        self.post_event("phase", "Demo Mode (Simulating Elite 200 V2)")
        self.log("Started Simulator: playing Elite 200 V2 power station")
        self.worker_thread = threading.Thread(target=self._demo_loop, daemon=True)
        self.worker_thread.start()

    def start_live(self, device_address: Optional[str] = None):
        """Starts real Bluetooth LE polling."""
        self.stop()
        self.demo_mode = False
        self.selected_device = device_address
        self.running = True
        if not HAVE_BLEAK:
            self.post_event("phase", "Bluetooth library (bleak) missing")
            self.log("The 'bleak' Bluetooth library is not installed, so live mode can't run. "
                     "Install it with: pip install --user bleak   (Demo Mode still works.)")
            self.running = False
            return

        self.worker_thread = threading.Thread(target=self._ble_worker, daemon=True)
        self.worker_thread.start()

    def stop(self):
        self.running = False
        self.demo_mode = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.0)
        self.worker_thread = None

    def _demo_loop(self):
        """Replays a sanitised Elite 200 V2 snapshot with gently changing flows."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_registers.json")
        try:
            with open(path) as fh:
                base = {int(k): v for k, v in json.load(fh).items()}
        except OSError:
            base = {}
        self.registers.clear()
        self.registers.update(base)
        # A typical afternoon: solar charging, the rack on AC, a little DC.
        self.registers.update({2011: 1, 1509: 1, 1500: 500, 1511: 2301, 124: 7, 123: 0b1000011001})
        soc, pv, ac_out, dc_out = 62.0, 320.0, 110.0, 12.0
        self.post_event("device_info", {"name": "Elite 200 V2 (demo)", "address": "demo"})
        self.post_event("security", {"encrypted": True, "signature": True})
        self.post_event("discovered", [{"name": "Elite 200 V2 (demo)", "address": "demo", "rssi": -58}])
        self.log("Demo data: a recorded Elite 200 V2 with simulated solar and loads")
        while self.running and self.demo_mode:
            if not self.paused:
                pv = max(0.0, min(800.0, pv + random.uniform(-18, 18)))
                ac_out = max(60.0, min(400.0, ac_out + random.uniform(-6, 6)))
                dc_out = max(0.0, min(60.0, dc_out + random.uniform(-1.5, 1.5)))
                net = pv - ac_out - dc_out
                soc = max(5.0, min(100.0, soc + net / 2073 * 100 * self.poll_interval / 3600 * 20))
                cell = 3.20 + soc / 100 * 0.15
                r = self.registers
                r[102], r[6005] = int(soc), int(soc)
                r[100] = int(cell * 12 * 100)
                r[101] = int(abs(net) / (cell * 12) * 10)
                r[103] = 1 if net > 5 else 2 if net < -5 else 0
                r[104] = int((100 - soc) / 100 * 2073 / max(net, 1) * 60) if net > 5 else 0
                r[105] = int(soc / 100 * 2073 / max(-net, 1) * 60) if net < -5 else 0
                for reg, w in ((140, dc_out), (142, ac_out), (144, pv), (146, 0.0)):
                    r[reg], r[reg + 1] = int(w) & 0xFFFF, int(w) >> 16
                r[1212], r[1213], r[1214] = int(pv), 486, int(pv / 48.6 * 10)
                r[1406], r[1400], r[1430], r[1510], r[1512] = int(dc_out), int(dc_out), int(ac_out), int(ac_out), int(ac_out / 230 * 10)
                for i in range(12):
                    r[6302 + i] = int(cell * 1000 + random.randint(-3, 3))
                r[6116], r[6117] = min(r[6302 + i] for i in range(12)), max(r[6302 + i] for i in range(12))
                decoded = decode_registers(self.registers)
                self.post_event("telemetry", {
                    "registers": dict(self.registers), "decoded": decoded,
                    "soc": soc, "pvInPower": pv, "gridInPower": 0.0, "acOutPower": ac_out,
                    "dcOutPower": dc_out, "packVoltage": cell * 12, "packCurrent": abs(net) / (cell * 12),
                })
            time.sleep(self.poll_interval)

    def _ble_worker(self):
        """Asynchronous BLE worker run in its own thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._ble_main_async())
        finally:
            loop.close()

    async def _ble_main_async(self):
        """Find a station, hold one encrypted connection, poll; reconnect on loss."""
        while self.running and not self.demo_mode:
            if self.paused:
                await asyncio.sleep(1)
                continue
            target = await self._find_target()
            if target is None:
                await asyncio.sleep(5)
                continue
            address, name = target
            try:
                await self._session(address, name)
            except Exception as e:
                self.post_event("phase", f"Disconnected: {e}")
                self.log(f"Connection lost: {e}")
            if self.running and not self.paused:
                self.log("Reconnecting in 5 s...")
                await asyncio.sleep(5)

    async def _find_target(self):
        self.post_event("phase", "Scanning for Bluetti devices...")
        try:
            found = await bleak.BleakScanner.discover(timeout=6.0, return_adv=True)
        except Exception as e:
            self.log(f"Scan error: {e}")
            return None
        stations = []
        for address, (dev, adv) in found.items():
            name = adv.local_name or dev.name or ""
            if any(name.upper().startswith(p) for p in NAME_PREFIXES) or "BLUETTI" in name.upper():
                stations.append({"name": name, "address": address, "rssi": adv.rssi})
                if address not in self.discovered_devices:
                    self.log(f"Found station: {name} ({address}) {adv.rssi} dBm")
                self.discovered_devices[address] = stations[-1]
        self.post_event("discovered", sorted(self.discovered_devices.values(), key=lambda s: -s["rssi"]))

        if self.selected_device:
            match = [s for s in stations if s["address"].upper() == self.selected_device.upper()]
            if not match:
                self.post_event("phase", "Station not in range")
                self.log(f"{self.selected_device} not heard. Keep it within ~10 m, and make sure the "
                         "BLUETTI phone app isn't connected to it (it takes one Bluetooth connection at a time).")
                return None
            return match[0]["address"], match[0]["name"]
        if not stations:
            self.post_event("phase", "No Bluetti found in range")
            self.log("No Bluetti power stations heard nearby.")
            return None
        # Never pick one ourselves: a neighbour's station may be in range too.
        self.post_event("phase", "Choose your power station")
        return None

    async def _session(self, address: str, name: str):
        loop = asyncio.get_running_loop()
        link = BluettiLink()
        ready = asyncio.Event()
        failure = {"reason": None}
        pending = {"future": None}
        write_lock = asyncio.Lock()
        working_chunks = {}

        self._reconnect = False
        if address != getattr(self, "_last_address", None):
            self.registers.clear()           # new station: don't mix in old readings
        self._last_address = address
        self.current_address = address
        self.post_event("phase", f"Connecting to {name}...")
        self.log(f"Connecting to {name} ({address})...")
        async with bleak.BleakClient(address, timeout=20.0) as client:
            self.client = client
            self.post_event("device_info", {"name": name, "address": address})
            char = client.services.get_characteristic(UUID_WRITE)
            with_response = char is not None and "write" in char.properties

            async def send(data: bytes):
                async with write_lock:
                    await client.write_gatt_char(UUID_WRITE, data, response=with_response)

            def handle(events):
                for kind, val in events:
                    if kind == "write":
                        loop.create_task(send(val))
                    elif kind == "ready":
                        ready.set()
                    elif kind == "response":
                        fut = pending["future"]
                        if fut is not None and not fut.done():
                            fut.set_result(val)
                    elif kind == "log":
                        self.log(val)
                    elif kind == "failed":
                        failure["reason"] = val
                        self.log(f"Error: {val}")
                        ready.set()

            await client.start_notify(UUID_NOTIFY, lambda _c, data: handle(link.receive(bytes(data))))

            # Encrypted stations send a challenge straight away; older ones stay silent.
            self.post_event("phase", "Securing connection (handshake)...")
            await asyncio.sleep(4)
            if not link.saw_challenge:
                self.log("No challenge received: using unencrypted Modbus")
                link.use_plaintext()
            else:
                try:
                    await asyncio.wait_for(ready.wait(), timeout=20)
                except asyncio.TimeoutError:
                    raise RuntimeError("handshake timed out")
                if failure["reason"]:
                    raise RuntimeError(failure["reason"])
            sig = link.crypto.peer_sig_valid
            self.post_event("security", {"encrypted": link.encrypted, "signature": sig})
            self.log(f"Connected ({'encrypted' if link.encrypted else 'unencrypted'}"
                     f"{', station signature verified' if sig else ''})")

            async def read(start: int, count: int):
                payload = link.wrap(modbus_read_frame(start, count))
                link.clear_pending()
                fut = loop.create_future()
                pending["future"] = fut
                try:
                    await send(payload)
                    frame = await asyncio.wait_for(fut, timeout=5)
                finally:
                    pending["future"] = None
                return modbus_parse_read(frame, count)

            def store(start: int, values):
                for i, v in enumerate(values):
                    self.registers[start + i] = v

            async def read_block(block):
                start, count = block["start"], block["count"]
                if start in working_chunks:
                    for s_, n_ in working_chunks[start]:
                        try:
                            store(s_, await read(s_, n_))
                        except Exception:
                            pass
                    return
                try:
                    store(start, await read(start, count))
                except Exception as e:
                    if not client.is_connected:
                        raise
                    # Some stations refuse reads that run past the end of a block: split it once.
                    self.log(f"{block['name']} ({start}+{count}): {e}; trying smaller reads")
                    ok = []
                    for s_ in range(start, start + count, 10):
                        n_ = min(10, start + count - s_)
                        try:
                            store(s_, await read(s_, n_))
                            ok.append((s_, n_))
                        except Exception:
                            if not client.is_connected:
                                raise
                    working_chunks[start] = ok

            self.post_event("phase", "Live Monitoring")
            cycle = 0
            while self.running and not self.demo_mode and client.is_connected:
                if self.paused:
                    self.log("Paused: disconnecting so the phone app can connect")
                    break
                if self._reconnect:
                    self.log("Switching station")
                    break
                if self._deep_scan_requested:
                    self._deep_scan_requested = False
                    await self._deep_scan(read, store)
                slow = cycle % max(1, int(60 / self.poll_interval)) == 0
                for block in BLOCKS:
                    if block["fast"] or slow:
                        await read_block(block)
                if cycle == 0:
                    self.log(f"First full read done: {len(self.registers)} registers")
                decoded = decode_registers(self.registers)
                self.post_event("telemetry", {
                    "registers": dict(self.registers),
                    "decoded": decoded,
                    "soc": decoded.get("soc", 0),
                    "pvInPower": decoded.get("pvInPower", 0),
                    "gridInPower": decoded.get("gridInPower", 0),
                    "acOutPower": decoded.get("acOutPower", 0),
                    "dcOutPower": decoded.get("dcOutPower", 0),
                    "packVoltage": decoded.get("packVoltage", 0),
                    "packCurrent": decoded.get("packCurrent", 0),
                })
                cycle += 1
                await asyncio.sleep(self.poll_interval)
            self.client = None
            self.current_address = None

    async def _deep_scan(self, read, store):
        total, chunk, found = 20000, 20, 0
        known = [range(b["start"], b["start"] + b["count"]) for b in BLOCKS]
        self.log("Deep scan started (registers 0–19,999, read-only)")
        for start in range(0, total, chunk):
            if not self.running or self.paused or self._reconnect:
                self.log("Deep scan stopped")
                return
            try:
                values = await read(start, chunk)
            except Exception:
                values = None
            if values:
                store(start, values)
                if any(values) and not any(start in r or start + chunk - 1 in r for r in known):
                    found += 1
            if start % 400 == 0:
                self.post_event("deepscan", {"position": start, "total": total, "found": found, "done": False})
        self.post_event("deepscan", {"position": total, "total": total, "found": found, "done": True})
        self.log(f"Deep scan finished: {found} extra non-empty ranges")

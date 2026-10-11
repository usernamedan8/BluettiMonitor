#!/usr/bin/python3
"""Bluetti Monitor entry point (Qt interface, Tk fallback)."""

import argparse
import os
import queue
import sys

# Ensure package directory is on sys.path
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
# The RPM bundles the 'bleak' Bluetooth library here (Fedora doesn't package it).
VENDOR = os.path.join(HERE, "vendor")
if os.path.isdir(VENDOR) and VENDOR not in sys.path:
    sys.path.append(VENDOR)

from bluetti_monitor.ble import MonitorEngine


def main():
    parser = argparse.ArgumentParser(description="Bluetti Power Station Monitor")
    parser.add_argument("--demo", action="store_true", help="Start in demo mode (recorded Elite 200 V2 data)")
    parser.add_argument("--device", type=str, help="Bluetooth address of the station to connect to")
    parser.add_argument("--poll", type=float, default=None, help="Polling interval in seconds")
    parser.add_argument("--tk", action="store_true", help="Use the basic Tk interface")
    args = parser.parse_args()

    engine = MonitorEngine(queue.Queue())

    if not args.tk:
        try:
            from bluetti_monitor import qt_gui
        except ImportError as e:
            print(f"Qt interface unavailable ({e}); using the basic Tk interface. "
                  "Install python3-pyside6 for the full one.", file=sys.stderr)
        else:
            from PySide6.QtCore import QSettings
            if args.poll:
                QSettings("BluettiMonitor", "BluettiMonitor").setValue("poll", args.poll)
            if args.device:
                QSettings("BluettiMonitor", "BluettiMonitor").setValue("station", args.device)
            sys.exit(qt_gui.run(engine, start_demo=args.demo))

    from bluetti_monitor.gui import BluettiApp
    engine.set_poll_interval(args.poll or 3.0)
    app = BluettiApp(engine)
    if args.demo:
        engine.start_demo()
        app.demo_btn.config(text="Switch to Live BLE")
    else:
        engine.start_live(device_address=args.device)
    try:
        app.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()


if __name__ == "__main__":
    main()

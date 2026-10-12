# Bluetti Monitor

An **unofficial** Linux desktop monitor for BLUETTI power stations. It connects over
encrypted Bluetooth LE and shows battery level, power flows, history graphs, cell
voltages, temperatures, energy totals and raw registers, with a battery readout in
the system tray. It only reads from the station and never changes its settings.
See the [disclaimer](#disclaimer) before using it.

Tested with the Apex 300. Other models using BLUETTI's newer (V2) Bluetooth protocol may work but are untested.

![Overview](screenshots/overview.png)



https://github.com/user-attachments/assets/b19fc483-b242-4d83-883b-a822ae6c7041



## Install

If you've been given a Flatpak bundle:

```
flatpak install --user bluetti-monitor-1.1.0.flatpak
```

## Run from source

Needs Python 3.10+, PySide6, `cryptography` and `dbus-fast`, plus BlueZ.
`bleak` is bundled in `vendor/`.

```
python3 main.py          # live Bluetooth
python3 main.py --demo   # recorded demo data, no station needed
```

## Build the Flatpak

```
flatpak install flathub org.flatpak.Builder
flatpak run org.flatpak.Builder --user --install --force-clean builddir io.github.usernamedan8.BluettiMonitor.yml
```

## Disclaimer

This is a hobby project, provided as is, with no warranty. Use it at your own risk.

- **Unofficial.** BLUETTI is a trademark of its owner. This project is not affiliated
  with, endorsed by or supported by BLUETTI. Don't contact BLUETTI support about it.
- **Read-only.** The app only reads values from the power station. It never sends
  commands that change settings, switch outputs or alter charging.
- **Encryption keys.** Newer BLUETTI stations encrypt their Bluetooth traffic using keys
  built into BLUETTI's own app, which are the same for every unit. The protocol code here,
  including those keys, is ported from the open-source
  [bluetti-bt-lib](https://github.com/Patrick762/bluetti-bt-lib), where they are already
  published. They exist only so this app can talk to your own station. They don't give
  access to anyone's BLUETTI account, cloud service or Wi-Fi, and they only work within
  Bluetooth range.
- **Interoperability.** The protocol work was done only so that this software can
  communicate with hardware its users own.
- **AI-assisted.** Much of this code was written with the help of AI tools.

If you are a rights holder and have a concern, please open an issue and it will be
dealt with promptly.

## Licence

MIT, see [LICENSE](LICENSE). `vendor/bleak` is MIT ([vendor/bleak-LICENSE](vendor/bleak-LICENSE)).

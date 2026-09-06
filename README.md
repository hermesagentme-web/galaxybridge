# GalaxyBridge

Linux controls for Samsung Galaxy Buds, with an optional GNOME extension and a
standalone desktop panel for KDE Plasma, Xfce, Cinnamon, MATE and other desktops.

**Experimental project.** Buds4 Pro has worked in controlled phone + Linux
sessions, but reliable automatic reconnection to both devices after a charging
case cycle is **not solved**. Software tests are not hardware certification.
GalaxyBridge is unofficial and is not affiliated with Samsung.

[Français](README.fr.md) · [Compatibility](docs/compatibility.md) ·
[Hardware test plan](docs/test-plan.md) · [Contributing](CONTRIBUTING.md)

## Features

- Explicit A2DP audio connection through BlueZ.
- Read-only device capability inspection and diagnostics.
- Noise cancellation / ambient / off commands with device readback.
- Experimental, guided SMEP multipoint activation.
- PipeWire/WirePlumber music profile selection.
- Optional GNOME Quick Settings integration; desktop-independent graphical panel
  and CLI use the same backend.
- No firmware flashing, factory reset, account login or cloud service.

## Compatibility

| Component | Requirements | Validation |
| --- | --- | --- |
| CLI and daemon | Linux, Python 3.10+, BlueZ, D-Bus, PyGObject | Ubuntu 26.04 tested |
| Standalone panel | Above plus Python Tk and graphical session | Software checks; other desktops need hardware testing |
| Music profile controls | PipeWire + WirePlumber with wpctl settings support | Tested locally |
| GNOME extension | GNOME Shell 50 | Local integration; reload after updates |
| Other GNOME versions | Use standalone panel or CLI | Extension not declared compatible |
| Non-systemd Linux | Run daemon directly in a terminal/session supervisor | Not hardware validated |

The project is not tied to a particular Bluetooth address. Standard A2DP controls
use BlueZ. Vendor operations require the matching service; advertised services
do not prove that a model's protocol has been validated. See the
[model matrix](docs/compatibility.md). **Do not reuse another device's channel.**

## Install

Install distribution packages providing Python venv/pip, dbus-python, PyGObject,
BlueZ (including sdptool), and optionally Tk. For Debian/Ubuntu:

```sh
sudo apt install python3-venv python3-pip python3-dbus python3-gi python3-tk bluez
# In your local clone:
bash scripts/install.sh                 # Any desktop; GNOME is not required
# OR:
bash scripts/install.sh --gnome         # Also copy GNOME Shell 50 extension
export PATH="$HOME/.local/bin:$PATH"
```

PipeWire and WirePlumber are needed for profile controls; ordinary Bluetooth
connection and experimental SMEP operations do not depend on GNOME.
The installer preserves existing configuration and does not connect the Buds,
start the service, or change system Bluetooth policy. Internet access may be
needed to install Python build requirements.

Find your device, then edit the installed configuration:

```sh
bluetoothctl devices
# Edit ~/.config/galaxybridge/environment and set GALAXYBRIDGE_BUDS_ADDRESS.
systemctl --user enable --now galaxybridge-buds.service
galaxybridge-ui
```

The desktop launcher is also available as **GalaxyBridge** in your application
menu. Pair the Buds using your desktop's Bluetooth settings first.

On GNOME 50, log out/in after installation, then:

```sh
gnome-extensions enable galaxybridge@galaxybridge.local
```

On Linux without systemd, export the address and run:

```sh
export GALAXYBRIDGE_BUDS_ADDRESS='YOUR_DEVICE_ADDRESS'
galaxybridge-buds-daemon
```

Leave the daemon running in that terminal; open the panel in another session.
It monitors the configured device and provides local IPC. It does not perform
background reconnect or automatic multipoint writes by default.

For a device whose explicit activation has already been verified, opt into
session renewal with `GALAXYBRIDGE_BUDS_AUTO_PATCH=true` in the environment file,
then restart the daemon. Keep `GALAXYBRIDGE_BUDS_AUTO_CONNECT=false`.
Renewal makes one attempt on an unverified connection, suppresses duplicate
events and spaces writes by at least 60 seconds. It can briefly interrupt PC
audio. A confirmed disconnect clears the session flag; this is a heuristic,
not direct detection of the charging case. Phone auto-reconnect is not guaranteed.

## Everyday commands

```sh
galaxybridgectl doctor
galaxybridgectl buds capabilities
galaxybridgectl buds connect
galaxybridgectl buds noise-control status
galaxybridgectl buds noise-control set anc
galaxybridgectl buds noise-control set ambient
galaxybridgectl buds noise-control set off
galaxybridgectl buds audio-mode set music
```

Noise commands briefly open a vendor control connection, then release it.
The GNOME menu does not poll that profile automatically. Its cached mode may
be stale after a phone/touch change; use the explicit refresh action.

Music chooses A2DP, not a guaranteed codec. Codec availability depends on the
device and host. Temporary PC call mode is an advanced daemon-only command
(`buds audio-mode set call`); HFP can interrupt phone use. Its automatic return
requires the daemon to remain alive. Avoid it during coexistence tests.

## Experimental multipoint

The proposed upstream technique sets a volatile SMEP peer version and verifies
the response. It does not implement Samsung Account Auto Switch. Case power
cycles can erase that state. Ubuntu cannot command Android to reconnect.

With the daemon running:

1. Run `galaxybridgectl buds multipoint prepare` (disconnects the PC).
2. Disable phone Bluetooth, put Buds in the case briefly, then take them out.
3. Run `galaxybridgectl buds multipoint activate`. Wait for a successful
   readback and PC audio restoration.
4. Enable phone Bluetooth and connect the Buds on the phone.
5. Check audio from each device, then run `galaxybridgectl buds multipoint done`.

The standalone panel provides these steps. Do not run recovery on an already
working session. A connected PC or successful write alone does not demonstrate
two working audio links. If activation fails, inspect the error rather than
repeatedly reconnecting. Never guess an RFCOMM channel from an HFP record.
A verified-channel override is for developers with prior exact discovery and
readback evidence on that same device, not an installation default.

## Development

```sh
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
bash scripts/build.sh
```

Tests use mocks or private local IPC, not the physical headset.
GitHub Actions runs tests on multiple Python versions, checks JavaScript and
shell syntax, and builds a wheel. Hardware acceptance is separate.

## Privacy, removal and migration

Addresses and device names are local configuration. Diagnostic output can contain
device aliases and audio application/media names: review it before posting.
There is no telemetry.

`bash scripts/uninstall.sh` disables the service and removes launcher entry
points. It retains configuration, venv and extension files for recovery.

Older private builds used a different GNOME extension identifier: disable the old
GalaxyBridge extension before enabling the public one to avoid duplicate menus.
This repository no longer includes Samsung Pass. Existing local vaults and browser
installations are not deleted by the new installer.

## License and acknowledgements

MIT for original GalaxyBridge code. Protocol implementation is informed by
[GalaxyBudsClient](https://github.com/timschneeb/GalaxyBudsClient) and
[experimental multipoint PR #729](https://github.com/timschneeb/GalaxyBudsClient/pull/729).
That proposal reports Buds2 Pro/macOS validation, not universal model support.
See [protocol notes](docs/buds-protocol.md) for provenance and limitations.

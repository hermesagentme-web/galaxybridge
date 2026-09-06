from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .capabilities import capabilities
from .audio_policy import HostAudioPolicy
from .bluez import A2DP_SINK_UUID, BlueZError, connect_device, device_state, disconnect_device
from .buds import BudsError, BudsPatcher, bluetoothctl, device_info, encode_mde_version_as_ver, find_rfcomm_channel
from .buds_control import NoiseControlClient, NoiseControlMode
from .buds_ipc import BudsIpcError, default_buds_socket_path, request_buds_daemon
from .buds_session import BudsSessionMarker
from .diagnostics import buds_diagnostics, doctor
from .status import NoiseControlStatusWriter



def _configured_address() -> str:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = config_home / "galaxybridge" / "environment"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip() == "GALAXYBRIDGE_BUDS_ADDRESS":
            return value.strip().strip("'\"")
    return ""


def _address(args: argparse.Namespace) -> str:
    address = args.address or os.environ.get("GALAXYBRIDGE_BUDS_ADDRESS", "") or _configured_address()
    if not address:
        raise SystemExit("Buds address is required; set GALAXYBRIDGE_BUDS_ADDRESS or pass --address")
    return address


def _daemon_request(payload: dict[str, object]) -> dict[str, object] | None:
    path = default_buds_socket_path()
    if not path.exists():
        return None
    try:
        timeout = 180.0 if payload.get("action") == "multipoint_activate" else 45.0
        return request_buds_daemon(payload, path=path, timeout=timeout)
    except BudsIpcError as exc:
        # Never repeat a write after an ambiguous IPC failure: the daemon may
        # have completed it just before the response was interrupted.
        raise BudsError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="galaxybridgectl", description="GalaxyBridge Linux CLI")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_parser = sub.add_parser("doctor", help="check Linux, Bluetooth and PipeWire prerequisites")
    doctor_parser.add_argument("--address")

    diag = sub.add_parser("diagnostics", help="collect redacted diagnostics")
    diag.add_argument("--buds", action="store_true")
    diag.add_argument("--address")

    buds = sub.add_parser("buds", help="Galaxy Buds SMEP multipoint operations")
    buds_sub = buds.add_subparsers(dest="buds_command", required=True)
    for name in ("status", "probe", "patch"):
        child = buds_sub.add_parser(name)
        child.add_argument("--address")
        child.add_argument("--channel", type=int)
        child.add_argument(
            "--verified-channel",
            type=int,
            help="reuse a channel previously confirmed by exact SMEP discovery and asVer readback",
        )
    caps = buds_sub.add_parser("capabilities", help="inspect advertised services without opening a control profile")
    caps.add_argument("--address")
    frame = buds_sub.add_parser("frame", help="print the exact asVer=2 SMEP frame without touching Bluetooth")
    frame.add_argument("--as-ver", type=int, default=2, choices=(2, 3))
    connect = buds_sub.add_parser("connect", help="ask BlueZ for a normal Buds connection")
    connect.add_argument("--address")
    noise = buds_sub.add_parser("noise-control", help="read or change Buds4 Pro noise control")
    noise_sub = noise.add_subparsers(dest="noise_command", required=True)
    noise_status = noise_sub.add_parser("status", help="read the current noise-control mode")
    noise_status.add_argument("--address")
    noise_set = noise_sub.add_parser("set", help="set a mode and require exact Buds readback")
    noise_set.add_argument("mode", choices=("off", "anc", "ambient"))
    noise_set.add_argument("--address")
    audio = buds_sub.add_parser("audio-mode", help="switch between stable music and temporary PC call mode")
    audio_sub = audio.add_subparsers(dest="audio_command", required=True)
    audio_status = audio_sub.add_parser("status")
    audio_status.add_argument("--address")
    audio_set = audio_sub.add_parser("set")
    audio_set.add_argument("mode", choices=("music", "call"))
    audio_set.add_argument("--address")
    multipoint = buds_sub.add_parser("multipoint", help="guided phone-safe multipoint restoration")
    multipoint_sub = multipoint.add_subparsers(dest="multipoint_command", required=True)
    for name in ("prepare", "activate", "done"):
        child = multipoint_sub.add_parser(name)
        child.add_argument("--address")
        if name == "activate":
            child.add_argument("--verified-channel", type=int)
    adopt = multipoint_sub.add_parser("adopt", help="remember a just-verified current power session")
    adopt.add_argument("--address")
    adopt.add_argument("--as-ver", type=int, choices=(2, 3), default=2)
    adopt.add_argument("--verified-channel", type=int, required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            print(json.dumps(doctor(args.address), indent=2, ensure_ascii=False))
            return 0
        if args.command == "diagnostics":
            if args.buds:
                address = _address(args)
                value = buds_diagnostics(address)
            else:
                value = doctor(args.address)
            print(json.dumps(value, indent=2, ensure_ascii=False))
            return 0
        if args.command == "buds":
            if args.buds_command == "frame":
                print(encode_mde_version_as_ver(args.as_ver).hex(" ").upper())
                return 0
            address = _address(args)
            def request(payload):
                return _daemon_request({**payload, "address": address})
            if args.buds_command == "capabilities":
                print(json.dumps(capabilities(address), indent=2))
                return 0
            if args.buds_command == "audio-mode":
                daemon_result = request({
                    "action": "audio_mode_status" if args.audio_command == "status" else "audio_mode_set",
                    **({"mode": args.mode} if args.audio_command == "set" else {}),
                })
                if daemon_result is None:
                    policy = HostAudioPolicy(address)
                    if args.audio_command == "status":
                        daemon_result = {"success": True, "mode": "music", "service_active": False, "error": ""}
                    else:
                        if args.mode == "call":
                            raise BudsError("Temporary call mode requires a running daemon")
                        result = policy.music()
                        daemon_result = dict(result.__dict__)
                print(json.dumps(daemon_result, indent=2))
                return 0 if daemon_result.get("success") else 1
            if args.buds_command == "multipoint":
                marker = BudsSessionMarker()
                command = args.multipoint_command
                if command == "adopt":
                    current = device_state(address)
                    if not current.connected:
                        raise BudsError("cannot adopt a session while the Buds are disconnected")
                    marker.remember(address, args.as_ver, args.verified_channel)
                    result = {
                        "success": True,
                        "stage": "idle",
                        "reported_as_ver": args.as_ver,
                        "channel": args.verified_channel,
                        "error": "",
                    }
                else:
                    result = request({"action": f"multipoint_{command}"})
                    if result is None:
                        raise BudsError("Start galaxybridge-buds-daemon before using the multipoint wizard")
                print(json.dumps(result, indent=2))
                return 0 if result.get("success") else 1
            if args.buds_command == "noise-control":
                daemon_result = request({
                    "action": "noise_status" if args.noise_command == "status" else "noise_set",
                    **({"mode": args.mode} if args.noise_command == "set" else {}),
                })
                if daemon_result is not None:
                    print(json.dumps(daemon_result, indent=2))
                    return 0 if daemon_result.get("success") else 1
                writer = NoiseControlStatusWriter()
                if args.noise_command == "status":
                    writer.update("reading")
                    result = NoiseControlClient(address).status()
                else:
                    mode = NoiseControlMode.from_name(args.mode)
                    writer.update("changing", requested_mode=mode.label)
                    result = NoiseControlClient(address).set_mode(mode)
                if result.success:
                    writer.update("ready", mode=result.reported_mode)
                else:
                    writer.update("error", "control_failed", requested_mode=result.requested_mode)
                print(json.dumps(result.__dict__, indent=2))
                return 0 if result.success else 1
            if args.buds_command == "connect":
                daemon_result = request({"action": "reconnect"})
                if daemon_result is None:
                    state = connect_device(address, profile_uuid=A2DP_SINK_UUID)
                    daemon_result = {
                        "success": state.connected,
                        "address": state.address,
                        "connected": state.connected,
                        "battery": state.battery,
                        "error": "" if state.connected else "the Buds are not currently available",
                    }
                print(json.dumps(daemon_result, indent=2))
                return 0 if daemon_result.get("success") else 1
            if args.buds_command == "status":
                print(device_info(address).strip())
                channel = args.verified_channel or find_rfcomm_channel(address)
                if args.channel is not None and args.channel != channel:
                    raise BudsError(
                        f"refusing unverified RFCOMM channel {args.channel}; SPPSERVICE4 is on channel {channel}"
                    )
                suffix = " (pre-verified)" if args.verified_channel else ""
                print(f"SMEP RFCOMM channel: {channel}{suffix}")
                return 0
            patcher = BudsPatcher(address, args.channel, verified_channel=args.verified_channel)
            result = patcher.probe() if args.buds_command == "probe" else patcher.apply()
            if args.buds_command == "patch" and result.success and result.reported_as_ver is not None and result.channel is not None:
                BudsSessionMarker().remember(address, result.reported_as_ver, result.channel)
            print(json.dumps(result.__dict__, indent=2))
            return 0 if result.success else 1
    except (BlueZError, BudsError, OSError, ValueError) as exc:
        print(f"galaxybridgectl: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

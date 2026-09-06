from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import __version__
from .buds import device_info


def command_output(*args: str, timeout: float = 5.0) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc}"
    return (result.stdout + result.stderr).strip()


def _redact_mac(value: str) -> str:
    return re.sub(r"(?i)\b([0-9a-f]{2}:){5}[0-9a-f]{2}\b", "XX:XX:XX:XX:XX:XX", value)


def pipewire_streams() -> list[dict[str, Any]]:
    try:
        raw = subprocess.run(["pw-dump"], capture_output=True, text=True, check=False, timeout=10)
        objects = json.loads(raw.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []
    streams: list[dict[str, Any]] = []
    for item in objects if isinstance(objects, list) else []:
        info = item.get("info", {}) if isinstance(item, dict) else {}
        props = info.get("props", {}) if isinstance(info, dict) else {}
        if props.get("media.class") != "Stream/Output/Audio":
            continue
        streams.append({
            "id": item.get("id"),
            "state": info.get("state"),
            "node_name": props.get("node.name"),
            "application": props.get("application.name"),
            "media_name": props.get("media.name"),
        })
    return streams


def doctor(address: str | None = None) -> dict[str, Any]:
    os_release: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                os_release[key] = value.strip('"')
    except OSError:
        pass
    commands = ["bluetoothctl", "sdptool", "dbus-monitor", "pw-dump", "wpctl", "wireplumber"]
    result: dict[str, Any] = {
        "galaxybridge": {"version": __version__, "python": platform.python_version()},
        "os": {"id": os_release.get("ID"), "version": os_release.get("VERSION_ID"), "kernel": platform.release()},
        "commands": {command: shutil.which(command) is not None for command in commands},
        "versions": {
            "bluez": command_output("bluetoothctl", "--version"),
            "pipewire": command_output("pw-dump", "--version"),
            "wireplumber": command_output("wireplumber", "--version"),
        },
        "bluetooth": {"address_configured": bool(address)},
        "pipewire": {"audio_output_streams": pipewire_streams()},
    }
    if address:
        result["bluetooth"]["device_info"] = _redact_mac(device_info(address))
    result["environment"] = {
        key: "set"
        for key in (
            "GALAXYBRIDGE_BUDS_ADDRESS",
            "GALAXYBRIDGE_BUDS_CHANNEL",
            "GALAXYBRIDGE_BUDS_VERIFIED_CHANNEL",
            "GALAXYBRIDGE_BUDS_AUTO_CONNECT",
            "GALAXYBRIDGE_BUDS_AUTO_PATCH",
        )
        if os.environ.get(key)
    }
    return result


def buds_diagnostics(address: str) -> dict[str, Any]:
    return {
        "address": _redact_mac(address),
        "bluetoothctl_info": _redact_mac(device_info(address)),
        "pipewire_streams": pipewire_streams(),
        "smep": {
            "service_uuid": "f8620674-a1ed-41ab-a8b9-de9ad655729d",
            "frame_capture": "disabled; use btmon separately if required",
            "asVer": "not read without an explicit patch run",
        },
    }

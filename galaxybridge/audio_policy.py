"""Reversible host audio policies for Galaxy Buds.

Music mode keeps A2DP/AAC and disables WirePlumber's automatic HFP switch.
Call mode is always explicit and temporary because HFP may displace a phone
from a two-device Buds session.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from .bluez import A2DP_SINK_UUID, BlueZError, connect_device, normalise_address


HFP_HANDSFREE_UUID = "0000111e-0000-1000-8000-00805f9b34fb"


@dataclass(frozen=True)
class AudioPolicyResult:
    success: bool
    mode: str
    profile: str = ""
    sink_restored: bool = False
    error: str = ""


class HostAudioPolicy:
    def __init__(
        self,
        address: str,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.address = normalise_address(address)
        self._runner = runner
        self._sleep = sleep

    def _run(self, args: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(args, capture_output=True, text=True, check=False, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BlueZError(f"host audio command failed: {args[0]}: {exc}") from exc

    def _wpctl(self, *args: str) -> None:
        result = self._run(["wpctl", *args])
        if result.returncode:
            raise BlueZError((result.stderr or result.stdout or "wpctl failed").strip())

    def _objects(self) -> list[dict[str, object]]:
        result = self._run(["pw-dump"], timeout=10.0)
        if result.returncode:
            raise BlueZError((result.stderr or "pw-dump failed").strip())
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise BlueZError("pw-dump returned invalid JSON") from exc
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def _matching_device(self, objects: list[dict[str, object]]) -> dict[str, object] | None:
        for item in objects:
            info = item.get("info")
            props = info.get("props", {}) if isinstance(info, dict) else {}
            if normalise_address(str(props.get("api.bluez5.address", ""))) == self.address and str(
                props.get("media.class", "")
            ) == "Audio/Device":
                return item
        return None

    @staticmethod
    def _profile(device: dict[str, object], prefix: str, exact: str = "") -> tuple[int, str] | None:
        info = device.get("info")
        params = info.get("params", {}) if isinstance(info, dict) else {}
        profiles = params.get("EnumProfile", []) if isinstance(params, dict) else []
        candidates: list[tuple[int, int, str]] = []
        for profile in profiles if isinstance(profiles, list) else []:
            if not isinstance(profile, dict):
                continue
            name = str(profile.get("name", ""))
            if (exact and name != exact) or (not exact and not name.startswith(prefix)):
                continue
            try:
                candidates.append((int(profile.get("priority", 0)), int(profile["index"]), name))
            except (KeyError, TypeError, ValueError):
                continue
        if not candidates:
            return None
        _priority, index, name = max(candidates)
        return index, name

    def _wait_for_profile(self, prefix: str, exact: str = "", seconds: float = 3.0) -> tuple[int, int, str]:
        deadline = time.monotonic() + seconds
        while True:
            objects = self._objects()
            device = self._matching_device(objects)
            if device is not None:
                selected = self._profile(device, prefix, exact)
                if selected is not None:
                    index, name = selected
                    return int(device["id"]), index, name
            if time.monotonic() >= deadline:
                raise BlueZError(f"WirePlumber did not expose the requested {prefix or exact} profile")
            self._sleep(0.2)

    def _restore_default_sink(self, seconds: float = 3.0) -> bool:
        deadline = time.monotonic() + seconds
        while True:
            for item in self._objects():
                info = item.get("info")
                props = info.get("props", {}) if isinstance(info, dict) else {}
                if (
                    normalise_address(str(props.get("api.bluez5.address", ""))) == self.address
                    and str(props.get("media.class", "")).startswith("Audio/Sink")
                    and str(props.get("api.bluez5.profile", "")) == "a2dp-sink"
                ):
                    self._wpctl("set-default", str(item["id"]))
                    return True
            if time.monotonic() >= deadline:
                return False
            self._sleep(0.2)

    def music(self) -> AudioPolicyResult:
        try:
            self._wpctl("settings", "--save", "bluetooth.autoswitch-to-headset-profile", "false")
            state = connect_device(self.address, profile_uuid=A2DP_SINK_UUID)
            if not state.connected:
                raise BlueZError("the A2DP music connection did not become active")
            device_id, profile_index, profile_name = self._wait_for_profile("", exact="a2dp-sink")
            self._wpctl("set-profile", str(device_id), str(profile_index))
            restored = self._restore_default_sink()
            return AudioPolicyResult(True, "music", profile_name, restored)
        except BlueZError as exc:
            return AudioPolicyResult(False, "music", error=str(exc))

    def call(self) -> AudioPolicyResult:
        try:
            self._wpctl("settings", "--save", "bluetooth.autoswitch-to-headset-profile", "true")
            state = connect_device(self.address, profile_uuid=HFP_HANDSFREE_UUID)
            if not state.connected:
                raise BlueZError("the HFP call connection did not become active")
            device_id, profile_index, profile_name = self._wait_for_profile("headset-head-unit")
            self._wpctl("set-profile", str(device_id), str(profile_index))
            return AudioPolicyResult(True, "call", profile_name)
        except BlueZError as exc:
            try:
                self._wpctl("settings", "--save", "bluetooth.autoswitch-to-headset-profile", "false")
            except BlueZError:
                pass
            return AudioPolicyResult(False, "call", error=str(exc))

import json
from subprocess import CompletedProcess
from unittest.mock import patch

from galaxybridge.audio_policy import HFP_HANDSFREE_UUID, HostAudioPolicy
from galaxybridge.bluez import A2DP_SINK_UUID, BlueZDeviceState


ADDRESS = "02:00:00:00:00:01"


def pipewire_dump() -> str:
    return json.dumps([
        {
            "id": 68,
            "info": {
                "props": {"api.bluez5.address": ADDRESS, "media.class": "Audio/Device"},
                "params": {"EnumProfile": [
                    {"index": 131076, "name": "a2dp-sink", "priority": 134},
                    {"index": 196865, "name": "headset-head-unit-msbc", "priority": 6},
                    {"index": 196866, "name": "headset-head-unit", "priority": 7},
                ]},
            },
        },
        {
            "id": 85,
            "info": {"props": {
                "api.bluez5.address": ADDRESS,
                "api.bluez5.profile": "a2dp-sink",
                "media.class": "Audio/Sink/Internal",
            }},
        },
    ])


class Runner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args, **_kwargs):
        self.calls.append(args)
        if args[0] == "pw-dump":
            return CompletedProcess(args, 0, pipewire_dump(), "")
        return CompletedProcess(args, 0, "", "")


def test_music_mode_locks_a2dp_and_restores_default_sink():
    runner = Runner()
    connected = BlueZDeviceState(ADDRESS, "/device", True)
    with patch("galaxybridge.audio_policy.connect_device", return_value=connected) as connect:
        result = HostAudioPolicy(ADDRESS, runner=runner, sleep=lambda _seconds: None).music()
    assert result.success is True
    assert result.profile == "a2dp-sink"
    assert result.sink_restored is True
    connect.assert_called_once_with(ADDRESS, profile_uuid=A2DP_SINK_UUID)
    assert ["wpctl", "settings", "--save", "bluetooth.autoswitch-to-headset-profile", "false"] in runner.calls
    assert ["wpctl", "set-profile", "68", "131076"] in runner.calls
    assert ["wpctl", "set-default", "85"] in runner.calls


def test_call_mode_is_explicit_and_selects_best_hfp_profile():
    runner = Runner()
    connected = BlueZDeviceState(ADDRESS, "/device", True)
    with patch("galaxybridge.audio_policy.connect_device", return_value=connected) as connect:
        result = HostAudioPolicy(ADDRESS, runner=runner, sleep=lambda _seconds: None).call()
    assert result.success is True
    assert result.profile == "headset-head-unit"
    connect.assert_called_once_with(ADDRESS, profile_uuid=HFP_HANDSFREE_UUID)
    assert ["wpctl", "settings", "--save", "bluetooth.autoswitch-to-headset-profile", "true"] in runner.calls
    assert ["wpctl", "set-profile", "68", "196866"] in runner.calls

from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import MagicMock, patch

from galaxybridge.audio_policy import AudioPolicyResult
from galaxybridge.bluez import (
    A2DP_SINK_UUID,
    BlueZDeviceState,
    BlueZError,
    _decode_properties_changed,
    connect_device,
)
from galaxybridge.buds import (
    MDE_VERSION_AS_VER2,
    NOTIFY_PROPERTY,
    SMEP_UUID,
    BudsError,
    BudsPatcher,
    PatchResult,
    _channel_from_sdp,
    decode_smep_frame,
    encode_mde_version_as_ver,
    encode_smep_frame,
    extract_as_ver,
    extract_frames,
    find_rfcomm_channel,
)
from galaxybridge.buds_daemon import BudsDaemon, _env_flag, _retry_schedule


def test_mde_version_frame_matches_public_capture():
    assert encode_mde_version_as_ver(2).hex(" ").upper() == "FC 0B 00 01 43 04 03 04 00 00 0B 02 1E AF CC"
    outer, body, response = decode_smep_frame(encode_mde_version_as_ver(2))
    assert outer == 1
    assert body == bytes([0x43]) + MDE_VERSION_AS_VER2
    assert response is False


def test_stream_decoder_and_asver_readback():
    notify_payload = bytes.fromhex("02 05 4C 0B 00 00 02 00 00 00")
    frame = __import__("galaxybridge.buds", fromlist=["encode_smep_frame"]).encode_smep_frame(NOTIFY_PROPERTY, notify_payload)
    buffer = bytearray(b"noise" + frame[:5])
    assert list(extract_frames(buffer)) == []
    buffer.extend(frame[5:])
    assert list(extract_frames(buffer)) == [frame]
    assert extract_as_ver(frame) == 2


def test_sdp_channel_requires_exact_smep_uuid():
    hfp_only = """
Attribute Identifier : 0x1 - ServiceClassIDList
  UUID16 : 0x111e - Handsfree
Protocol Descriptor List:
  UUID16 : 0x0003 - RFCOMM
  Channel/Port (Integer) : 0x2
"""
    assert _channel_from_sdp(hfp_only) is None

    smep = """
Attribute Identifier : 0x1 - ServiceClassIDList
  UUID128 : 0xf8620674-a1ed-41ab-a8b9-de9ad655-729d
Attribute Identifier : 0x4 - ProtocolDescriptorList
  Data Sequence
    UUID16 : 0x0003 - RFCOMM
    Channel/Port (Integer) : 0x1d
"""
    assert _channel_from_sdp(smep, SMEP_UUID) == 29

    smep_xml = """
<record>
  <attribute id="0x0001">
    <sequence><uuid value="f8620674-a1ed-41ab-a8b9-de9ad655729d" /></sequence>
  </attribute>
  <attribute id="0x0004">
    <sequence>
      <sequence><uuid value="0x0100" /></sequence>
      <sequence><uuid value="0x0003" /><uint8 value="0x1d" /></sequence>
    </sequence>
  </attribute>
</record>
"""
    assert _channel_from_sdp(smep_xml, SMEP_UUID) == 29


def test_sdp_timeout_partial_output_is_accepted():
    smep_xml = f"""
<record>
  <attribute id="0x0001"><sequence><uuid value="{SMEP_UUID}" /></sequence></attribute>
  <attribute id="0x0004"><sequence>
    <sequence><uuid value="0x0100" /></sequence>
    <sequence><uuid value="0x0003" /><uint8 value="0x1d" /></sequence>
  </sequence></attribute>
</record>
"""
    timed_out = TimeoutExpired(["sdptool"], 58, output=smep_xml)
    with patch("galaxybridge.buds.subprocess.run", side_effect=timed_out):
        assert find_rfcomm_channel("02:00:00:00:00:01") == 29


def test_preverified_channel_bypasses_unreliable_repeat_sdp():
    patcher = BudsPatcher("02:00:00:00:00:01", verified_channel=29)
    with patch("galaxybridge.buds.find_rfcomm_channel") as discover:
        assert patcher._verified_channel() == 29
        discover.assert_not_called()

    conflicting = BudsPatcher("02:00:00:00:00:01", channel=2, verified_channel=29)
    with patch("galaxybridge.buds.find_rfcomm_channel") as discover:
        try:
            conflicting._verified_channel()
        except BudsError as exc:
            assert "conflicts" in str(exc)
        else:
            raise AssertionError("conflicting channel hint was accepted")
        discover.assert_not_called()


def test_env_flag_accepts_only_explicit_true_values(monkeypatch):
    monkeypatch.delenv("GALAXYBRIDGE_TEST_FLAG", raising=False)
    assert _env_flag("GALAXYBRIDGE_TEST_FLAG") is False
    monkeypatch.setenv("GALAXYBRIDGE_TEST_FLAG", "yes")
    assert _env_flag("GALAXYBRIDGE_TEST_FLAG") is True
    monkeypatch.setenv("GALAXYBRIDGE_TEST_FLAG", "no")
    assert _env_flag("GALAXYBRIDGE_TEST_FLAG") is False


def test_bluez_property_signal_accepts_missing_invalidated_argument():
    shortened = _decode_properties_changed(("org.bluez.Device1", {"Connected": True}))
    standard = _decode_properties_changed(("org.bluez.Device1", {"Connected": True}, []))
    assert shortened == standard == ("org.bluez.Device1", {"Connected": True})
    assert _decode_properties_changed(("org.bluez.Device1",)) is None


def test_profile_connect_is_requested_even_when_another_profile_is_connected():
    connected = BlueZDeviceState("02:00:00:00:00:01", "/device", True)
    with patch("galaxybridge.bluez.device_state", return_value=connected), patch(
        "galaxybridge.bluez._device_method", return_value=connected
    ) as method:
        result = connect_device(connected.address, profile_uuid=A2DP_SINK_UUID)
    assert result.connected is True
    method.assert_called_once_with(
        connected.address,
        "Connect",
        12.0,
        profile_uuid=A2DP_SINK_UUID,
    )


def test_host_connect_is_bounded_and_skips_while_patching():
    daemon = BudsDaemon("02:00:00:00:00:01", auto_connect=True, reconnect_seconds=0)
    assert daemon.address == "02:00:00:00:00:01"
    assert daemon.reconnect_seconds == 60.0
    assert daemon._connect_schedule == (3.0, 8.0, 20.0, 60.0)

    disconnected = BlueZDeviceState(daemon.address, "/device", False)
    connected = BlueZDeviceState(daemon.address, "/device", True, 97)
    with patch("galaxybridge.buds_daemon.device_state", return_value=disconnected) as state, patch(
        "galaxybridge.buds_daemon.connect_device", return_value=connected
    ) as connect, patch.object(daemon, "_on_connected") as on_connected:
        daemon._worker_active = True
        assert daemon._attempt_host_connect() is False
        state.assert_not_called()
        connect.assert_not_called()

        daemon._worker_active = False
        assert daemon._attempt_host_connect() is True
        connect.assert_called_once_with(
            "02:00:00:00:00:01",
            timeout=12.0,
            discovery_assist=False,
            profile_uuid=A2DP_SINK_UUID,
        )
        on_connected.assert_called_once_with()


def test_host_connect_does_nothing_when_already_connected():
    daemon = BudsDaemon("02:00:00:00:00:01", auto_connect=True)
    connected = BlueZDeviceState(daemon.address, "/device", True)
    with patch("galaxybridge.buds_daemon.device_state", return_value=connected), patch(
        "galaxybridge.buds_daemon.connect_device"
    ) as connect:
        assert daemon._attempt_host_connect() is True
        connect.assert_not_called()


def test_recovery_connect_uses_short_discovery_assist():
    daemon = BudsDaemon("02:00:00:00:00:01", auto_connect=True)
    disconnected = BlueZDeviceState(daemon.address, "/device", False)
    connected = BlueZDeviceState(daemon.address, "/device", True)
    with patch("galaxybridge.buds_daemon.device_state", return_value=disconnected), patch(
        "galaxybridge.buds_daemon.connect_device", return_value=connected
    ) as connect, patch.object(daemon, "_on_connected"):
        assert daemon._attempt_host_connect(discovery_assist=True) is True
    connect.assert_called_once_with(
        "02:00:00:00:00:01",
        timeout=12.0,
        discovery_assist=True,
        profile_uuid=A2DP_SINK_UUID,
    )


def test_disconnect_defers_first_reconnect_and_bluez_hint_accelerates_it():
    daemon = BudsDaemon("02:00:00:00:00:01", auto_connect=True)
    with patch("galaxybridge.buds_daemon.time.monotonic", return_value=100.0):
        daemon._on_disconnected()
    assert daemon._next_connect_at == 103.0

    with patch("galaxybridge.buds_daemon.time.monotonic", return_value=100.5):
        daemon._on_bluez_available_hint()
    assert daemon._next_connect_at == 101.5

    daemon._connect_failures = 1
    daemon._next_connect_at = 150.0
    with patch("galaxybridge.buds_daemon.time.monotonic", return_value=101.0):
        daemon._on_bluez_available_hint()
    assert daemon._next_connect_at == 150.0


def test_short_connection_flaps_increase_backoff_without_resetting_pairing():
    daemon = BudsDaemon("02:00:00:00:00:01", auto_connect=True)
    daemon._connected_since = 100.0
    daemon._patched_for_session = False
    with patch("galaxybridge.buds_daemon.time.monotonic", return_value=105.0):
        daemon._on_disconnected()
    assert daemon._connect_failures == 1
    assert daemon._next_connect_at == 113.0

    daemon._connected_since = 120.0
    daemon._next_connect_at = None
    with patch("galaxybridge.buds_daemon.time.monotonic", return_value=125.0):
        daemon._on_disconnected()
    assert daemon._connect_failures == 2
    assert daemon._next_connect_at is None
    assert daemon._phone_priority_until == 725.0


def test_stable_connection_resets_flap_backoff():
    daemon = BudsDaemon("02:00:00:00:00:01", auto_connect=True)
    daemon._connect_failures = 3
    daemon._connected_since = 100.0
    with patch("galaxybridge.buds_daemon.time.monotonic", return_value=140.0):
        daemon._on_disconnected()
    assert daemon._connect_failures == 0
    assert daemon._next_connect_at == 143.0


def test_retry_schedule_is_progressive_and_bounded():
    assert _retry_schedule(None, (3.0, 8.0)) == (3.0, 8.0)
    assert _retry_schedule("1,5,20", (3.0, 8.0)) == (1.0, 5.0, 20.0)
    assert _retry_schedule("invalid", (3.0, 8.0)) == (3.0, 8.0)


def test_internal_patch_disconnect_does_not_reset_session():
    daemon = BudsDaemon("02:00:00:00:00:01")
    daemon._patched_for_session = True
    daemon._worker_active = True
    daemon._on_disconnected()
    assert daemon._patched_for_session is True


def test_short_regular_disconnect_preserves_verified_session():
    daemon = BudsDaemon("02:00:00:00:00:01")
    daemon._patched_for_session = True
    daemon._disconnect_generation = 4
    connected = BlueZDeviceState(daemon.address, "/device", True)
    with patch("galaxybridge.buds_daemon.device_state", return_value=connected):
        daemon._confirm_disconnected(4, 0)
    assert daemon._patched_for_session is True


def test_persistent_disconnect_rearms_patch():
    marker = MagicMock()
    daemon = BudsDaemon("02:00:00:00:00:01", session_marker=marker)
    daemon._patched_for_session = True
    daemon._disconnect_generation = 7
    disconnected = BlueZDeviceState(daemon.address, "/device", False)
    with patch("galaxybridge.buds_daemon.device_state", return_value=disconnected):
        daemon._confirm_disconnected(7, 0)
    assert daemon._patched_for_session is False
    marker.clear.assert_called_once_with()


def test_recent_session_marker_prevents_duplicate_initial_patch():
    marker = MagicMock()
    marker.load.return_value = {
        "address": "02:00:00:00:00:01",
        "as_ver": 2,
        "channel": 29,
        "verified_at": 100.0,
    }
    daemon = BudsDaemon("02:00:00:00:00:01", session_marker=marker)
    assert daemon._restore_remembered_session() is True
    assert daemon._patched_for_session is True


def test_unmarked_connection_waits_for_explicit_multipoint_restore():
    daemon = BudsDaemon("02:00:00:00:00:01")
    connected = BlueZDeviceState(daemon.address, "/device", True, 97)
    with patch("galaxybridge.buds_daemon.device_state", return_value=connected), patch(
        "galaxybridge.buds_daemon.threading.Thread"
    ) as thread:
        daemon._on_connected()
    assert daemon._worker_active is False
    assert daemon._current_state == "connected"
    assert daemon._current_detail == "multipoint_restore_required"
    thread.assert_not_called()


def test_once_forces_patch_even_when_automatic_patch_is_disabled():
    daemon = BudsDaemon("02:00:00:00:00:01")
    connected = BlueZDeviceState(daemon.address, "/device", True)
    with patch("galaxybridge.buds_daemon.device_state", return_value=connected), patch(
        "galaxybridge.buds_daemon.threading.Thread"
    ) as thread:
        daemon._on_connected(force_patch=True)
    assert daemon._worker_active is True
    thread.assert_called_once()


def test_audio_modes_are_explicit_and_call_mode_expires():
    policy = MagicMock()
    policy.call.return_value = AudioPolicyResult(True, "call", "headset-head-unit")
    policy.music.return_value = AudioPolicyResult(True, "music", "a2dp-sink", True)
    daemon = BudsDaemon("02:00:00:00:00:01", audio_policy=policy)
    with patch("galaxybridge.buds_daemon.time.monotonic", return_value=100.0):
        result = daemon._change_audio_mode("call")
    assert result["success"] is True
    assert daemon._audio_mode == "call"
    assert daemon._call_mode_until == 700.0

    with patch("galaxybridge.buds_daemon.time.monotonic", return_value=701.0), patch(
        "galaxybridge.buds_daemon.threading.Thread"
    ) as thread:
        daemon._control_idle()
    thread.assert_called_once()


def test_multipoint_prepare_clears_marker_and_disconnects_only_pc():
    marker = MagicMock()
    daemon = BudsDaemon("02:00:00:00:00:01", session_marker=marker)
    disconnected = BlueZDeviceState(daemon.address, "/device", False)
    with patch("galaxybridge.buds_daemon.disconnect_device", return_value=disconnected) as disconnect:
        result = daemon._multipoint_prepare()
    assert result["success"] is True
    assert result["stage"] == "phone_off"
    disconnect.assert_called_once_with(daemon.address)
    marker.clear.assert_called_once_with()


def test_multipoint_activate_verifies_once_then_prompts_for_phone():
    marker = MagicMock()
    daemon = BudsDaemon(
        "02:00:00:00:00:01",
        verified_channel=29,
        session_marker=marker,
    )
    daemon._multipoint_stage = "phone_off"
    connected = BlueZDeviceState(daemon.address, "/device", True, 97)
    patched = PatchResult(True, daemon.address, 29, 2, 4)
    patcher = MagicMock()
    patcher.apply.return_value = patched
    with patch("galaxybridge.buds_daemon.connect_device", return_value=connected), patch(
        "galaxybridge.buds_daemon.device_state", return_value=connected
    ), patch("galaxybridge.buds_daemon.BudsPatcher", return_value=patcher) as patcher_type:
        result = daemon._multipoint_activate()
    assert result["success"] is True
    assert result["stage"] == "phone_on"
    assert daemon._patched_for_session is True
    patcher_type.assert_called_once_with(daemon.address, None, verified_channel=29)
    marker.remember.assert_called_once_with(daemon.address, 2, 29)


def test_multipoint_audio_restore_retry_does_not_patch_twice():
    marker = MagicMock()
    marker.load.return_value = {
        "address": "02:00:00:00:00:01",
        "as_ver": 2,
        "channel": 29,
        "verified_at": 100.0,
    }
    daemon = BudsDaemon("02:00:00:00:00:01", session_marker=marker)
    daemon._multipoint_stage = "pc_restore"
    connected = BlueZDeviceState(daemon.address, "/device", True)
    with patch("galaxybridge.buds_daemon.connect_device", return_value=connected), patch(
        "galaxybridge.buds_daemon.device_state", return_value=connected
    ), patch("galaxybridge.buds_daemon.BudsPatcher") as patcher_type:
        result = daemon._multipoint_activate()
    assert result["success"] is True
    assert result["stage"] == "phone_on"
    patcher_type.assert_not_called()


def test_verified_patch_survives_a_bluez_restore_timeout():
    notify = encode_smep_frame(NOTIFY_PROPERTY, bytes.fromhex("02 05 4C 0B 00 00 02 00 00 00"))
    transport = MagicMock()
    transport.__enter__.return_value = transport
    transport.read_until.return_value = [notify]
    disconnected = BlueZDeviceState("02:00:00:00:00:01", "/device", False)

    with patch("galaxybridge.buds.device_connected", return_value=True), patch(
        "galaxybridge.buds.find_rfcomm_channel", return_value=29
    ), patch("galaxybridge.buds.RfcommTransport", return_value=transport), patch(
        "galaxybridge.buds.disconnect_device", return_value=disconnected
    ), patch(
        "galaxybridge.buds.connect_device", side_effect=BlueZError("BlueZ connect timed out")
    ):
        result = BudsPatcher("02:00:00:00:00:01").apply(verify_seconds=0)

    assert result.success is True
    assert result.reported_as_ver == 2
    assert "restore pending" in result.error


def test_sdp_failure_does_not_issue_an_unneeded_restore():
    with patch("galaxybridge.buds.device_connected", return_value=True), patch(
        "galaxybridge.buds.find_rfcomm_channel", side_effect=BudsError("SMEP not found")
    ), patch("galaxybridge.buds.disconnect_device") as disconnect, patch(
        "galaxybridge.buds.connect_device"
    ) as connect:
        result = BudsPatcher("02:00:00:00:00:01").apply()

    assert result.success is False
    assert result.error == "SMEP not found"
    disconnect.assert_not_called()
    connect.assert_not_called()

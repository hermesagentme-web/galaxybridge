from unittest.mock import patch

from galaxybridge.buds_control import (
    ACKNOWLEDGEMENT,
    EXTENDED_STATUS_UPDATED,
    NOISE_CONTROLS,
    NOISE_CONTROLS_UPDATE,
    NoiseControlClient,
    NoiseControlMode,
    WarmNoiseControlSession,
    decode_spp_frame,
    encode_spp_frame,
    extract_noise_mode,
    extract_spp_frames,
)


class FakeTransport:
    def __init__(self, frames: list[bytes]):
        self.frames = frames
        self.sent: list[bytes] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def send(self, frame: bytes) -> None:
        self.sent.append(frame)

    def read_for(self, _seconds: float) -> list[bytes]:
        return self.frames

    def read_until(self, _seconds: float, _predicate=None, settle_seconds: float = 0) -> list[bytes]:
        return self.frames

    def close(self) -> None:
        return None

    @property
    def closed(self) -> bool:
        return False


class SequencedTransport(FakeTransport):
    def __init__(self, batches: list[list[bytes]]):
        super().__init__([])
        self.batches = list(batches)
        self.close_calls = 0

    def read_until(self, _seconds: float, _predicate=None, settle_seconds: float = 0) -> list[bytes]:
        return self.batches.pop(0) if self.batches else []

    def close(self) -> None:
        self.close_calls += 1


def test_noise_control_frame_and_stream_codec():
    frame = encode_spp_frame(NOISE_CONTROLS, bytes([NoiseControlMode.ANC]))
    assert frame.hex(" ").upper() == "FD 04 00 78 01 D1 91 DD"
    assert decode_spp_frame(frame) == (NOISE_CONTROLS, b"\x01")

    buffer = bytearray(b"noise" + frame[:4])
    assert list(extract_spp_frames(buffer)) == []
    buffer.extend(frame[4:])
    assert list(extract_spp_frames(buffer)) == [frame]


def test_only_documented_noise_modes_are_accepted():
    assert NoiseControlMode.from_name("off") is NoiseControlMode.OFF
    assert NoiseControlMode.from_name("anc") is NoiseControlMode.ANC
    assert NoiseControlMode.from_name("ambient") is NoiseControlMode.AMBIENT
    try:
        NoiseControlMode.from_name("adaptive")
    except ValueError:
        pass
    else:
        raise AssertionError("an unsupported mode was accepted")


def test_noise_mode_readback_sources():
    ack = encode_spp_frame(ACKNOWLEDGEMENT, bytes([NOISE_CONTROLS, NoiseControlMode.ANC]))
    update = encode_spp_frame(NOISE_CONTROLS_UPDATE, bytes([NoiseControlMode.AMBIENT, 0]))
    status_payload = bytearray(13)
    status_payload[12] = NoiseControlMode.OFF
    status = encode_spp_frame(EXTENDED_STATUS_UPDATED, status_payload)
    assert extract_noise_mode(ack) is NoiseControlMode.ANC
    assert extract_noise_mode(update) is NoiseControlMode.AMBIENT
    assert extract_noise_mode(status) is NoiseControlMode.OFF


def test_set_mode_requires_matching_buds_readback(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    matching = encode_spp_frame(ACKNOWLEDGEMENT, bytes([NOISE_CONTROLS, NoiseControlMode.ANC]))
    transport = FakeTransport([matching])
    client = NoiseControlClient("02:00:00:00:00:01", lambda _address, _timeout: transport)
    with patch("galaxybridge.buds_control.device_connected", return_value=True):
        result = client.set_mode(NoiseControlMode.ANC, read_seconds=0)
    assert result.success is True
    assert result.reported_mode == "anc"
    assert transport.sent == [encode_spp_frame(NOISE_CONTROLS, b"\x01")]

    mismatch = FakeTransport(
        [encode_spp_frame(ACKNOWLEDGEMENT, bytes([NOISE_CONTROLS, NoiseControlMode.AMBIENT]))]
    )
    client = NoiseControlClient("02:00:00:00:00:01", lambda _address, _timeout: mismatch)
    with patch("galaxybridge.buds_control.device_connected", return_value=True):
        result = client.set_mode(NoiseControlMode.ANC, read_seconds=0)
    assert result.success is False
    assert result.reported_mode is None


def test_status_requires_an_explicit_report(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    transport = FakeTransport([])
    client = NoiseControlClient("02:00:00:00:00:01", lambda _address, _timeout: transport)
    with patch("galaxybridge.buds_control.device_connected", return_value=True):
        result = client.status(read_seconds=0)
    assert result.success is False
    assert "did not report" in result.error


def test_warm_session_reuses_profile_and_requires_fresh_set_ack():
    ambient = encode_spp_frame(NOISE_CONTROLS_UPDATE, bytes([NoiseControlMode.AMBIENT]))
    anc = encode_spp_frame(ACKNOWLEDGEMENT, bytes([NOISE_CONTROLS, NoiseControlMode.ANC]))
    transport = SequencedTransport([[ambient], [anc]])
    factory_calls: list[str] = []

    def factory(address: str, _timeout: float):
        factory_calls.append(address)
        return transport

    observed: list[NoiseControlMode] = []
    session = WarmNoiseControlSession(
        "02:00:00:00:00:01",
        transport_factory=factory,
        on_mode=observed.append,
    )
    with patch("galaxybridge.buds_control.device_connected", return_value=True):
        status = session.status()
        changed = session.set_mode(NoiseControlMode.ANC)
    session.close()

    assert status.reported_mode == "ambient"
    assert changed.success is True
    assert changed.reported_mode == "anc"
    assert factory_calls == ["02:00:00:00:00:01"]
    assert observed == [NoiseControlMode.AMBIENT, NoiseControlMode.ANC]
    assert transport.close_calls == 1


def test_warm_session_never_accepts_cached_mode_as_write_confirmation():
    anc = encode_spp_frame(NOISE_CONTROLS_UPDATE, bytes([NoiseControlMode.ANC]))
    transport = SequencedTransport([[anc], []])
    session = WarmNoiseControlSession(
        "02:00:00:00:00:01",
        transport_factory=lambda _address, _timeout: transport,
    )
    with patch("galaxybridge.buds_control.device_connected", return_value=True):
        assert session.status().reported_mode == "anc"
        result = session.set_mode(NoiseControlMode.ANC)
    assert result.success is False
    assert "no matching" in result.error

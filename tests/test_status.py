import json
import stat

from galaxybridge.status import BudsStatusWriter, NoiseControlStatusWriter


def test_status_writer_is_atomic_private_and_refreshable(tmp_path):
    now = [100.0]
    path = tmp_path / "galaxybridge" / "buds-status.json"
    writer = BudsStatusWriter(path, clock=lambda: now[0])

    writer.update("ready", as_ver=2, channel=29)
    first = json.loads(path.read_text())
    assert first == {
        "version": 1,
        "state": "ready",
        "detail": "",
        "updated_at": 100.0,
        "as_ver": 2,
        "channel": 29,
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    now[0] = 110.0
    writer.heartbeat()
    second = json.loads(path.read_text())
    assert second["state"] == "ready"
    assert second["updated_at"] == 110.0


def test_noise_status_uses_its_own_file(tmp_path):
    path = tmp_path / "noise.json"
    writer = NoiseControlStatusWriter(path, clock=lambda: 30.0)
    writer.update("ready", mode="ambient")
    assert json.loads(path.read_text()) == {
        "version": 1,
        "state": "ready",
        "detail": "",
        "mode": "ambient",
        "updated_at": 30.0,
    }

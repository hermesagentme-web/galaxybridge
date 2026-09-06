import json
import stat

from galaxybridge.buds_session import BudsSessionMarker


def test_session_marker_is_private_address_bound_and_short_lived(tmp_path):
    now = [100.0]
    path = tmp_path / "runtime" / "galaxybridge" / "buds-session.json"
    marker = BudsSessionMarker(path, clock=lambda: now[0], max_age_seconds=300)
    marker.remember("02:00:00:00:00:01", 2, 29)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert marker.load("02:00:00:00:00:01") == {
        "address": "02:00:00:00:00:01",
        "as_ver": 2,
        "channel": 29,
        "verified_at": 100.0,
    }
    assert marker.load("00:11:22:33:44:55") is None

    now[0] = 401.0
    assert marker.load("02:00:00:00:00:01") is None


def test_session_marker_rejects_unsafe_permissions_and_clears(tmp_path):
    path = tmp_path / "buds-session.json"
    path.write_text(json.dumps({"version": 1}))
    path.chmod(0o644)
    marker = BudsSessionMarker(path)
    assert marker.load("02:00:00:00:00:01") is None
    marker.clear()
    assert not path.exists()

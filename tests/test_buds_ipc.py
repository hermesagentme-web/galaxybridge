import shutil
import tempfile
from pathlib import Path

import pytest

from galaxybridge.buds_ipc import BudsControlServer, BudsIpcError, request_buds_daemon


@pytest.fixture
def socket_dir() -> Path:
    """AF_UNIX socket paths are capped near 108 bytes, so keep the test dir short."""
    directory = Path(tempfile.mkdtemp(prefix="gb-ipc-", dir="/tmp"))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def start_or_skip_restricted_socket(server: BudsControlServer) -> None:
    try:
        server.start()
    except BudsIpcError as exc:
        if "Operation not permitted" in str(exc):
            pytest.skip("the test sandbox blocks AF_UNIX bind")
        raise


def test_private_buds_ipc_round_trip(socket_dir: Path):
    socket_path = socket_dir / "buds.sock"
    idle_calls: list[bool] = []
    server = BudsControlServer(
        lambda request: {"success": True, "mode": request.get("mode")},
        idle=lambda: idle_calls.append(True),
        path=socket_path,
    )
    start_or_skip_restricted_socket(server)
    try:
        response = request_buds_daemon({"action": "noise_set", "mode": "anc"}, path=socket_path)
    finally:
        server.stop()
    assert response == {"success": True, "mode": "anc"}
    assert not socket_path.exists()


def test_ipc_rejects_unknown_action_without_crashing(socket_dir: Path):
    socket_path = socket_dir / "buds.sock"

    def reject(_request):
        raise ValueError("unsupported action")

    server = BudsControlServer(reject, path=socket_path)
    start_or_skip_restricted_socket(server)
    try:
        response = request_buds_daemon({"action": "firmware"}, path=socket_path)
    finally:
        server.stop()
    assert response["success"] is False
    assert response["error"] == "unsupported action"


def test_socket_path_length_is_validated(tmp_path: Path):
    over_long = tmp_path / ("d" * 120) / "buds.sock"
    with pytest.raises(BudsIpcError):
        request_buds_daemon({"action": "noise_status"}, path=over_long, timeout=0.5)

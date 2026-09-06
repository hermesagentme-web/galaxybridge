from unittest.mock import MagicMock, patch
from galaxybridge.buds_daemon import BudsDaemon
from galaxybridge.bluez import BlueZDeviceState
from galaxybridge.buds import PatchResult

ADDRESS = '02:00:00:00:00:01'


def test_power_session_renewal_and_duplicate_connection_suppression():
    marker = MagicMock()
    daemon = BudsDaemon(ADDRESS, auto_patch=True, max_attempts=1, session_marker=marker)
    connected = BlueZDeviceState(ADDRESS, '/device', True)
    disconnected = BlueZDeviceState(ADDRESS, '/device', False)
    with patch('galaxybridge.buds_daemon.device_state', return_value=connected), patch(
        'galaxybridge.buds_daemon.threading.Thread'
    ) as thread:
        daemon._on_connected()
        daemon._on_connected()
        assert thread.call_count == 1
    with patch('galaxybridge.buds_daemon.device_state', return_value=connected), patch(
        'galaxybridge.buds_daemon.BudsPatcher'
    ) as patcher:
        patcher.return_value.apply.return_value = PatchResult(True, ADDRESS, 29, 2, 4)
        daemon._patch_worker()
        assert daemon._patched_for_session
    with patch('galaxybridge.buds_daemon.device_state', return_value=disconnected):
        daemon._confirm_disconnected(daemon._disconnect_generation, 0)
    assert not daemon._patched_for_session
    with patch('galaxybridge.buds_daemon.device_state', return_value=connected), patch(
        'galaxybridge.buds_daemon.threading.Thread'
    ) as thread:
        daemon._on_connected()
        thread.assert_called_once()
    assert not daemon.auto_connect


def test_cooldown_does_not_write_when_buds_leave():
    daemon = BudsDaemon(ADDRESS, auto_patch=True, max_attempts=1)
    daemon._last_patch_started = 100
    daemon._stop_event = MagicMock()
    daemon._stop_event.wait.return_value = False
    with patch('galaxybridge.buds_daemon.time.monotonic', return_value=110), patch(
        'galaxybridge.buds_daemon.device_state', return_value=BlueZDeviceState(ADDRESS, '/device', False)
    ), patch('galaxybridge.buds_daemon.BudsPatcher') as patcher:
        daemon._patch_worker()
    daemon._stop_event.wait.assert_called_once_with(50)
    patcher.assert_not_called()
    assert not daemon._worker_active

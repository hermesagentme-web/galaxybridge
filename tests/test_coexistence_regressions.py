import socket
import threading
from unittest.mock import MagicMock, patch

from galaxybridge.buds_daemon import BudsDaemon
from galaxybridge.bluez import BlueZDeviceState
from galaxybridge.buds_ipc import BudsControlServer, request_buds_daemon
from test_buds_ipc import start_or_skip_restricted_socket


def test_late_reply_does_not_remove_ipc_server(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def handler(request):
        if request.get('action') == 'slow':
            entered.set()
            assert release.wait(3)
        return {'success': True}
    server = BudsControlServer(handler, path=tmp_path / 'control.sock')
    start_or_skip_restricted_socket(server)
    try:
        client = socket.socket(socket.AF_UNIX)
        client.connect(str(server.path))
        client.sendall(b'{"action":"slow"}\n')
        assert entered.wait(2)
        client.close()
        release.set()
        assert request_buds_daemon({'action': 'ping'}, path=server.path)['success']
    finally:
        release.set()
        server.stop()


def test_anc_read_releases_phone_manager_profile():
    daemon = BudsDaemon('02:00:00:00:00:01')
    daemon._noise_session = MagicMock()
    daemon._handle_control_request({'action': 'noise_status'})
    daemon._noise_session.close.assert_called_once()


def test_connection_echo_cannot_publish_ready_during_wizard():
    daemon = BudsDaemon('02:00:00:00:00:01')
    daemon._worker_active = True
    daemon._set_status('patching')
    with patch('galaxybridge.buds_daemon.device_state', return_value=BlueZDeviceState(daemon.address, '/device', True)):
        daemon._on_connected()
    assert daemon._current_state == 'patching'

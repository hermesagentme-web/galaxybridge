from galaxybridge.capabilities import parse_capabilities
from galaxybridge.buds import SMEP_UUID
from galaxybridge.buds_control import CONTROL_UUID
from galaxybridge.buds_daemon import BudsDaemon
import pytest


def test_unknown_model_never_becomes_validated_from_its_alias():
    report = parse_capabilities(f"Alias: Buds4 Pro\nUUID: {SMEP_UUID}\nUUID: {CONTROL_UUID}")
    assert report["smep_advertised"]
    assert report["noise_control_advertised"]
    assert report["model"] == "unverified model"


def test_absent_services_are_not_assumed():
    report = parse_capabilities("Modalias: bluetooth:v0075pA013d0001")
    assert report["model"] == "Galaxy Buds4 Pro"
    assert not report["smep_advertised"]


def test_request_for_other_device_cannot_control_daemon_headset():
    daemon = BudsDaemon("02:00:00:00:00:01")
    with pytest.raises(ValueError, match="different headset"):
        daemon._handle_control_request({"address": "02:00:00:00:00:02", "action": "reconnect"})

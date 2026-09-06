"""Read-only capability discovery: names are not trusted as device identity."""
import re
from .buds import SMEP_UUID, device_info
from .buds_control import CONTROL_UUID


def parse_capabilities(info: str) -> dict:
    uuids = set(re.findall(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', info.lower()))
    validated = bool(re.search(r'bluetooth:v0075pa013', info, re.I))
    return {
        'model': 'Galaxy Buds4 Pro' if validated else 'unverified model',
        'a2dp': '0000110b-0000-1000-8000-00805f9b34fb' in uuids,
        'smep_advertised': SMEP_UUID in uuids,
        'noise_control_advertised': CONTROL_UUID in uuids,
        'noise_control_validation': 'locally tested' if validated else 'not hardware validated',
        'multipoint': 'experimental; exact SDP channel and write readback required',
        'automatic_dual_reconnect': 'not implemented reliably',
    }


def capabilities(address: str) -> dict:
    return parse_capabilities(device_info(address))

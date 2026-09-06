"""Generate local launchers with absolute, escaped installation paths."""
import os
from pathlib import Path
import sys


def quoted(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$') + '"'


def main():
    data = Path(sys.argv[1]).resolve()
    config = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')).resolve()
    executable = str(data / 'galaxybridge/venv/bin/galaxybridge-ui')
    (data / 'applications/galaxybridge.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=GalaxyBridge\n'
        'Comment=Galaxy Buds controls\nIcon=audio-headphones\n'
        f'Exec={quoted(executable.replace("%", "%%"))}\nTerminal=false\nCategories=AudioVideo;Audio;Utility;\n'
    )
    python = str(data / 'galaxybridge/venv/bin/python').replace('%', '%%')
    environment = str(config / 'galaxybridge/environment').replace('%', '%%')
    (config / 'systemd/user/galaxybridge-buds.service').write_text(
        '[Unit]\nDescription=GalaxyBridge Buds controls\n'
        '[Service]\nType=simple\n'
        f'EnvironmentFile=-{quoted(environment)}\nExecStart={quoted(python)} -m galaxybridge.buds_daemon\n'
        'Restart=on-failure\nRestartSec=5\nRuntimeDirectory=galaxybridge\nRuntimeDirectoryMode=0700\n'
        'NoNewPrivileges=yes\n[Install]\nWantedBy=default.target\n'
    )


if __name__ == '__main__':
    main()

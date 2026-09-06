#!/usr/bin/env bash
set -euo pipefail
data_root="${XDG_DATA_HOME:-$HOME/.local/share}"
config_root="${XDG_CONFIG_HOME:-$HOME/.config}"
bin_root="${XDG_BIN_HOME:-$HOME/.local/bin}"
systemctl --user disable --now galaxybridge-buds.service 2>/dev/null || true
gnome-extensions disable galaxybridge@galaxybridge.local 2>/dev/null || true
# Preserve program/config data for recovery; remove only integration entry points.
for command in galaxybridgectl galaxybridge-ui galaxybridge-buds-daemon; do
  if [[ -L "$bin_root/$command" ]]; then unlink "$bin_root/$command"; fi
done
for file in "$config_root/systemd/user/galaxybridge-buds.service" "$data_root/applications/galaxybridge.desktop"; do
  if [[ -f "$file" ]]; then rm -- "$file"; fi
done
systemctl --user daemon-reload 2>/dev/null || true
echo "Launchers removed and service disabled. Program, extension and configuration files retained for recovery."

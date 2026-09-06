#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gnome=false
case "${1:-}" in
  --gnome) gnome=true ;;
  ""|--no-gnome) ;;
  *) echo "Usage: $0 [--gnome|--no-gnome]" >&2; exit 2 ;;
esac
data_root="${XDG_DATA_HOME:-$HOME/.local/share}"
config_root="${XDG_CONFIG_HOME:-$HOME/.config}"
bin_root="${XDG_BIN_HOME:-$HOME/.local/bin}"
venv="$data_root/galaxybridge/venv"
mkdir -p "$bin_root" "$config_root/galaxybridge" "$config_root/systemd/user" "$data_root/applications"
python3 -m venv --system-site-packages "$venv"
"$venv/bin/python" -m pip install --no-deps "$project_root"
"$venv/bin/python" -c 'import dbus, gi' || { echo "Install Python D-Bus and GObject bindings from your distribution."; exit 2; }
for command in galaxybridgectl galaxybridge-ui galaxybridge-buds-daemon; do
  ln -sfn "$venv/bin/$command" "$bin_root/$command"
done
install -m 0644 "$project_root/systemd/galaxybridge-buds.service" "$config_root/systemd/user/"
# A desktop launcher uses the installed venv directly, independent of login PATH.
"$venv/bin/python" -m galaxybridge.install_desktop "$data_root"
if "$gnome"; then
  target="$data_root/gnome-shell/extensions/galaxybridge@galaxybridge.local"
  mkdir -p "$target"
  install -m 0644 "$project_root/gnome-extension/galaxybridge@galaxybridge.local/"{extension.js,metadata.json} "$target/"
  echo "GNOME integration copied. Log out/in, then enable galaxybridge@galaxybridge.local."
fi
if [[ ! -e "$config_root/galaxybridge/environment" ]]; then
  install -m 0600 "$project_root/config/environment.example" "$config_root/galaxybridge/environment"
fi
systemctl --user daemon-reload 2>/dev/null || true
echo "Installed. Add $bin_root to PATH. Configure your Buds address before starting the daemon."
echo "No Bluetooth connection or service start was performed."

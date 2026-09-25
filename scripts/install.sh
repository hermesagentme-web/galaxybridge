#!/usr/bin/env bash
# GalaxyBridge installer and setup (distribution aware).
#
# Usage: bash scripts/install.sh [options]
#   --system-packages  install the distribution packages first (asks for privileges)
#   --configure        guided Buds setup: device address, service start
#   --gnome            force the GNOME Shell 50 extension on
#   --no-gnome         never install the GNOME Shell extension
#   --yes, -y          non-interactive: accept defaults
#   -h, --help         show this help
#
# Supported families: Debian/Ubuntu (apt), Fedora/RHEL (dnf), Arch (pacman),
# openSUSE (zypper). The panel follows the desktop GTK theme; the GNOME Shell
# extension is optional and other desktops use galaxybridge-ui or
# "galaxybridgectl menu".
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

system_packages=false
configure=false
gnome_mode="auto"
assume_yes=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --system-packages) system_packages=true ;;
    --configure) configure=true ;;
    --gnome) gnome_mode="yes" ;;
    --no-gnome) gnome_mode="no" ;;
    --yes|-y) assume_yes=true ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }

detect_family() {
  local id="" id_like=""
  if [[ -r /etc/os-release ]]; then
    id="$(. /etc/os-release && printf '%s' "${ID:-}")"
    id_like="$(. /etc/os-release && printf '%s' "${ID_LIKE:-}")"
  fi
  case "$id $id_like" in
    *debian*|*ubuntu*|*mint*|*pop*|*kali*|*raspbian*|*elementary*) echo debian ;;
    *fedora*|*rhel*|*centos*|*rocky*|*almalinux*|*nobara*)          echo fedora ;;
    *arch*|*manjaro*|*endeavouros*|*garuda*)                          echo arch ;;
    *suse*|*sles*)                                                    echo suse ;;
    *)                                                                echo unknown ;;
  esac
}

run_as_root() {
  if [[ $EUID -eq 0 ]]; then
    "$@"
  elif command -v pkexec >/dev/null 2>&1 && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    pkexec env DEBIAN_FRONTEND=noninteractive "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo env DEBIAN_FRONTEND=noninteractive "$@"
  else
    warn "no pkexec/sudo available; run manually as root: $*"
    return 1
  fi
}

install_system_packages() {
  local family="$1"
  log "Installing system packages for family: $family"
  case "$family" in
    debian) run_as_root apt-get update -qq
            run_as_root apt-get install -y python3 python3-venv python3-pip \
              python3-dbus python3-gi gir1.2-gtk-3.0 bluez ;;
    fedora) run_as_root dnf install -y python3 python3-pip python3-dbus \
              python3-gobject gtk3 bluez ;;
    arch)   run_as_root pacman -S --needed --noconfirm python python-pip \
              python-dbus python-gobject gtk3 bluez bluez-utils ;;
    suse)   run_as_root zypper --non-interactive install python3 python3-pip \
              python3-dbus-python python3-gobject-Gdk typelib-1_0-Gtk-3_0 bluez ;;
    *)      warn "Unsupported distribution: install python3 (venv, pip), dbus-python, PyGObject, GTK 3 bindings and bluez yourself." ;;
  esac
}

check_python_bindings() {
  local python="$1"
  if ! "$python" -c 'import dbus, gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
    warn "Python D-Bus, GObject or GTK 3 bindings are missing for $python."
    warn "Install your distribution's python3-dbus, PyGObject and GTK 3 typelib packages, then re-run."
    return 1
  fi
}

gnome_shell_major() {
  command -v gnome-shell >/dev/null 2>&1 || return 1
  gnome-shell --version 2>/dev/null | awk '{print $3}' | cut -d. -f1
}

install_python() {
  local data_root="${XDG_DATA_HOME:-$HOME/.local/share}"
  local config_root="${XDG_CONFIG_HOME:-$HOME/.config}"
  local bin_root="${XDG_BIN_HOME:-$HOME/.local/bin}"
  local venv="$data_root/galaxybridge/venv"
  mkdir -p "$bin_root" "$config_root/galaxybridge" "$config_root/systemd/user" "$data_root/applications"
  python3 -m venv --system-site-packages "$venv"
  "$venv/bin/python" -m pip install --quiet --no-deps "$project_root"
  check_python_bindings "$venv/bin/python"
  local command
  for command in galaxybridgectl galaxybridge-ui galaxybridge-buds-daemon; do
    ln -sfn "$venv/bin/$command" "$bin_root/$command"
  done
  "$venv/bin/python" -m galaxybridge.install_desktop "$data_root"
  systemctl --user daemon-reload 2>/dev/null || true
}

install_gnome_extension() {
  local data_root="${XDG_DATA_HOME:-$HOME/.local/share}"
  local target="$data_root/gnome-shell/extensions/galaxybridge@galaxybridge.local"
  mkdir -p "$target"
  install -m 0644 "$project_root/gnome-extension/galaxybridge@galaxybridge.local/"{extension.js,metadata.json} "$target/"
  log "GNOME extension copied. Log out/in, then run: gnome-extensions enable galaxybridge@galaxybridge.local"
}

configure_buds() {
  local config_root="${XDG_CONFIG_HOME:-$HOME/.config}"
  local environment="$config_root/galaxybridge/environment"
  mkdir -p "$config_root/galaxybridge"
  if [[ -f "$environment" ]] && grep -q '^GALAXYBRIDGE_BUDS_ADDRESS=..*' "$environment"; then
    log "Keeping existing configuration in $environment"
    grep '^GALAXYBRIDGE_BUDS_ADDRESS=' "$environment" | sed 's/^/    /'
    return 0
  fi

  local devices
  devices="$(bluetoothctl devices Paired 2>/dev/null || bluetoothctl devices 2>/dev/null || true)"
  if [[ -z "$devices" ]]; then
    warn "No paired Bluetooth device found. Pair the Buds in your desktop Bluetooth settings first, then re-run with --configure."
    return 0
  fi

  local address=""
  if $assume_yes || [[ ! -t 0 ]]; then
    address="$(printf '%s\n' "$devices" | head -n1 | awk '{print $2}')"
    log "Non-interactive: selecting first paired device $address"
  else
    echo "Paired Bluetooth devices:"
    printf '%s\n' "$devices" | nl -ba
    local choice
    read -r -p "Number of your Galaxy Buds [1]: " choice
    choice="${choice:-1}"
    address="$(printf '%s\n' "$devices" | sed -n "${choice}p" | awk '{print $2}')"
    [[ -n "$address" ]] || { warn "Invalid selection."; return 1; }
  fi

  {
    echo "GALAXYBRIDGE_BUDS_ADDRESS=$address"
    echo "GALAXYBRIDGE_BUDS_AUTO_CONNECT=false"
    echo "GALAXYBRIDGE_BUDS_AUTO_PATCH=false"
    echo "GALAXYBRIDGE_BUDS_PATCH_COOLDOWN_SECONDS=60"
  } > "$environment"
  chmod 0600 "$environment"
  log "Configuration written to $environment"

  if systemctl --user status >/dev/null 2>&1; then
    systemctl --user enable --now galaxybridge-buds.service
    log "galaxybridge-buds.service enabled and started"
  else
    warn "systemd user session unavailable; start it later with: galaxybridge-buds-daemon"
  fi
}

# --- main ------------------------------------------------------------------

family="$(detect_family)"
log "GalaxyBridge installer · distribution family: $family"

if $system_packages; then
  install_system_packages "$family"
else
  log "Skipping system packages (--system-packages not given)"
fi

if ! command -v python3 >/dev/null 2>&1; then
  warn "python3 not found; install your distribution's Python 3 first."
  exit 1
fi

install_python

if [[ "$gnome_mode" == "yes" ]] || { [[ "$gnome_mode" == "auto" ]] && [[ "$(gnome_shell_major || true)" == "50" ]]; }; then
  install_gnome_extension
else
  log "GNOME Shell 50 extension not installed (desktop is not GNOME 50 or --no-gnome)"
fi

if $configure; then
  configure_buds
else
  log "Skipping Buds configuration (--configure not given)"
fi

echo
log "Installed. Add ${XDG_BIN_HOME:-$HOME/.local/bin} to PATH if needed."
echo "    galaxybridge-ui        graphical panel (any desktop)"
echo "    galaxybridgectl menu   terminal menu (servers, TTYs)"
echo "    galaxybridgectl doctor environment check"

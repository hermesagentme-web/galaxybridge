#!/usr/bin/env bash
# GalaxyBridge one-line installer (any Linux distribution).
#
#   curl -fsSL https://raw.githubusercontent.com/hermesagentme-web/galaxybridge/main/install.sh | bash
#
# Detects the distribution, installs the right packages, then runs the guided
# setup. Options below are forwarded to scripts/install.sh:
#   --yes  --no-gnome  --gnome  --system-packages  --configure
#
# Environment overrides:
#   GALAXYBRIDGE_REPO  repository to clone (default: the public GitHub repo)
#   GALAXYBRIDGE_DIR   checkout directory (default: ~/.local/share/galaxybridge/src)
set -euo pipefail

repo="${GALAXYBRIDGE_REPO:-https://github.com/hermesagentme-web/galaxybridge}"
target="${GALAXYBRIDGE_DIR:-$HOME/.local/share/galaxybridge/src}"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }

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

if ! command -v git >/dev/null 2>&1; then
  log "Installing git"
  id="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-} ${ID_LIKE:-}")"
  case "$id" in
    *debian*|*ubuntu*|*mint*|*pop*|*kali*) run_as_root apt-get update -qq && run_as_root apt-get install -y git ;;
    *fedora*|*rhel*|*centos*)             run_as_root dnf install -y git ;;
    *arch*|*manjaro*)                     run_as_root pacman -S --needed --noconfirm git ;;
    *suse*|*sles*)                        run_as_root zypper --non-interactive install git ;;
    *) warn "git is required; install it and re-run."; exit 1 ;;
  esac
fi

if [[ -d "$target/.git" ]]; then
  log "Updating existing checkout in $target"
  git -C "$target" pull --ff-only
else
  log "Cloning GalaxyBridge into $target"
  git clone --depth 1 "$repo" "$target"
fi

exec bash "$target/scripts/install.sh" --system-packages --configure "$@"

#!/usr/bin/env bash
#
# Modric Agent installer (Linux / macOS).
#
#   curl -fsSL https://raw.githubusercontent.com/mangosteen-lab/modric-agent/master/scripts/install.sh | sudo bash
#
# Downloads the latest public release, installs it to /opt/mangosteen/modric-agent,
# prompts for the Toil connection settings, and registers the OS service (systemd on
# Linux, launchd on macOS).
#
# Non-interactive: pre-set the MODRIC_* env vars and no prompts are shown, e.g.
#   MODRIC_TOIL_WSS_URL=wss://toil/ws/soil MODRIC_TOIL_API_KEY=key \
#   MODRIC_AGENT_LABELS="template=LINUX_ABA" sudo -E bash install.sh
#
# Env overrides:
#   MODRIC_AGENT_HOME       install dir            (default: /opt/mangosteen/modric-agent)
#   MODRIC_AGENT_TARBALL_URL  pin a specific tarball / internal mirror (skips discovery)
#   MODRIC_TOIL_WSS_URL, MODRIC_TOIL_API_KEY, MODRIC_AGENT_NAME, MODRIC_AGENT_CAPACITY,
#   MODRIC_AGENT_LABELS, MODRIC_AGENT_UPGRADE_CHANNEL, MODRIC_AGENT_AUTO_UPGRADE
#   FORCE_CONFIG=1          overwrite an existing conf/config.ini
set -euo pipefail

REPO="${MODRIC_AGENT_REPO:-mangosteen-lab/modric-agent}"
HOME_DIR="${MODRIC_AGENT_HOME:-/opt/mangosteen/modric-agent}"
API_LATEST="https://api.github.com/repos/${REPO}/releases/latest"

log()  { printf '\033[1;34m>>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

# --- preconditions ---------------------------------------------------------
[ "$(id -u)" -eq 0 ] || die "Please run as root (e.g. pipe to 'sudo bash')."
command -v curl >/dev/null || die "curl is required."
command -v tar  >/dev/null || die "tar is required."

# Prompt helper: reads from the terminal even when the script is piped via curl|bash.
# Skips the prompt when the env var is already set. Usage: ask VAR "Prompt" "default"
ask() {
  local var="$1" prompt="$2" default="${3:-}" cur input
  cur="$(printf '%s' "${!var:-}")"
  if [ -n "$cur" ]; then return 0; fi          # already provided via env
  if [ ! -t 0 ] && [ ! -e /dev/tty ]; then     # no terminal -> must use env/default
    [ -n "$default" ] || die "Missing required input for $var (no terminal to prompt)."
    printf -v "$var" '%s' "$default"; return 0
  fi
  if [ -n "$default" ]; then
    read -r -p "$prompt [$default]: " input </dev/tty || true
    printf -v "$var" '%s' "${input:-$default}"
  else
    while [ -z "${input:-}" ]; do read -r -p "$prompt: " input </dev/tty || true; done
    printf -v "$var" '%s' "$input"
  fi
}

# --- 1. resolve the tarball URL -------------------------------------------
if [ -n "${MODRIC_AGENT_TARBALL_URL:-}" ]; then
  TARBALL_URL="$MODRIC_AGENT_TARBALL_URL"
else
  log "Finding the latest release of $REPO"
  TARBALL_URL="$(curl -fsSL "$API_LATEST" \
    | grep -o '"browser_download_url": *"[^"]*\.tar\.gz"' | head -1 \
    | sed 's/.*"\(https[^"]*\)"/\1/')"
  [ -n "$TARBALL_URL" ] || die "Could not find a .tar.gz asset in the latest release."
fi
TARBALL_NAME="$(basename "$TARBALL_URL")"
log "Release artifact: $TARBALL_NAME"

# --- 2. download (+ verify against SHA256SUMS if present) ------------------
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$TARBALL_URL" -o "$TMP/$TARBALL_NAME"
SUMS_URL="$(dirname "$TARBALL_URL")/SHA256SUMS"
if curl -fsSL "$SUMS_URL" -o "$TMP/SHA256SUMS" 2>/dev/null; then
  ( cd "$TMP" && grep " $TARBALL_NAME\$" SHA256SUMS | sha256sum -c - >/dev/null 2>&1 ) \
    && log "Checksum OK" || warn "Checksum could not be verified — continuing."
else
  warn "No SHA256SUMS published for this release — skipping checksum."
fi

# --- 3. extract into the install dir (preserve existing config) ------------
log "Installing to $HOME_DIR"
mkdir -p "$HOME_DIR"
tar -xzf "$TMP/$TARBALL_NAME" --strip-components=1 -C "$HOME_DIR"

# --- 4. uv (per-project venv) ---------------------------------------------
if ! command -v uv >/dev/null; then
  log "Installing uv"
  curl -fsSL https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null || die "uv is not on PATH after install."

log "Syncing dependencies (uv sync)"
( cd "$HOME_DIR" && uv sync )

# --- 5. prompt for connection settings ------------------------------------
ask MODRIC_TOIL_WSS_URL       "Toil WebSocket URL (wss://host/ws/soil)"
ask MODRIC_TOIL_API_KEY       "Toil API key"
ask MODRIC_AGENT_NAME         "Machine name" "$(hostname)"
ask MODRIC_AGENT_LABELS       "Labels (comma-separated key=value)" ""
ask MODRIC_AGENT_CAPACITY     "Max concurrent jobs" "10"
ask MODRIC_AGENT_UPGRADE_CHANNEL "Upgrade channel" "stable"
ask MODRIC_AGENT_AUTO_UPGRADE "Auto-upgrade (true/false)" "true"
export MODRIC_TOIL_WSS_URL MODRIC_TOIL_API_KEY MODRIC_AGENT_NAME MODRIC_AGENT_LABELS \
       MODRIC_AGENT_CAPACITY MODRIC_AGENT_UPGRADE_CHANNEL MODRIC_AGENT_AUTO_UPGRADE

# --- 6. render conf/config.ini (reuses app.bootstrap) ----------------------
CONFIG="$HOME_DIR/conf/config.ini"
export MODRIC_AGENT_CONFIG="$CONFIG"
if [ -f "$CONFIG" ] && [ -z "${FORCE_CONFIG:-}" ]; then
  log "Keeping existing $CONFIG (set FORCE_CONFIG=1 to overwrite)"
else
  log "Writing $CONFIG"
  ( cd "$HOME_DIR" && uv run python -c "from app.bootstrap import render_config; render_config()" )
fi

# --- 7. install + start the OS service ------------------------------------
log "Installing the modric-agent service"
( cd "$HOME_DIR" && uv run python -m app.main service install )

log "Done. Machine '${MODRIC_AGENT_NAME}' should appear in Toil shortly."
log "Logs: journalctl -u modric-agent -f   (and $HOME_DIR/logs/agent.log)"

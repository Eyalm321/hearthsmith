#!/usr/bin/env bash
# Installs CLIs, config, the heartbeat timer, the avatar service + app launcher (+ autostart).
# Run from YOUR terminal (needs your login session bus). Idempotent.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"

# CLIs
mkdir -p ~/.local/bin
for b in forge forged forge-mcp forge-sprite-preview forge-sprite-icon; do ln -sf "$ROOT/.venv/bin/$b" ~/.local/bin/$b; done
install -m 755 "$HERE/forge-sprite" ~/.local/bin/forge-sprite

# config + secrets
mkdir -p ~/.config/forge
[ -f ~/.config/forge/config.yaml ] || cp "$HERE/config.example.yaml" ~/.config/forge/config.yaml
if [ ! -f ~/.config/forge/env ]; then
  # pull the OpenRouter key from the dsh secrets file if present; never echoes it
  { echo "# sourced by forged.service"; grep -E "^DSH_OPENROUTER_API_KEY=" ~/.dsh-jarvis/secrets/secrets.env 2>/dev/null || true; } > ~/.config/forge/env
  chmod 600 ~/.config/forge/env
fi

# sprite pack: slice the shipped sheet into ~/.config/forge/pack unless one already exists
# (FORGE_SHEET=blacksmith-b for the stockier dwarf cut)
if [ ! -f ~/.config/forge/pack/manifest.json ]; then
  "$ROOT/.venv/bin/forge-sprite-slice" "$ROOT/assets/sheets/${FORGE_SHEET:-blacksmith-a}.png" ~/.config/forge/pack --cell 96
fi

# systemd user units: heartbeat timer + avatar service
mkdir -p ~/.config/systemd/user
cp "$HERE/forged.service" "$HERE/forged.timer" "$HERE/forge-sprite.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now forged.timer
systemctl --user enable forge-sprite.service
systemctl --user restart forge-sprite.service

# app launcher + icon (+ autostart is the service's WantedBy=graphical-session.target)
mkdir -p ~/.local/share/applications ~/.local/share/icons/hicolor/256x256/apps
cp "$HERE/forge.png" ~/.local/share/icons/hicolor/256x256/apps/forge.png
cp "$HERE/forge.desktop" ~/.local/share/applications/forge.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null || true
gtk-update-icon-cache -q ~/.local/share/icons/hicolor 2>/dev/null || true

systemctl --user list-timers forged.timer --no-pager
systemctl --user is-active forge-sprite.service && echo "avatar: running (forge-sprite toggle to hide)"
echo "first nag: systemctl --user start forged.service && journalctl --user -u forged -n 20"

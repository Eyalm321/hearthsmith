#!/usr/bin/env bash
# Installs config + user timer. Run from YOUR terminal (needs your login session bus).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.config/forge ~/.config/systemd/user
[ -f ~/.config/forge/config.yaml ] || cp "$HERE/config.example.yaml" ~/.config/forge/config.yaml
if [ ! -f ~/.config/forge/env ]; then
  # pull the OpenRouter key from the dsh secrets file if present; never echoes it
  { echo "# sourced by forged.service"; grep -E "^DSH_OPENROUTER_API_KEY=" ~/.dsh-jarvis/secrets/secrets.env 2>/dev/null || true; } > ~/.config/forge/env
  chmod 600 ~/.config/forge/env
fi
cp "$HERE/forged.service" "$HERE/forged.timer" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now forged.timer
systemctl --user list-timers forged.timer --no-pager
echo "first run: systemctl --user start forged.service && journalctl --user -u forged -n 20"

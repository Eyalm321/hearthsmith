#!/usr/bin/env bash
# Installs CLIs, config, the heartbeat timer, the avatar service + app launcher (+ autostart).
# Run from YOUR terminal (needs your login session bus). Idempotent.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"

# CLIs
mkdir -p ~/.local/bin
for b in hearthsmith hearthsmithd hearthsmith-mcp hearthsmith-look hearthsmith-ear hearthsmith-sprite-preview hearthsmith-sprite-icon; do ln -sf "$ROOT/.venv/bin/$b" ~/.local/bin/$b; done
install -m 755 "$HERE/hearthsmith-sprite" ~/.local/bin/hearthsmith-sprite

# config + secrets
mkdir -p ~/.config/hearthsmith
[ -f ~/.config/hearthsmith/config.yaml ] || cp "$HERE/config.example.yaml" ~/.config/hearthsmith/config.yaml
if [ ! -f ~/.config/hearthsmith/env ]; then
  # KEY=value lines sourced by hearthsmithd.service (and `hearthsmith say` from the sprite).
  # Seeded from the current environment if the key is exported; never echoed.
  { echo "# sourced by hearthsmithd.service"
    [ -n "${OPENROUTER_API_KEY:-}" ] && echo "OPENROUTER_API_KEY=$OPENROUTER_API_KEY"
    true; } > ~/.config/hearthsmith/env
  chmod 600 ~/.config/hearthsmith/env
  [ -n "${OPENROUTER_API_KEY:-}" ] || echo "NOTE: put OPENROUTER_API_KEY=... in ~/.config/hearthsmith/env"
fi

# sprite pack: slice the shipped sheet into ~/.config/hearthsmith/pack unless one already exists
# (HEARTHSMITH_SHEET=blacksmith-b for the stockier dwarf cut)
if [ ! -f ~/.config/hearthsmith/pack/manifest.json ]; then
  "$ROOT/.venv/bin/hearthsmith-sprite-slice" "$ROOT/assets/sheets/${HEARTHSMITH_SHEET:-blacksmith-a}.png" ~/.config/hearthsmith/pack --cell 96
fi

# systemd user units: heartbeat timer + avatar service
mkdir -p ~/.config/systemd/user
cp "$HERE/hearthsmithd.service" "$HERE/hearthsmithd-now.service" "$HERE/hearthsmithd.timer" ~/.config/systemd/user/
sed "s|@ROOT@|$ROOT|g" "$HERE/hearthsmith-sprite.service" > ~/.config/systemd/user/hearthsmith-sprite.service
cp "$HERE/hearthsmith-ear.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hearthsmith-ear.service
systemctl --user enable --now hearthsmithd.timer
systemctl --user enable hearthsmith-sprite.service
systemctl --user restart hearthsmith-sprite.service

# talk to him: a GNOME shortcut for one spoken turn (ear.hotkey, default Super+J), added to the
# custom keybindings list without touching the ones already there
KB=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/hearthsmith-ear/
HOTKEY="$("$ROOT/.venv/bin/python" -c 'from hearthsmith import config; print(config.load().ear.hotkey)')"
if [ -n "$HOTKEY" ]; then
  LIST="$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)"
  case "$LIST" in *"$KB"*) ;; "@as []"|"[]") LIST="['$KB']" ;; *) LIST="${LIST%]}, '$KB']" ;; esac
  gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$LIST"
  S="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$KB"
  gsettings set "$S" name "hearthsmith: listen"
  gsettings set "$S" command "$HOME/.local/bin/hearthsmith-ear listen"
  gsettings set "$S" binding "$HOTKEY"
fi

# computer use: a11y bus on + window-geometry extension (Wayland hides frame rects otherwise)
gsettings set org.gnome.desktop.interface toolkit-accessibility true
mkdir -p ~/.local/share/gnome-shell/extensions
rm -rf ~/.local/share/gnome-shell/extensions/hearthsmith-windows@hearthsmith
cp -r "$HERE/gnome-extension/hearthsmith-windows@hearthsmith" ~/.local/share/gnome-shell/extensions/
gnome-extensions enable hearthsmith-windows@hearthsmith 2>/dev/null || true
id -nG | grep -qw input || echo "NOTE: add yourself to the input group for /dev/uinput: sudo usermod -aG input $USER (re-login)"
echo "NOTE: new GNOME extensions load on next login (Wayland can't hot-reload the shell)"

# app launcher + icon (+ autostart is the service's WantedBy=graphical-session.target)
mkdir -p ~/.local/share/applications ~/.local/share/icons/hicolor/256x256/apps
cp "$HERE/hearthsmith.png" ~/.local/share/icons/hicolor/256x256/apps/hearthsmith.png
cp "$HERE/hearthsmith.desktop" ~/.local/share/applications/hearthsmith.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null || true
gtk-update-icon-cache -q ~/.local/share/icons/hicolor 2>/dev/null || true

systemctl --user list-timers hearthsmithd.timer --no-pager
systemctl --user is-active hearthsmith-sprite.service && echo "avatar: running (hearthsmith-sprite toggle to hide)"
echo "first nag: systemctl --user start hearthsmithd.service && journalctl --user -u hearthsmithd -n 20"

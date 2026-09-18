#!/usr/bin/env bash
# One-time: move an install made under the old name (forge) to hearthsmith. Idempotent-ish:
# every step is skipped when the old thing is already gone. Run from YOUR terminal, then
# contrib/install.sh. Delete this file once nobody has a forge install left.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"

# services under the old names
for u in forge-sprite.service forged.timer forge-voice.service; do
  systemctl --user disable --now "$u" 2>/dev/null || true
done
systemctl --user stop forged.service forged-now.service 2>/dev/null || true
rm -f ~/.config/systemd/user/{forge-sprite.service,forged.service,forged-now.service,forged.timer,forge-voice.service}

# config + state: same files, new home; the db and env keys are renamed in place
[ -d ~/.config/forge ] && [ ! -d ~/.config/hearthsmith ] && mv ~/.config/forge ~/.config/hearthsmith
[ -d ~/.local/state/forge ] && [ ! -d ~/.local/state/hearthsmith ] && mv ~/.local/state/forge ~/.local/state/hearthsmith
[ -f ~/.local/state/hearthsmith/forge.db ] && mv ~/.local/state/hearthsmith/forge.db ~/.local/state/hearthsmith/hearthsmith.db
if [ -f ~/.config/hearthsmith/env ]; then
  sed -i 's/^DSH_OPENROUTER_API_KEY=/OPENROUTER_API_KEY=/; s/forged\.service/hearthsmithd.service/' ~/.config/hearthsmith/env
fi
if [ -f ~/.config/hearthsmith/config.yaml ]; then
  sed -i 's/DSH_OPENROUTER_API_KEY/OPENROUTER_API_KEY/g; s/^\(\s*delegate_queue:\s*\)forge$/\1hearthsmith/' ~/.config/hearthsmith/config.yaml
fi

# CLIs, launcher, icon, GNOME extension under the old names
rm -f ~/.local/bin/{forge,forged,forge-mcp,forge-look,forge-sprite,forge-sprite-icon,forge-sprite-preview}
rm -f ~/.local/share/applications/forge.desktop ~/.local/share/icons/hicolor/256x256/apps/forge.png
gnome-extensions disable forge-windows@forge 2>/dev/null || true
rm -rf ~/.local/share/gnome-shell/extensions/forge-windows@forge

# Claude Code's MCP entry: same server, new name and binary
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path.home() / ".claude.json"
if p.exists():
    d = json.loads(p.read_text())
    changed = False
    for scope in [d] + [v for v in d.get("projects", {}).values() if isinstance(v, dict)]:
        servers = scope.get("mcpServers") or {}
        if "forge" in servers and "hearthsmith" not in servers:
            s = servers.pop("forge")
            s["command"] = s.get("command", "").replace("forge-mcp", "hearthsmith-mcp")
            servers["hearthsmith"] = s
            changed = True
    if changed:
        p.write_text(json.dumps(d, indent=2))
        print("~/.claude.json: mcp server forge -> hearthsmith")
EOF

# the voice server unit is installed by hand (see contrib/voice-server/README.md)
[ -f ~/.config/systemd/user/hearthsmith-voice.service ] || cp "$HERE/voice-server/hearthsmith-voice.service" ~/.config/systemd/user/
systemctl --user daemon-reload

echo "migrated. now: $ROOT/contrib/install.sh"
echo "the checkout itself can be renamed any time: mv $ROOT ~/dev/hearthsmith && cd ~/dev/hearthsmith && uv sync && contrib/install.sh"

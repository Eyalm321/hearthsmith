"""Drive the user's own running Firefox over WebDriver BiDi (Firefox ≥ 129 is BiDi-only; no CDP).

He opens a real tab in the browser you're already using, you can watch and take over at any
point. Requires Firefox started with `--remote-debugging-port 9222` (contrib/firefox.desktop
adds it to every launch). Without the port we fall back to `firefox --new-tab URL` — he can
still open the page for you, just not click around in it.

Minimal client: session.new, tab create/activate/navigate, script.evaluate (JSON in/out),
real pointer clicks and key typing via input.performActions.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
from dataclasses import dataclass

from websockets.sync.client import connect

PORT = int(os.environ.get("FORGE_FIREFOX_PORT", "9222"))
ENTER = ""

OBSERVE_JS = r"""
(() => {
  const max = %d;
  const out = []; const seen = new Set();
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && cs.visibility !== 'hidden' && cs.display !== 'none'
      && r.bottom > 0 && r.top < innerHeight * 1.5; };
  const q = 'a[href], button, input, textarea, select, [role=button], [role=link], [role=tab], [role=menuitem], [role=option], [role=checkbox], [role=switch], [role=combobox], [role=searchbox], [role=textbox], [contenteditable=true], [onclick], summary';
  let i = 0;
  for (const el of Array.from(document.querySelectorAll(q))) {
    if (seen.has(el) || !vis(el) || out.length >= max) continue;
    seen.add(el);
    const tag = el.tagName.toLowerCase(); const type = el.type || '';
    const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80);
    const label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || '';
    const role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'button' ? 'button' : /input|textarea|select/.test(tag) ? (type || tag) : tag);
    if (!text && !label && !el.href) continue;
    el.setAttribute('data-forge-i', String(i));
    const r = el.getBoundingClientRect();
    out.push({ i, role, text, label, href: el.href ? String(el.href).slice(0, 120) : undefined,
      fillable: (/input|textarea/.test(tag) && !/button|submit|checkbox|radio|hidden/.test(type)) || el.isContentEditable || ['textbox','searchbox','combobox'].includes(role),
      x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) });
    i++;
  }
  return JSON.stringify({ url: location.href, title: document.title, candidates: out });
})()
"""


@dataclass
class Tab:
    context: str


class Firefox:
    def __init__(self, port: int = PORT):
        self.port = port
        self.ws = None
        self._ids = itertools.count(1)

    # -- transport ---------------------------------------------------------------------------

    def connect(self) -> bool:
        try:
            self.ws = connect(f"ws://127.0.0.1:{self.port}/session", open_timeout=3)
            self.call("session.new", {"capabilities": {}})
            return True
        except Exception:  # noqa: BLE001 — no port = not driveable, caller falls back
            self.ws = None
            return False

    def close(self) -> None:
        if self.ws:
            try:
                self.call("session.end", {})
            except Exception:  # noqa: BLE001
                pass
            self.ws.close()
            self.ws = None

    def call(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        mid = next(self._ids)
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=timeout))
            if msg.get("id") != mid:
                continue  # events / other replies
            if msg.get("type") == "error":
                raise RuntimeError(f"{method}: {msg.get('error')}: {msg.get('message')}")
            return msg.get("result", {})

    # -- tabs --------------------------------------------------------------------------------

    def new_tab(self, url: str | None = None) -> Tab:
        ctx = self.call("browsingContext.create", {"type": "tab"})["context"]
        self.call("browsingContext.activate", {"context": ctx})
        if url:
            self.navigate(Tab(ctx), url)
        return Tab(ctx)

    def navigate(self, tab: Tab, url: str) -> None:
        self.call("browsingContext.navigate", {"context": tab.context, "url": url, "wait": "interactive"},
                  timeout=45)

    # -- page --------------------------------------------------------------------------------

    def eval_json(self, tab: Tab, expression: str):
        r = self.call("script.evaluate", {"expression": expression, "target": {"context": tab.context},
                                          "awaitPromise": True, "resultOwnership": "none"})
        if r.get("type") != "success":
            raise RuntimeError(f"script: {r.get('exceptionDetails', r)}")
        v = r["result"]
        return json.loads(v["value"]) if v.get("type") == "string" else v.get("value")

    def observe(self, tab: Tab, max_items: int = 60) -> dict:
        return self.eval_json(tab, OBSERVE_JS % max_items)

    def text(self, tab: Tab, max_chars: int = 4000) -> str:
        return self.eval_json(tab, f"JSON.stringify((document.body?.innerText||'').replace(/\\s+/g,' ').trim().slice(0,{max_chars}))")

    def scroll(self, tab: Tab, dy: int = 700) -> None:
        self.eval_json(tab, f"(window.scrollBy(0,{dy}), 'null')")

    # -- input -------------------------------------------------------------------------------

    def click(self, tab: Tab, x: int, y: int) -> None:
        self.call("input.performActions", {"context": tab.context, "actions": [
            {"type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"}, "actions": [
                {"type": "pointerMove", "x": x, "y": y},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerUp", "button": 0}]}]})

    def type_text(self, tab: Tab, text: str, enter: bool = True) -> None:
        keys = []
        for ch in text + (ENTER if enter else ""):
            keys += [{"type": "keyDown", "value": ch}, {"type": "keyUp", "value": ch}]
        self.call("input.performActions", {"context": tab.context, "actions": [
            {"type": "key", "id": "kb", "actions": keys}]})

    def fill(self, tab: Tab, idx: int, value: str, enter: bool = True) -> None:
        self.eval_json(tab, f"(() => {{ const e = document.querySelector('[data-forge-i=\"{idx}\"]'); "
                            f"e.focus(); if ('value' in e) e.value = ''; return 'null'; }})()")
        self.type_text(tab, value, enter)

    def click_idx(self, tab: Tab, idx: int) -> None:
        r = self.eval_json(tab, f"(() => {{ const e = document.querySelector('[data-forge-i=\"{idx}\"]'); "
                                f"e.scrollIntoView({{block:'center'}}); const b = e.getBoundingClientRect(); "
                                f"return JSON.stringify([Math.round(b.left+b.width/2), Math.round(b.top+b.height/2)]); }})()")
        self.click(tab, r[0], r[1])


def open_in_firefox(url: str) -> bool:
    """Fallback: hand the URL to the running Firefox (no driving)."""
    try:
        subprocess.Popen(["firefox", "--new-tab", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False

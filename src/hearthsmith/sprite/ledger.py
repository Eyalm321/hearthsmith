"""The ledger: his task list as a window. Same store the heartbeat and the MCP server use, read
straight from SQLite, so a task an agent adds over MCP shows up here within a couple of seconds.

Add box takes the tasks.md syntax (`title @due(2026-09-25T18:00) +project #tag`) or plain
words ("call the vet friday 5pm", see when.py). Tick = done,
click a row = its notes and what he did about it, ⋯ = edit / snooze / block / delete.

Lives in the sprite process (system python, GTK3): right click him → Ledger, or middle click.
`python3 -m hearthsmith.sprite.ledger` opens it alone.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta

os.environ.setdefault("GDK_BACKEND", "x11")

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from hearthsmith import config, when
from hearthsmith.adapters.markdown import parse_line
from hearthsmith.store import Store, Task

CSS = b"""
#ledger { background: rgba(20,20,22,0.97); border: 2px solid #ff9a2e; border-radius: 10px; }
#ledger * { font-family: VT323, monospace; font-size: 19px; color: #e8e2d6; }
#ledger .head { font-family: 'Press Start 2P', monospace; font-size: 11px; color: #ff9a2e;
                padding: 12px 14px 4px 14px; }
#ledger .count { color: #8c857a; font-size: 17px; padding: 12px 14px 4px 0; }
#ledger .tabs { padding: 2px 10px 6px 10px; }
#ledger .tabs button { background: transparent; border: none; box-shadow: none; padding: 0 8px;
                       min-height: 0; }
#ledger .tabs button label { color: #8c857a; }
#ledger .tabs button:checked label, #ledger .tabs button:hover label { color: #ff9a2e; }
#ledger entry { background: #2a2826; border: 1px solid #4a4540; border-radius: 6px;
                box-shadow: none; caret-color: #ff9a2e; margin: 0 12px 8px 12px; padding: 2px 8px; min-height: 0; }
#ledger entry:focus { border-color: #ff9a2e; }
#ledger list, #ledger row { background: transparent; }
#ledger row:hover { background: rgba(255,154,46,0.07); }
#ledger .section { color: #ff9a2e; font-size: 16px; padding: 10px 14px 2px 14px; }
#ledger .meta { color: #8c857a; font-size: 16px; }
#ledger .overdue { color: #ff5e4a; }
#ledger .done { color: #6d675f; text-decoration-line: line-through; }
#ledger .detail { color: #b8b0a2; font-size: 16px; padding: 2px 14px 8px 44px; }
#ledger .empty { color: #6d675f; padding: 30px; }
#ledger button.flat { background: transparent; border: none; box-shadow: none; padding: 0 6px;
                      min-height: 0; }
#ledger button.flat label { color: #8c857a; }
#ledger button.flat:hover label { color: #ff9a2e; }
#ledger check { background: #2a2826; border: 1px solid #6d675f; border-radius: 3px;
                min-width: 14px; min-height: 14px; }
#ledger check:checked { background: #ff9a2e; border-color: #ff9a2e; }
"""

TABS = (("open", "Open"), ("blocked", "Blocked"), ("done", "Done"))


def _when(ts: int) -> str:
    d = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    day = ("today" if d.date() == today else "tomorrow" if d.date() == today + timedelta(1)
           else "yesterday" if d.date() == today - timedelta(1) else f"{d:%a %d %b}")
    return day if (d.hour, d.minute) == (0, 0) else f"{day} {d:%H:%M}"


def _section(t: Task) -> str:
    if t.state != "open":
        return ""
    if t.due is None:
        return "Someday"
    if t.overdue:
        return "Overdue"
    return "Today" if datetime.fromtimestamp(t.due).date() == datetime.now().date() else "Upcoming"


def as_line(t: Task) -> str:
    """The task written back in the add-box syntax, so edit is the same text box as add."""
    out = [t.title]
    if t.due:
        d = datetime.fromtimestamp(t.due)
        out.append(f"@due({d:%Y-%m-%d})" if (d.hour, d.minute) == (0, 0) else f"@due({d:%Y-%m-%dT%H:%M})")
    if t.project:
        out.append(f"+{t.project}")
    out += [f"#{g}" for g in t.tags.split(",") if g]
    return " ".join(out)


def _tomorrow_9() -> int:
    """Minutes until 9:00 tomorrow."""
    at = datetime.combine(datetime.now().date() + timedelta(1), datetime.min.time()).replace(hour=9)
    return max(1, int((at - datetime.now()).total_seconds() // 60))


class Ledger(Gtk.Window):
    def __init__(self, store: Store | None = None, on_event: Callable[[str, Task], None] | None = None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.store = store or Store(config.load().db_path)
        self.on_event = on_event or (lambda *_: None)
        self.tab = "open"
        self.expanded: set[str] = set()
        self.editing: str | None = None
        self._stamp: tuple = ()

        self.set_title("hearthsmith — ledger")
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_app_paintable(True)
        self.set_default_size(480, 600)
        if (vis := self.get_screen().get_rgba_visual()) and self.get_screen().is_composited():
            self.set_visual(vis)
        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_name("ledger")
        self.add(root)

        # header doubles as the drag handle: undecorated, so nothing else moves it
        head = Gtk.EventBox()
        head.connect("button-press-event", self._on_head_press)
        hb = Gtk.Box()
        title = Gtk.Label(label="⚒ LEDGER", xalign=0)
        title.get_style_context().add_class("head")
        hb.pack_start(title, True, True, 0)
        self.count = Gtk.Label(xalign=1)
        self.count.get_style_context().add_class("count")
        hb.pack_start(self.count, False, False, 0)
        close = Gtk.Button(label="×")
        close.get_style_context().add_class("flat")
        close.connect("clicked", lambda *_: self.close())
        hb.pack_start(close, False, False, 6)
        head.add(hb)
        root.pack_start(head, False, False, 0)

        tabs = Gtk.Box()
        tabs.get_style_context().add_class("tabs")
        self.tab_buttons: dict[str, Gtk.ToggleButton] = {}
        for key, lab in TABS:
            b = Gtk.ToggleButton(label=lab)
            b.set_active(key == self.tab)
            b.connect("toggled", self._on_tab, key)
            tabs.pack_start(b, False, False, 0)
            self.tab_buttons[key] = b
        root.pack_start(tabs, False, False, 0)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("New task…  call the vet friday 5pm +project #tag")
        self.entry.connect("activate", self._on_add)
        root.pack_start(self.entry, False, False, 0)

        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list.set_activate_on_single_click(True)
        self.list.connect("row-activated", self._on_row)
        sc.add(self.list)
        root.pack_start(sc, True, True, 0)

        self.connect("key-press-event", self._on_key)
        GLib.timeout_add(1500, self._poll)
        self.refresh()

    # -- data ----------------------------------------------------------------------------------

    def _fingerprint(self) -> tuple:
        # updated_at moves on every edit/state change, and the runs table on every errand; a
        # delete only moves the count. Cheap enough to ask every 1.5s.
        r = self.store.db.execute("SELECT COUNT(*), MAX(updated_at) FROM tasks").fetchone()
        n = self.store.db.execute("SELECT MAX(at) FROM runs").fetchone()
        return (tuple(r), n[0], self.tab, frozenset(self.expanded))

    def _poll(self) -> bool:
        if self.get_visible() and self.editing is None and self._fingerprint() != self._stamp:
            self.refresh()
        return True

    def refresh(self) -> None:
        self._stamp = self._fingerprint()
        for c in self.list.get_children():
            self.list.remove(c)
        n_open = len(self.store.list("open"))
        n_over = sum(t.overdue for t in self.store.list("open"))
        self.count.set_text(f"{n_open} open" + (f" · {n_over} overdue" if n_over else ""))
        tasks = self.store.list(self.tab)
        if self.tab == "done":
            tasks.sort(key=lambda t: -t.updated_at)
            tasks = tasks[:50]
        if not tasks:
            lab = Gtk.Label(label={"open": "Anvil's clear. Nothing on the ledger.",
                                   "blocked": "Nothing stuck.",
                                   "done": "Nothing finished yet."}[self.tab])
            lab.get_style_context().add_class("empty")
            self.list.add(self._plain_row(lab))
        order = {"Overdue": 0, "Today": 1, "Upcoming": 2, "Someday": 3, "": 4}
        tasks.sort(key=lambda t: order[_section(t)])  # stable: keeps the store's due order inside
        last = None
        for t in tasks:
            if (sec := _section(t)) != last and sec:
                lab = Gtk.Label(label=sec.upper(), xalign=0)
                lab.get_style_context().add_class("section")
                self.list.add(self._plain_row(lab))
            last = sec
            self.list.add(self._task_row(t))
        self.list.show_all()

    # -- rows ----------------------------------------------------------------------------------

    def _plain_row(self, child: Gtk.Widget) -> Gtk.ListBoxRow:
        r = Gtk.ListBoxRow()
        r.set_activatable(False)
        r.add(child)
        return r

    def _task_row(self, t: Task) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.task_id = t.id
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        line = Gtk.Box(spacing=8)
        line.set_margin_start(14)
        line.set_margin_end(8)
        line.set_margin_top(4)
        line.set_margin_bottom(4)

        chk = Gtk.CheckButton()
        chk.set_active(t.state == "done")
        chk.set_valign(Gtk.Align.START)
        chk.connect("toggled", self._on_check, t)
        line.pack_start(chk, False, False, 0)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        if self.editing == t.id:
            ed = Gtk.Entry()
            ed.set_text(as_line(t))
            ed.connect("activate", self._on_edit_save, t)
            ed.connect("key-press-event", self._on_edit_key)
            GLib.idle_add(ed.grab_focus)
            col.pack_start(ed, False, False, 0)
        else:
            title = Gtk.Label(label=t.title, xalign=0)
            title.set_line_wrap(True)
            title.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            if t.state == "done":
                title.get_style_context().add_class("done")
            col.pack_start(title, False, False, 0)
            if meta := self._meta(t):
                m = Gtk.Label(xalign=0)
                m.set_markup(meta)
                m.get_style_context().add_class("meta")
                col.pack_start(m, False, False, 0)
        line.pack_start(col, True, True, 0)

        more = Gtk.MenuButton(label="⋯")
        more.get_style_context().add_class("flat")
        more.set_valign(Gtk.Align.START)
        more.set_popup(self._menu(t))
        line.pack_start(more, False, False, 0)
        box.pack_start(line, False, False, 0)

        if t.id in self.expanded:
            d = Gtk.Label(label=self._detail(t), xalign=0)
            d.set_line_wrap(True)
            d.set_selectable(True)
            d.get_style_context().add_class("detail")
            box.pack_start(d, False, False, 0)
        row.add(box)
        return row

    def _meta(self, t: Task) -> str:
        esc = GLib.markup_escape_text
        bits = []
        if t.due:
            w = esc(_when(t.due))
            bits.append(f"<span foreground='#ff5e4a'>overdue · {w}</span>" if t.overdue else w)
        if t.project:
            bits.append(f"+{esc(t.project)}")
        bits += [f"#{esc(g)}" for g in t.tags.split(",") if g]
        if t.snoozed:
            bits.append(f"zz till {datetime.fromtimestamp(t.snoozed_until):%H:%M}")
        if t.nag_count:
            bits.append(f"nagged {t.nag_count}×")
        if t.source != "hearthsmith":
            bits.append(f"from {esc(t.source)}")
        return "  ".join(bits)

    def _detail(self, t: Task) -> str:
        out = []
        if t.notes.strip():
            out.append(t.notes.strip())
        for r in self.store.runs(5, t.id):
            mark = "✓" if r["ok"] else "×"
            out.append(f"{mark} {datetime.fromtimestamp(r['at']):%m-%d %H:%M} {r['body']}: "
                       f"{r['note'] or r['goal']}"[:300])
            if steps := json.loads(r["steps"]):
                out.append(f"   last: {steps[-1]}"[:200])
        out.append(f"id {t.id} · added {_when(t.created_at)}")
        return "\n".join(out)

    def _menu(self, t: Task) -> Gtk.Menu:
        m = Gtk.Menu()

        def item(label, cb):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda *_: (cb(), self.refresh()))
            m.append(it)

        item("Edit", lambda: setattr(self, "editing", t.id))
        if t.state == "open":
            item("Snooze 1 hour", lambda: self.store.snooze(t.id, 60))
            item("Snooze till tomorrow 9:00", lambda: self.store.snooze(t.id, _tomorrow_9()))
            if t.snoozed:
                item("Wake", lambda: self.store.snooze(t.id, 0))
            item("Block", lambda: self.store.set_state(t.id, "blocked"))
        else:
            item("Reopen", lambda: self.store.set_state(t.id, "open"))
        m.append(Gtk.SeparatorMenuItem())
        item("Delete", lambda: self.store.delete(t.id))
        m.show_all()
        return m

    # -- events --------------------------------------------------------------------------------

    def _on_add(self, e: Gtk.Entry) -> None:
        text = e.get_text().strip()
        if not text:
            return
        try:
            title, due, project, tags = parse_line(text)
        except ValueError:
            e.get_style_context().add_class("overdue")  # bad @due(...) date
            return
        e.get_style_context().remove_class("overdue")
        if due is None:
            title, due = when.parse(title)     # "call the vet friday 5pm" works here too
        if title:
            t = self.store.add(title, due=due, project=project, tags=tags)
            self.on_event("added", t)
        e.set_text("")
        if self.tab != "open":
            self.tab_buttons["open"].set_active(True)
        self.refresh()

    def _on_check(self, chk: Gtk.CheckButton, t: Task) -> None:
        state = "done" if chk.get_active() else "open"
        self.store.set_state(t.id, state)
        if state == "done":
            self.on_event("done", t)
        # let the tick show before the row leaves the list
        GLib.timeout_add(350, lambda: (self.refresh(), False)[1])

    def _on_row(self, _l, row) -> None:
        if tid := getattr(row, "task_id", None):
            self.expanded ^= {tid}
            self.refresh()

    def _on_edit_save(self, e: Gtk.Entry, t: Task) -> None:
        try:
            title, due, project, tags = parse_line(e.get_text().strip())
        except ValueError:
            return
        if title:
            self.store.edit(t.id, title=title, due=due, project=project, tags=tags)
        self.editing = None
        self.refresh()

    def _on_edit_key(self, _e, ev) -> bool:
        if ev.keyval == Gdk.KEY_Escape:
            self.editing = None
            self.refresh()
            return True
        return False

    def _on_tab(self, b: Gtk.ToggleButton, key: str) -> None:
        if not b.get_active():
            if self.tab == key:
                b.set_active(True)  # radio behaviour: the current tab stays pressed
            return
        self.tab = key
        for k, other in self.tab_buttons.items():
            if k != key:
                other.set_active(False)
        self.refresh()

    def _on_key(self, _w, ev) -> bool:
        if ev.keyval == Gdk.KEY_Escape and self.editing is None:
            self.close()
            return True
        if ev.keyval == Gdk.KEY_n and ev.state & Gdk.ModifierType.CONTROL_MASK:
            self.entry.grab_focus()
            return True
        return False

    def _on_head_press(self, _w, ev) -> bool:
        if ev.button == 1:
            self.begin_move_drag(ev.button, int(ev.x_root), int(ev.y_root), ev.time)
        return True

    def show_near(self, x: int, y: int) -> None:
        """Open on the monitor he stands on, centred."""
        mon = self.get_screen().get_display().get_monitor_at_point(x, y).get_workarea()
        w, h = self.get_default_size()
        self.move(mon.x + (mon.width - w) // 2, mon.y + (mon.height - h) // 2)
        self.refresh()
        self.show_all()
        self.present()
        self.entry.grab_focus()


def main() -> None:
    from hearthsmith.sprite.renderer import _add_fonts
    _add_fonts()
    w = Ledger()
    w.connect("destroy", Gtk.main_quit)
    w.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()

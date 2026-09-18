import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `
<node>
  <interface name="org.forge.Windows">
    <method name="List"><arg type="s" direction="out" name="json"/></method>
    <method name="Activate"><arg type="u" direction="in" name="id"/><arg type="b" direction="out" name="ok"/></method>
  </interface>
</node>`;

export default class ForgeWindows extends Extension {
    enable() {
        this._tracker = Shell.WindowTracker.get_default();
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/org/forge/Windows');
    }

    disable() {
        this._dbus?.unexport();
        this._dbus = null;
        this._tracker = null;
    }

    List() {
        const out = [];
        for (const actor of global.get_window_actors()) {
            const w = actor.meta_window;
            if (!w || w.is_skip_taskbar() && w.get_window_type() !== 0) continue;
            const r = w.get_frame_rect();
            const app = this._tracker.get_window_app(w);
            out.push({
                id: w.get_id(), wm_class: w.get_wm_class() ?? '', title: w.get_title() ?? '',
                app: app?.get_name() ?? '', app_id: app?.get_id() ?? '', pid: w.get_pid(),
                x: r.x, y: r.y, w: r.width, h: r.height,
                focus: w.has_focus(), minimized: w.minimized, monitor: w.get_monitor(),
                workspace: w.get_workspace()?.index() ?? -1, type: w.get_window_type(),
            });
        }
        return JSON.stringify(out);
    }

    Activate(id) {
        for (const actor of global.get_window_actors()) {
            const w = actor.meta_window;
            if (w && w.get_id() === id) {
                w.activate(global.get_current_time());
                return true;
            }
        }
        return false;
    }
}

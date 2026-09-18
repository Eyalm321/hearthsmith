import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `
<node>
  <interface name="org.forge.Windows">
    <method name="List"><arg type="s" direction="out" name="json"/></method>
    <method name="Activate"><arg type="u" direction="in" name="id"/><arg type="b" direction="out" name="ok"/></method>
    <method name="Pointer"><arg type="s" direction="out" name="json"/></method>
    <!-- Capture a region (w=0 → whole screen). Wayland gives clients no way to read the
         screen, and the portal prompts every time; this is the same trust boundary as
         the window list above. -->
    <method name="Screenshot">
      <arg type="i" direction="in" name="x"/><arg type="i" direction="in" name="y"/>
      <arg type="i" direction="in" name="w"/><arg type="i" direction="in" name="h"/>
      <arg type="s" direction="out" name="path"/>
    </method>
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

    Pointer() {
        // Wayland clients cannot read the cursor; the shell can. Used only to notice when the
        // user grabs the mouse mid-task so the assistant stops.
        const [x, y, mods] = global.get_pointer();
        return JSON.stringify({x, y, mods});
    }

    ScreenshotAsync([x, y, w, h], invocation) {
        const path = GLib.build_filenamev([GLib.get_tmp_dir(), `forge-shot-${Date.now()}.png`]);
        const stream = Gio.File.new_for_path(path).replace(
            null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
        const shooter = new Shell.Screenshot();
        const reply = () => invocation.return_value(new GLib.Variant('(s)', [path]));
        const fail = e => invocation.return_value(new GLib.Variant('(s)', [`ERROR: ${e}`]));
        try {
            if (w > 0 && h > 0) {
                shooter.screenshot_area(x, y, w, h, stream, (o, res) => {
                    try { shooter.screenshot_area_finish(res); reply(); } catch (e) { fail(e); }
                });
            } else {
                shooter.screenshot(false, stream, (o, res) => {
                    try { shooter.screenshot_finish(res); reply(); } catch (e) { fail(e); }
                });
            }
        } catch (e) { fail(e); }
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

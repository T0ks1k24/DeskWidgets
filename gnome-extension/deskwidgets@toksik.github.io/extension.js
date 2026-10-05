// DeskWidgets grid helper for GNOME Shell.
//
// While a DeskWidgets window (title "deskwidget:<id>") is moved, we draw the grid on the
// desktop under all windows and highlight the cell where the widget will land. When the
// move ends the window is snapped onto that cell. The service writes the grid to
// $XDG_RUNTIME_DIR/deskwidgets/grid.json and picks up the new position. While the
// "dragging" file exists there, the service doesn't snap on its own.

import GLib from 'gi://GLib';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const PREFIX = 'deskwidget:';

export default class DeskWidgetsGridExtension extends Extension {
    enable() {
        this._drag = null;
        this._beginId = global.display.connect('grab-op-begin', (_display, window) => this._begin(window));
        this._endId = global.display.connect('grab-op-end', (_display, window) => this._end(window));
    }

    disable() {
        global.display.disconnect(this._beginId);
        global.display.disconnect(this._endId);
        this._finish();
    }

    _runFile(name) {
        return GLib.build_filenamev([GLib.get_user_runtime_dir(), 'deskwidgets', name]);
    }

    _readGrid() {
        try {
            const [ok, bytes] = GLib.file_get_contents(this._runFile('grid.json'));
            return ok ? JSON.parse(new TextDecoder().decode(bytes)) : null;
        } catch (e) {
            return null;
        }
    }

    _widgetId(window) {
        const title = window?.get_title?.() ?? '';
        return title.startsWith(PREFIX) ? title.slice(PREFIX.length) : null;
    }

    _begin(window) {
        const id = this._widgetId(window);
        if (!id)
            return;
        const grid = this._readGrid();
        if (!grid || !grid.widgets?.[id])
            return;
        this._finish();
        const rect = window.get_frame_rect();
        this._drag = {window, id, grid, size: grid.widgets[id], width: rect.width, height: rect.height};
        try {
            GLib.file_set_contents(this._runFile('dragging'), id);
        } catch (e) {
            // the service then snaps on its own a moment later
        }

        const overlay = new St.Widget({reactive: false, x: 0, y: 0, width: global.stage.width, height: global.stage.height});
        const radius = grid.radius ?? 22;
        for (let c = 0; c < grid.cols; c++) {
            for (let r = 0; r < grid.rows; r++) {
                overlay.add_child(new St.Widget({
                    style_class: 'deskwidgets-cell',
                    style: `border-radius: ${radius}px;`,
                    x: grid.x0 + c * grid.pitch, y: grid.y0 + r * grid.pitch,
                    width: grid.cell, height: grid.cell,
                }));
            }
        }
        this._target = new St.Widget({style_class: 'deskwidgets-target', style: `border-radius: ${radius}px;`});
        overlay.add_child(this._target);
        this._overlay = overlay;
        // just above the wallpaper: under every window, so the dragged widget stays on top
        global.window_group.insert_child_above(overlay, Main.layoutManager._backgroundGroup);
        this._posId = window.connect('position-changed', () => this._update());
        this._update();
    }

    _taken() {
        const {grid, id} = this._drag;
        const taken = new Set();
        for (const [other, w] of Object.entries(grid.widgets)) {
            if (other === id)
                continue;
            for (let c = w.col; c < w.col + w.w; c++) {
                for (let r = w.row; r < w.row + w.h; r++)
                    taken.add(`${c},${r}`);
            }
        }
        return taken;
    }

    // nearest free cell for the dragged widget (same rule as the service)
    _spot() {
        const {window, grid, size} = this._drag;
        const rect = window.get_frame_rect();
        const col = Math.round((rect.x + grid.margin - grid.x0) / grid.pitch);
        const row = Math.round((rect.y + grid.margin - grid.y0) / grid.pitch);
        const taken = this._taken();
        let best = null;
        for (let c = 0; c <= grid.cols - size.w; c++) {
            for (let r = 0; r <= grid.rows - size.h; r++) {
                let free = true;
                for (let i = c; i < c + size.w && free; i++) {
                    for (let j = r; j < r + size.h && free; j++)
                        free = !taken.has(`${i},${j}`);
                }
                if (!free)
                    continue;
                const d = (c - col) ** 2 + (r - row) ** 2;
                if (!best || d < best.d)
                    best = {c, r, d};
            }
        }
        return best;
    }

    _update() {
        if (!this._drag || !this._target)
            return;
        const spot = this._spot();
        if (!spot) {
            this._target.hide();
            return;
        }
        const {grid, size} = this._drag;
        this._target.set_position(grid.x0 + spot.c * grid.pitch, grid.y0 + spot.r * grid.pitch);
        this._target.set_size(size.w * grid.cell + (size.w - 1) * grid.gap, size.h * grid.cell + (size.h - 1) * grid.gap);
        this._target.show();
    }

    _end(window) {
        if (!this._drag || this._drag.window !== window)
            return;
        const {grid, width, height} = this._drag;
        const rect = window.get_frame_rect();
        const spot = this._spot();
        // a move, not a resize: snap into the highlighted cell
        if (spot && rect.width === width && rect.height === height) {
            window.move_frame(true, grid.x0 + spot.c * grid.pitch - grid.margin, grid.y0 + spot.r * grid.pitch - grid.margin);
            grid.widgets[this._drag.id].col = spot.c;
            grid.widgets[this._drag.id].row = spot.r;
        }
        this._finish();
    }

    _finish() {
        if (this._drag?.window && this._posId)
            this._drag.window.disconnect(this._posId);
        this._posId = 0;
        this._overlay?.destroy();
        this._overlay = null;
        this._target = null;
        this._drag = null;
        try {
            GLib.unlink(this._runFile('dragging'));
        } catch (e) {
            // not there
        }
    }
}

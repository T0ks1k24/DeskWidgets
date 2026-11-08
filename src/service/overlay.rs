//! The grid that appears while a widget is dragged, with the cell it will snap to highlighted.
use std::f64::consts::PI;

use super::daemon::Daemon;
use crate::core::catalog::cells_of;
use crate::core::grid::{Cell, BASE_RADIUS};
use crate::engine::canvas::{new_surface, Context};
use crate::platform::{Host, WinId};

// Readable on any wallpaper: a translucent dark fill shows on light pictures, the light outline on dark ones,
// and the thin dark rim around it keeps the outline visible on mid-tones.
const CELL_FILL: [f64; 4] = [0.0, 0.0, 0.0, 0.18];
const CELL_STROKES: [([f64; 4], f64); 2] = [([0.0, 0.0, 0.0, 0.30], 3.5), ([1.0, 1.0, 1.0, 0.70], 1.5)];
const TARGET_FILL: [f64; 4] = [0.04, 0.52, 1.0, 0.38];
const TARGET_STROKES: [([f64; 4], f64); 2] = [([1.0, 1.0, 1.0, 0.85], 4.5), ([0.04, 0.52, 1.0, 1.0], 2.5)];

/// A click-through window over the grid. It is redrawn only when the target cell changes.
#[derive(Default)]
pub struct Overlay {
    pub window: Option<WinId>,
    /// None: nothing drawn yet; Some(None): drawn, and there is no free spot for the widget.
    pub target: Option<Option<Cell>>,
}

impl Daemon {
    pub fn overlay_show(&mut self, host: &mut dyn Host, id: &str) {
        if !self.settings.show_grid || self.overlay.window.is_some() {
            return;
        }
        let grid = &self.grid;
        let width = (grid.cols * grid.pitch - grid.gap + 2) as u32;
        let height = (grid.rows * grid.pitch - grid.gap + 2) as u32;
        let origin = (self.origin.0 + grid.x0 - 1, self.origin.1 + grid.y0 - 1);
        match host.create_window("deskwidgets-grid", width, height, origin.0, origin.1, true) {
            Ok(win) => {
                self.overlay.window = Some(win);
                self.overlay.target = None;
                let pos = self.instances.get(id).and_then(|i| i.window).and_then(|w| host.position(w)).or_else(|| self.instances.get(id).map(|i| i.requested));
                if let Some(pos) = pos {
                    self.overlay_update(host, id, pos);
                }
            }
            Err(e) => crate::core::util::log(format!("cannot show the grid: {e}")),
        }
    }

    pub fn overlay_update(&mut self, host: &mut dyn Host, id: &str, pos: (i32, i32)) {
        let Some(win) = self.overlay.window else { return };
        let Some(inst) = self.layout.get(id).cloned() else { return };
        let (col, row) = self.nearest_cell(id, pos);
        let spot = self.layout.grid.free_near(col, row, &inst.size, &self.layout.taken(Some(id)));
        if self.overlay.target == Some(spot) {
            return;
        }
        self.overlay.target = Some(spot);
        if let Some(surface) = self.draw_overlay(&inst.size, spot, id) {
            host.present(win, &surface.borrow());
        }
    }

    pub fn overlay_hide(&mut self, host: &mut dyn Host) {
        if let Some(win) = self.overlay.window.take() {
            host.close_window(win);
        }
        self.overlay.target = None;
    }

    fn draw_overlay(&self, size: &str, spot: Option<Cell>, dragged: &str) -> Option<crate::engine::canvas::Surface> {
        let grid = &self.grid;
        let width = (grid.cols * grid.pitch - grid.gap + 2) as u32;
        let height = (grid.rows * grid.pitch - grid.gap + 2) as u32;
        let surface = new_surface(width, height)?;
        let mut cr = Context::new(surface.clone(), self.fonts.clone());
        let radius = (BASE_RADIUS * grid.scale).round();
        let taken = self.layout.taken(None);
        let target_cells = spot.map(|(c, r)| crate::core::grid::Grid::cells(c, r, size)).unwrap_or_default();
        let _ = dragged;
        let cell = grid.cell as f64;
        for col in 0..grid.cols {
            for row in 0..grid.rows {
                if taken.contains(&(col, row)) && !target_cells.contains(&(col, row)) {
                    continue;
                }
                rounded(&mut cr, 1.0 + (col * grid.pitch) as f64, 1.0 + (row * grid.pitch) as f64, cell, cell, radius, CELL_FILL, &CELL_STROKES);
            }
        }
        if let Some((col, row)) = spot {
            let (w, h) = grid.card_px(size);
            let _ = cells_of(size);
            rounded(&mut cr, 1.0 + (col * grid.pitch) as f64, 1.0 + (row * grid.pitch) as f64, w as f64, h as f64, radius, TARGET_FILL, &TARGET_STROKES);
        }
        Some(surface)
    }
}

fn rounded(cr: &mut Context, x: f64, y: f64, w: f64, h: f64, r: f64, fill: [f64; 4], strokes: &[([f64; 4], f64)]) {
    cr.new_sub_path();
    cr.arc(x + w - r, y + r, r, -PI / 2.0, 0.0);
    cr.arc(x + w - r, y + h - r, r, 0.0, PI / 2.0);
    cr.arc(x + r, y + h - r, r, PI / 2.0, PI);
    cr.arc(x + r, y + r, r, PI, 3.0 * PI / 2.0);
    cr.close_path();
    cr.set_source_rgba(fill[0], fill[1], fill[2], fill[3]);
    cr.fill_preserve();
    for (color, width) in strokes {
        cr.set_source_rgba(color[0], color[1], color[2], color[3]);
        cr.set_line_width(*width);
        cr.stroke_preserve();
    }
    cr.new_path();
}

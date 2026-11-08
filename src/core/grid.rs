//! The grid every widget snaps to. A cell is a small card, medium is 2x1 cells, large is 2x2.
use std::collections::HashSet;

use super::catalog::cells_of;
use super::settings::Settings;

pub const BASE_CELL: f64 = 170.0; // small card at 100 %
pub const BASE_MARGIN: f64 = 16.0; // transparent shadow margin around a card (lib.lua L.M)
pub const BASE_RADIUS: f64 = 22.0; // card corner radius (lib.lua L.R)

pub type Cell = (i32, i32);

#[derive(Debug, Clone, PartialEq)]
pub struct Grid {
    pub scale: f64,
    pub cell: i32,
    pub gap: i32,
    pub margin: i32,
    pub pitch: i32,
    pub cols: i32,
    pub rows: i32,
    pub x0: i32,
    pub y0: i32,
}

/// Python-style round (half to even) so layouts match the old implementation exactly.
fn round(value: f64) -> i32 {
    let floor = value.floor();
    let diff = value - floor;
    if (diff - 0.5).abs() < 1e-12 {
        if (floor as i64) % 2 == 0 { floor as i32 } else { floor as i32 + 1 }
    } else {
        value.round() as i32
    }
}

pub fn round_half_even(value: f64) -> i32 {
    round(value)
}

impl Grid {
    pub fn new(screen_w: i32, screen_h: i32, scale: f64, gap: i32, side: i32, top: i32, bottom: i32) -> Grid {
        let cell = round(BASE_CELL * scale);
        let margin = round(BASE_MARGIN * scale);
        let pitch = cell + gap;
        let cols = ((screen_w - 2 * side + gap).div_euclid(pitch)).max(2);
        let rows = ((screen_h - top - bottom + gap).div_euclid(pitch)).max(2);
        let x0 = (screen_w - (cols * pitch - gap)).div_euclid(2);
        Grid { scale, cell, gap, margin, pitch, cols, rows, x0, y0: top }
    }

    /// A grid from the user's settings for a screen of this size (scale/gap/margins as stored).
    pub fn from_settings(screen_w: i32, screen_h: i32, s: &Settings, scale: f64) -> Grid {
        Grid::new(screen_w, screen_h, scale, s.gap, s.margin_side, s.margin_top, s.margin_bottom)
    }

    pub fn card_px(&self, size: &str) -> (i32, i32) {
        let (cw, ch) = cells_of(size);
        (cw * self.cell + (cw - 1) * self.gap, ch * self.cell + (ch - 1) * self.gap)
    }

    pub fn window_px(&self, size: &str) -> (i32, i32) {
        let (w, h) = self.card_px(size);
        (w + 2 * self.margin, h + 2 * self.margin)
    }

    pub fn window_xy(&self, col: i32, row: i32) -> (i32, i32) {
        (self.x0 + col * self.pitch - self.margin, self.y0 + row * self.pitch - self.margin)
    }

    pub fn fits(&self, col: i32, row: i32, size: &str) -> bool {
        let (cw, ch) = cells_of(size);
        col >= 0 && col <= self.cols - cw && row >= 0 && row <= self.rows - ch
    }

    pub fn cells(col: i32, row: i32, size: &str) -> HashSet<Cell> {
        let (cw, ch) = cells_of(size);
        let mut set = HashSet::new();
        for c in col..col + cw {
            for r in row..row + ch {
                set.insert((c, r));
            }
        }
        set
    }

    pub fn nearest_cell(&self, win_x: i32, win_y: i32, size: &str) -> Cell {
        let (cw, ch) = cells_of(size);
        let col = round((win_x + self.margin - self.x0) as f64 / self.pitch as f64);
        let row = round((win_y + self.margin - self.y0) as f64 / self.pitch as f64);
        (col.min(self.cols - cw).max(0), row.min(self.rows - ch).max(0))
    }

    /// Free spot for `size` closest to (col, row), or None when the grid is full.
    pub fn free_near(&self, col: i32, row: i32, size: &str, taken: &HashSet<Cell>) -> Option<Cell> {
        let mut spots: Vec<Cell> = Vec::new();
        for c in 0..self.cols {
            for r in 0..self.rows {
                if self.fits(c, r, size) {
                    spots.push((c, r));
                }
            }
        }
        spots.sort_by_key(|&(c, r)| (((c - col).pow(2) + (r - row).pow(2)), r, c));
        spots.into_iter().find(|&(c, r)| Grid::cells(c, r, size).is_disjoint(taken))
    }

    /// New widgets fill the outer columns first, so the middle of the wallpaper stays clear.
    pub fn first_free(&self, size: &str, taken: &HashSet<Cell>) -> Option<Cell> {
        let (cw, _) = cells_of(size);
        let last = self.cols - cw;
        let mut order: Vec<i32> = Vec::new();
        for i in 0..=last {
            for c in [i, last - i] {
                if !order.contains(&c) && (0..=last).contains(&c) {
                    order.push(c);
                }
            }
        }
        for c in order {
            for r in 0..self.rows {
                if self.fits(c, r, size) && Grid::cells(c, r, size).is_disjoint(taken) {
                    return Some((c, r));
                }
            }
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn grid() -> Grid {
        Grid::new(1920, 1080, 1.0, 16, 24, 44, 88)
    }

    #[test]
    fn dimensions() {
        let g = grid();
        assert_eq!(g.cell, 170);
        assert_eq!(g.pitch, 186);
        assert_eq!(g.card_px("medium"), (356, 170));
        assert_eq!(g.window_px("small"), (202, 202));
        assert!(g.cols >= 9 && g.rows >= 4);
    }

    #[test]
    fn snapping_and_free_spots() {
        let g = grid();
        let (x, y) = g.window_xy(3, 2);
        assert_eq!(g.nearest_cell(x, y, "small"), (3, 2));
        assert_eq!(g.nearest_cell(x + 40, y - 30, "small"), (3, 2));
        let mut taken = Grid::cells(3, 2, "large");
        assert_eq!(g.free_near(3, 2, "small", &taken), Some((3, 1)));
        taken.insert((3, 1));
        assert_ne!(g.free_near(3, 2, "small", &taken), Some((3, 2)));
    }

    #[test]
    fn first_free_takes_outer_columns() {
        let g = grid();
        assert_eq!(g.first_free("small", &HashSet::new()), Some((0, 0)));
        let taken = Grid::cells(0, 0, "small");
        assert_eq!(g.first_free("small", &taken), Some((0, 1)));
    }

    #[test]
    fn rounding_matches_python() {
        assert_eq!(round_half_even(0.5), 0);
        assert_eq!(round_half_even(1.5), 2);
        assert_eq!(round_half_even(2.5), 2);
        assert_eq!(round_half_even(-0.5), 0);
    }
}

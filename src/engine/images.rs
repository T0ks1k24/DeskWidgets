//! Pictures for widgets: any format the `image` crate reads (JPEG, PNG, WebP, GIF) -> a surface.
//!
//! Photos are shrunk while they are decoded, and the last few surfaces are kept, so a 24 MP photo
//! neither stalls a frame nor stays in memory at full size.
use std::collections::VecDeque;
use std::path::Path;
use std::rc::Rc;
use std::time::{Instant, SystemTime};

use image::imageops::FilterType;
use image::{DynamicImage, ImageDecoder, ImageReader};
use tiny_skia::Pixmap;

use super::canvas::Surface;
use crate::core::util::log;

pub const MAX_SIDE: u32 = 768; // longest side kept; widgets draw pictures at most ~360 px
const CACHE_ITEMS: usize = 8;
const CACHE_BYTES: usize = 32 * 1024 * 1024;
const RETRY_AFTER: f64 = 2.0; // seconds before a file that failed to load is tried again

struct Item {
    path: String,
    stamp: (Option<SystemTime>, u64),
    surface: Option<Surface>,
    loaded_at: Instant,
}

#[derive(Default)]
pub struct ImageCache {
    items: VecDeque<Item>, // least recently used first
    bytes: usize,
}

fn cost(surface: &Surface) -> usize {
    let s = surface.borrow();
    s.width() as usize * s.height() as usize * 4
}

impl ImageCache {
    pub fn new() -> ImageCache {
        ImageCache::default()
    }

    /// A surface for `path`, or None (missing, still downloading, not an image).
    pub fn load(&mut self, path: &str) -> Option<Surface> {
        let meta = match std::fs::metadata(path) {
            Ok(m) => m,
            Err(_) => {
                self.forget(path);
                return None;
            }
        };
        let stamp = (meta.modified().ok(), meta.len());
        if let Some(pos) = self.items.iter().position(|i| i.path == path) {
            let item = &self.items[pos];
            let fresh = item.stamp == stamp;
            if fresh && (item.surface.is_some() || item.loaded_at.elapsed().as_secs_f64() < RETRY_AFTER) {
                let item = self.items.remove(pos).unwrap();
                let surface = item.surface.clone();
                self.items.push_back(item);
                return surface;
            }
        }
        let surface = load_surface(Path::new(path), MAX_SIDE);
        self.forget(path);
        if let Some(s) = &surface {
            self.bytes += cost(s);
        }
        self.items.push_back(Item { path: path.to_string(), stamp, surface: surface.clone(), loaded_at: Instant::now() });
        self.trim();
        surface
    }

    fn forget(&mut self, path: &str) {
        if let Some(pos) = self.items.iter().position(|i| i.path == path) {
            let old = self.items.remove(pos).unwrap();
            if let Some(s) = old.surface {
                self.bytes = self.bytes.saturating_sub(cost(&s));
            }
        }
    }

    fn trim(&mut self) {
        while self.items.len() > 1 && (self.items.len() > CACHE_ITEMS || self.bytes > CACHE_BYTES) {
            if let Some(old) = self.items.pop_front() {
                if let Some(s) = old.surface {
                    self.bytes = self.bytes.saturating_sub(cost(&s));
                }
            }
        }
    }
}

/// Decodes `path` into a premultiplied surface no larger than `max_side`; None if that fails.
pub fn load_surface(path: &Path, max_side: u32) -> Option<Surface> {
    match decode(path, max_side) {
        Ok(surface) => Some(surface),
        Err(e) => {
            log(format!("image {}: {e}", path.display()));
            None
        }
    }
}

fn decode(path: &Path, max_side: u32) -> Result<Surface, String> {
    let reader = ImageReader::open(path).map_err(|e| e.to_string())?.with_guessed_format().map_err(|e| e.to_string())?;
    let mut decoder = reader.into_decoder().map_err(|e| e.to_string())?;
    let orientation = decoder.orientation().ok();
    let mut img = DynamicImage::from_decoder(decoder).map_err(|e| e.to_string())?;
    if let Some(o) = orientation {
        img.apply_orientation(o);
    }
    if img.width() > max_side || img.height() > max_side {
        img = img.resize(max_side, max_side, FilterType::Lanczos3);
    }
    from_image(&img)
}

pub fn from_image(img: &DynamicImage) -> Result<Surface, String> {
    let rgba = img.to_rgba8();
    let (w, h) = rgba.dimensions();
    let mut pixmap = Pixmap::new(w.max(1), h.max(1)).ok_or("empty image")?;
    for (src, dst) in rgba.as_raw().chunks_exact(4).zip(pixmap.data_mut().chunks_exact_mut(4)) {
        let a = src[3] as u32;
        dst[0] = ((src[0] as u32 * a + 127) / 255) as u8;
        dst[1] = ((src[1] as u32 * a + 127) / 255) as u8;
        dst[2] = ((src[2] as u32 * a + 127) / 255) as u8;
        dst[3] = a as u8;
    }
    Ok(Rc::new(std::cell::RefCell::new(pixmap)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loads_and_shrinks_pictures_and_survives_garbage() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("big.png");
        image::RgbaImage::from_pixel(1600, 800, image::Rgba([255, 0, 0, 255])).save(&path).unwrap();
        let mut cache = ImageCache::new();
        let s = cache.load(path.to_str().unwrap()).unwrap();
        assert_eq!((s.borrow().width(), s.borrow().height()), (768, 384));
        assert!(Rc::ptr_eq(&s, &cache.load(path.to_str().unwrap()).unwrap()));
        let junk = dir.path().join("junk.png");
        std::fs::write(&junk, b"not a picture").unwrap();
        assert!(cache.load(junk.to_str().unwrap()).is_none());
        assert!(cache.load("/no/such/file.png").is_none());
    }
}

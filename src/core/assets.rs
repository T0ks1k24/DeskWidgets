//! The widgets, the Lua library, fonts and icons live inside the executable and are unpacked into
//! the cache folder, because Lua loads its files from disk. Unpacking is incremental: only changed files
//! are written, files that no longer belong are removed. `DESKWIDGETS_DATA=<checkout>` skips all of it.
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use include_dir::{include_dir, Dir, DirEntry};

use super::paths;
use super::util::log;

static WIDGETS: Dir = include_dir!("$CARGO_MANIFEST_DIR/widgets");
static LUA: Dir = include_dir!("$CARGO_MANIFEST_DIR/lua");
static ASSETS: Dir = include_dir!("$CARGO_MANIFEST_DIR/assets");

static DATA: OnceLock<PathBuf> = OnceLock::new();

pub fn data_dir() -> &'static Path {
    DATA.get_or_init(|| {
        if let Some(path) = std::env::var_os("DESKWIDGETS_DATA") {
            return PathBuf::from(path);
        }
        let root = paths::cache().join("data");
        for (name, dir) in [("widgets", &WIDGETS), ("lua", &LUA), ("assets", &ASSETS)] {
            if let Err(e) = sync(dir, &root.join(name)) {
                log(format!("cannot unpack {name} into {}: {e}", root.display()));
            }
        }
        root
    })
}

fn sync(dir: &Dir, target: &Path) -> std::io::Result<()> {
    fs::create_dir_all(target)?;
    let mut wanted: Vec<PathBuf> = Vec::new();
    write_dir(dir, target, &mut wanted)?;
    remove_stale(target, &wanted)
}

fn write_dir(dir: &Dir, root: &Path, wanted: &mut Vec<PathBuf>) -> std::io::Result<()> {
    for entry in dir.entries() {
        match entry {
            DirEntry::Dir(sub) => write_dir(sub, root, wanted)?,
            DirEntry::File(file) => {
                let path = root.join(file.path());
                wanted.push(path.clone());
                if fs::read(&path).map(|existing| existing != file.contents()).unwrap_or(true) {
                    if let Some(parent) = path.parent() {
                        fs::create_dir_all(parent)?;
                    }
                    fs::write(&path, file.contents())?;
                }
            }
        }
    }
    Ok(())
}

fn remove_stale(dir: &Path, wanted: &[PathBuf]) -> std::io::Result<()> {
    for entry in fs::read_dir(dir)? {
        let path = entry?.path();
        if path.is_dir() {
            remove_stale(&path, wanted)?;
            if fs::read_dir(&path).map(|mut d| d.next().is_none()).unwrap_or(false) {
                let _ = fs::remove_dir(&path);
            }
        } else if !wanted.contains(&path) {
            let _ = fs::remove_file(&path);
        }
    }
    Ok(())
}

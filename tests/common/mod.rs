//! Shared by the integration tests: every test gets an isolated config/cache/run folder of its own.
#![allow(dead_code)]
use std::sync::Once;

static INIT: Once = Once::new();

/// Call first in every test. Keeps the folder alive until dropped.
pub fn isolated() -> tempfile::TempDir {
    INIT.call_once(|| {
        // the widgets come from the checkout, never from a cache folder
        std::env::set_var("DESKWIDGETS_DATA", env!("CARGO_MANIFEST_DIR"));
        std::env::set_var("DESKWIDGETS_KEEP_OLD", "1"); // never stop the developer's real widgets
        deskwidgets::core::i18n::set_lang("en");
    });
    let dir = tempfile::tempdir().unwrap();
    deskwidgets::core::paths::use_root_for_this_thread(dir.path());
    deskwidgets::core::paths::ensure_dirs();
    deskwidgets::core::i18n::set_lang("en");
    dir
}

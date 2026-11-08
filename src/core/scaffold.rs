//! `deskwidgets new <id>`: a ready-to-edit widget in ~/.config/deskwidgets/widgets/<id>.
use std::fs;
use std::path::PathBuf;

use serde_json::json;

use super::paths;

const LUA: &str = r#"-- {widget_id}: your widget. Edit me, then run `deskwidgets reload` (or Reload in the gallery menu).
-- API: docs/creating-widgets.md. Examples: the built-in widgets in {builtin}
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {}
local NAME = {lua_name}
local GREETING = L.opt('greeting', 'Hello!')   -- an option from widget.json

-- tick() runs before every draw: read files, call commands (sparingly), keep state
local count = 0
function W.tick()
    count = count + 1
end

-- draw() paints the card. (x, y) is its top-left corner, w x h its size in base units:
-- small 170x170, medium 356x170, large 356x356. L.SIZE tells you which one.
function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)                                  -- background in the chosen style
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.star, L.acc(L.SYS.purple), NAME)

    L.font(cr, F.light, 40); L.set(cr, C.text)
    L.text(cr, GREETING, x + PAD, y + 90)

    L.font(cr, F.regular, 12); L.set(cr, C.text3)
    L.text(cr, L.tr('оновлень: ', 'updates: ') .. count, x + PAD, y + h - PAD)
end

-- optional: left click inside the card (coordinates relative to the card)
function W.click(cx, cy, w, h)
    L.notify(NAME, GREETING)
    return true
end

return W
"#;

/// Errors with `AlreadyExists` when the folder is taken.
pub fn create(widget_id: &str, name: Option<&str>) -> std::io::Result<PathBuf> {
    let mut id: String = widget_id.to_lowercase().chars().map(|c| if c.is_ascii_alphanumeric() || c == '_' { c } else { '\u{0}' }).collect();
    // runs of anything else become one underscore
    let mut collapsed = String::new();
    let mut last_sep = false;
    for c in id.drain(..) {
        if c == '\u{0}' {
            if !last_sep {
                collapsed.push('_');
            }
            last_sep = true;
        } else {
            collapsed.push(c);
            last_sep = false;
        }
    }
    let collapsed = collapsed.trim_matches('_').to_string();
    let widget_id = if collapsed.is_empty() { "my_widget".to_string() } else { collapsed };
    let folder = paths::user_widgets().join(&widget_id);
    if folder.exists() {
        return Err(std::io::Error::new(std::io::ErrorKind::AlreadyExists, folder.display().to_string()));
    }
    fs::create_dir_all(&folder)?;
    let name = name.map(str::to_string).unwrap_or_else(|| title_case(&widget_id));
    let manifest = json!({
        "id": widget_id,
        "version": "1.0",
        "author": user(),
        "category": "personal",
        "name": {"uk": name, "en": name},
        "description": {"uk": "Мій віджет", "en": "My widget"},
        "sizes": ["small", "medium", "large"],
        "interval": 5,
        "options": [
            {"key": "greeting", "type": "text", "default": "Hello!", "label": {"uk": "Привітання", "en": "Greeting"}}
        ],
    });
    fs::write(folder.join("widget.json"), serde_json::to_string_pretty(&manifest).unwrap() + "\n")?;
    fs::write(
        folder.join("widget.lua"),
        LUA.replace("{widget_id}", &widget_id).replace("{lua_name}", &lua_string(&name)).replace("{builtin}", &paths::builtin_widgets().display().to_string()),
    )?;
    Ok(folder)
}

fn title_case(id: &str) -> String {
    id.split('_')
        .filter(|w| !w.is_empty())
        .map(|w| {
            let mut chars = w.chars();
            chars.next().map(|f| f.to_uppercase().collect::<String>() + chars.as_str()).unwrap_or_default()
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// A Lua string literal: control characters use Lua's decimal escapes, quotes and backslashes are escaped.
fn lua_string(text: &str) -> String {
    let mut out = String::from("\"");
    for c in text.chars() {
        match c {
            '"' | '\\' => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 32 => out.push_str(&format!("\\{:03}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn user() -> String {
    std::env::var(if cfg!(windows) { "USERNAME" } else { "USER" }).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lua_literals_and_titles() {
        assert_eq!(lua_string("a\"b\\c\nd"), "\"a\\\"b\\\\c\\010d\"");
        assert_eq!(title_case("my_cool_widget"), "My Cool Widget");
    }
}

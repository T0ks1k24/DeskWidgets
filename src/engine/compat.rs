//! A small subset of conky's `${variable}` syntax, so widgets written for DeskWidgets 1.x/2.x
//! (`L.cp('${cpu cpu0}')`) keep working. New widgets should use `L.sys` instead.
use regex::Regex;
use serde_json::Value;

use super::sysinfo::SysInfo;

pub fn conky_parse(text: &str, info: &SysInfo) -> String {
    static VARIABLE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = VARIABLE.get_or_init(|| Regex::new(r"\$\{([^}]*)\}|\$([a-z_]+)").unwrap());
    re.replace_all(text, |caps: &regex::Captures| {
        let inner = caps.get(1).or_else(|| caps.get(2)).map(|m| m.as_str()).unwrap_or("");
        let words: Vec<&str> = inner.split_whitespace().collect();
        value(&words, info)
    })
    .into_owned()
}

fn num(v: &Value) -> f64 {
    v.as_f64().unwrap_or(0.0)
}

fn show(v: &Value) -> String {
    match v {
        Value::Number(n) if n.is_i64() => n.to_string(),
        Value::Number(n) => format!("{}", n.as_f64().unwrap_or(0.0)),
        Value::String(s) => s.clone(),
        _ => String::new(),
    }
}

fn value(words: &[&str], info: &SysInfo) -> String {
    let Some(name) = words.first() else { return String::new() };
    let args = &words[1..];
    let arg = args.first().copied().unwrap_or("");
    match *name {
        "cpu" => {
            let cpu = info.cpu();
            if let Some(index) = arg.strip_prefix("cpu").and_then(|d| d.parse::<usize>().ok()) {
                if index == 0 {
                    return show(&cpu["total"]);
                }
                return cpu["cores"].get(index - 1).map(show).unwrap_or_else(|| "0".into());
            }
            show(&cpu["total"])
        }
        "processes" => show(&info.cpu()["processes"]),
        "loadavg" => {
            let key = match arg {
                "5" => "load5",
                "15" => "load15",
                _ => "load1",
            };
            show(&info.cpu()[key])
        }
        "freq_g" => show(&info.cpu()["freq_ghz"]),
        "mem" | "memmax" | "memperc" | "swap" | "swapmax" | "swapperc" => {
            let m = info.memory();
            let (total, used) = if name.starts_with("mem") { (num(&m["total"]), num(&m["used"])) } else { (num(&m["swap_total"]), num(&m["swap_used"])) };
            if name.ends_with("perc") {
                return if total > 0.0 { format!("{}", (100.0 * used / total).round() as i64) } else { "0".into() };
            }
            size(if name.ends_with("max") { total } else { used })
        }
        "top" => {
            let index = args.get(1).and_then(|s| s.parse::<usize>().ok()).unwrap_or(1).max(1);
            let rows = info.top(index);
            let Some(row) = rows.get(index - 1) else { return String::new() };
            match arg {
                "name" => show(&row["name"]),
                "cpu" => show(&row["cpu"]),
                "mem_res" => size(num(&row["mem"])),
                _ => String::new(),
            }
        }
        "downspeedf" | "upspeedf" => format!("{:.1}", num(&info.net()[if *name == "downspeedf" { "down" } else { "up" }])),
        "totaldown" => size(num(&info.net()["total_down"])),
        "totalup" => size(num(&info.net()["total_up"])),
        "addr" => show(&info.net()["ip"]),
        "wireless_essid" => {
            let n = info.net();
            if n["kind"] == "wifi" { show(&n["name"]) } else { String::new() }
        }
        "fs_used_perc" | "fs_used" | "fs_size" => {
            let usage = info.disk(if arg.is_empty() { None } else { Some(arg) });
            let (total, used) = (num(&usage["total"]), num(&usage["used"]));
            match *name {
                "fs_used_perc" => if total > 0.0 { format!("{}", (100.0 * used / total).round() as i64) } else { "0".into() },
                "fs_used" => size(used),
                _ => size(total),
            }
        }
        _ => String::new(),
    }
}

/// Like conky: 1.5GiB, 120MiB.
fn size(mut number: f64) -> String {
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"] {
        if number < 1024.0 || unit == "TiB" {
            return if matches!(unit, "B" | "KiB" | "MiB") { format!("{number:.0}{unit}") } else { format!("{number:.1}{unit}") };
        }
        number /= 1024.0;
    }
    unreachable!()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sizes_and_unknown_variables() {
        assert_eq!(size(512.0), "512B");
        assert_eq!(size(1536.0 * 1024.0 * 1024.0), "1.5GiB");
        let info = SysInfo::new(false);
        assert_eq!(conky_parse("a${nothing}b", &info), "ab");
        assert!(!conky_parse("${mem}", &info).is_empty());
    }
}

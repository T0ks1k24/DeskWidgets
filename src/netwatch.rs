//! LAN device watch for the Network widget (runs in the background, started by widget.lua as
//! `deskwidgets netwatch ...`).
//!
//! Ping-scans the subnet of the default route with nmap and flags every device whose MAC is not
//! listed in KNOWN ("mac|friendly name" per line). Notifies once per new unknown device.
//! Works on Linux (`ip neigh`) and Windows (`arp -a`).
//!
//! The result is written to CACHE: state (OK | ALERT | OFFLINE | NONMAP), device count,
//! unknown count, first unknown IP; one value per line.
use std::collections::HashSet;
use std::io::Write;
use std::path::Path;
use std::time::Duration;

use regex::Regex;
use serde_json::json;

use crate::core::util::{run_command, which, write_atomic};
use crate::engine::sysinfo::default_route_interface;
use crate::service::ipc;

fn write_cache(path: &Path, lines: &[&str]) {
    let _ = write_atomic(path, &(lines.join("\n") + "\n"));
}

pub fn default_gateway() -> String {
    if cfg!(windows) {
        let (_, out) = run_command("route", &["print", "-4", "0.0.0.0"], Duration::from_secs(60));
        for line in out.lines() {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 3 && parts[0] == "0.0.0.0" && parts[1] == "0.0.0.0" {
                return parts[2].to_string();
            }
        }
        return String::new();
    }
    let Ok(text) = std::fs::read_to_string("/proc/net/route") else { return String::new() };
    let mut best: Option<(u32, String)> = None;
    for line in text.lines().skip(1) {
        let f: Vec<&str> = line.split_whitespace().collect();
        if f.len() > 6 && f[1] == "00000000" {
            let metric: u32 = f[6].parse().unwrap_or(u32::MAX);
            if let Ok(raw) = u32::from_str_radix(f[2], 16) {
                let addr = std::net::Ipv4Addr::from(raw.swap_bytes());
                if best.as_ref().map(|b| metric < b.0).unwrap_or(true) {
                    best = Some((metric, addr.to_string()));
                }
            }
        }
    }
    best.map(|b| b.1).unwrap_or_default()
}

fn mac_pattern() -> Regex {
    Regex::new(r"^([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$").unwrap()
}

/// [(ip, mac)] of the devices the OS has seen answer on the LAN.
pub fn neighbours(iface: &str, ip: &str) -> Vec<(String, String)> {
    let mac = mac_pattern();
    let mut found = Vec::new();
    if cfg!(windows) {
        let (_, out) = run_command("arp", &["-a"], Duration::from_secs(60));
        let mut ours = false;
        for line in out.lines() {
            if line.starts_with("Interface:") {
                ours = line.contains(ip);
            } else if ours {
                let f: Vec<&str> = line.split_whitespace().collect();
                if f.len() >= 3 && mac.is_match(f[1]) && f[2].eq_ignore_ascii_case("dynamic") {
                    found.push((f[0].to_string(), f[1].replace('-', ":")));
                }
            }
        }
        return found;
    }
    let (_, out) = run_command("ip", &["neigh", "show", "dev", iface], Duration::from_secs(60));
    for line in out.lines() {
        let f: Vec<&str> = line.split_whitespace().collect();
        if let Some(pos) = f.iter().position(|w| *w == "lladdr") {
            if f.last() != Some(&"FAILED") && pos + 1 < f.len() && mac.is_match(f[pos + 1]) {
                found.push((f[0].to_string(), f[pos + 1].to_string()));
            }
        }
    }
    found
}

fn vendor(mac: &str, nmap: &Path) -> String {
    let prefix: String = mac.chars().filter(|c| *c != ':' && *c != '-').collect::<String>().to_uppercase().chars().take(6).collect();
    let mut candidates = vec![std::path::PathBuf::from("/usr/share/nmap/nmap-mac-prefixes")];
    if let Some(dir) = nmap.parent() {
        candidates.push(dir.join("nmap-mac-prefixes"));
    }
    for db in candidates {
        if let Ok(text) = std::fs::read_to_string(&db) {
            for line in text.lines() {
                if let Some(rest) = line.strip_prefix(&prefix) {
                    return rest.trim().to_string();
                }
            }
        }
    }
    String::new()
}

pub fn run(cache: &Path, known_path: &Path, seen_path: &Path, log_path: &Path, lang: &str) -> i32 {
    let tr = |uk: &'static str, en: &'static str| if lang == "en" { en } else { uk };
    let Some(nmap) = which("nmap") else {
        write_cache(cache, &["NONMAP"]);
        return 0;
    };
    for path in [known_path, seen_path, log_path] {
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let _ = std::fs::OpenOptions::new().create(true).append(true).open(path);
    }
    let Some((iface, ip, prefix)) = default_route_interface() else {
        write_cache(cache, &["OFFLINE"]);
        return 0;
    };
    let gateway = default_gateway();
    let net = subnet(&ip, prefix);
    // an unprivileged ping scan also fills the neighbour table
    run_command(&nmap.to_string_lossy(), &["-sn", &net], Duration::from_secs(120));

    let known: HashSet<String> = std::fs::read_to_string(known_path)
        .unwrap_or_default()
        .lines()
        .filter(|l| l.contains('|'))
        .map(|l| l.split('|').next().unwrap_or("").trim().to_lowercase())
        .collect();
    let seen: HashSet<String> = std::fs::read_to_string(seen_path).unwrap_or_default().lines().map(|l| l.trim().to_lowercase()).collect();
    let (mut macs, mut unknown): (HashSet<String>, Vec<(String, String, String)>) = (HashSet::new(), Vec::new());
    for (address, mac) in neighbours(&iface, &ip) {
        let mac = mac.to_lowercase();
        if address == gateway || address == ip || macs.contains(&mac) {
            continue;
        }
        macs.insert(mac.clone());
        if !known.contains(&mac) {
            let maker = vendor(&mac, &nmap);
            unknown.push((address, mac, maker));
        }
    }
    for (address, mac, maker) in &unknown {
        if seen.contains(mac) {
            continue;
        }
        if let Ok(mut f) = std::fs::OpenOptions::new().append(true).open(seen_path) {
            let _ = writeln!(f, "{mac}");
        }
        if let Ok(mut f) = std::fs::OpenOptions::new().append(true).open(log_path) {
            let now = chrono::Local::now().format("%Y-%m-%d %H:%M:%S");
            let _ = writeln!(f, "{now} {}: {address} · {mac} · {}", tr("НОВИЙ пристрій", "NEW device"), if maker.is_empty() { "?" } else { maker });
        }
        let body = format!("{address} · {}\nMAC: {mac}", if maker.is_empty() { tr("невідомий виробник", "unknown vendor") } else { maker });
        let _ = ipc::request("notify", json!({"title": tr("Новий пристрій у мережі", "New device on your network"), "body": body}), Duration::from_secs(15));
    }
    let count = macs.len().to_string();
    if unknown.is_empty() {
        write_cache(cache, &["OK", &count, "", ""]);
    } else {
        write_cache(cache, &["ALERT", &count, &unknown.len().to_string(), &unknown[0].0]);
    }
    0
}

/// "192.168.1.23" with prefix 24 -> "192.168.1.0/24".
pub fn subnet(ip: &str, prefix: u8) -> String {
    let Ok(addr) = ip.parse::<std::net::Ipv4Addr>() else { return format!("{ip}/{prefix}") };
    let mask = if prefix == 0 { 0 } else { u32::MAX << (32 - prefix.min(32)) };
    format!("{}/{}", std::net::Ipv4Addr::from(u32::from(addr) & mask), prefix)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn subnets() {
        assert_eq!(subnet("192.168.1.23", 24), "192.168.1.0/24");
        assert_eq!(subnet("10.20.30.40", 16), "10.20.0.0/16");
        assert_eq!(subnet("10.20.30.40", 32), "10.20.30.40/32");
    }

    #[test]
    fn mac_addresses() {
        assert!(mac_pattern().is_match("aa:bb:cc:dd:ee:ff"));
        assert!(mac_pattern().is_match("AA-BB-CC-DD-EE-FF"));
        assert!(!mac_pattern().is_match("aa:bb:cc"));
    }
}

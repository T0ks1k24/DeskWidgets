//! System numbers for the widgets (CPU, memory, disks, network, battery ...), the same on every OS.
//!
//! Widgets call these from `tick()`/`draw()`, so every getter is cheap: it returns the last snapshot,
//! and a background thread refreshes only the parts that widgets asked for recently.
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use sysinfo::{Disks, Networks, ProcessesToUpdate, System};

use crate::core::util::log;
#[cfg(windows)]
use crate::core::util;

// seconds between refreshes of each part
fn every(part: &str) -> f64 {
    match part {
        "cpu" | "net" => 1.0,
        "top" | "memory" => 2.0,
        "disks" => 30.0,
        "battery" => 10.0,
        "temps" => 5.0,
        _ => 5.0,
    }
}
const IDLE_AFTER: f64 = 20.0; // stop refreshing a part nobody asked for in this many seconds

/// File systems that hold the user's data (the Disks widget skips pseudo and removable ones).
const REAL_FS: [&str; 17] = [
    "ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "ntfs", "ntfs3", "fuseblk", "exfat", "zfs", "apfs", "hfs", "refs", "fat32", "jfs", "reiserfs",
];
const SKIP_MOUNTS: [&str; 4] = ["/snap", "/boot", "/var/lib/docker", "/var/snap"];

/// The system disk: "/" on Linux, the Windows drive (usually C:\).
pub fn system_root() -> PathBuf {
    if cfg!(windows) {
        PathBuf::from(format!("{}\\", std::env::var("SystemDrive").unwrap_or_else(|_| "C:".into())))
    } else {
        PathBuf::from("/")
    }
}

struct Sources {
    sys: System,
    networks: Networks,
    net_prev: Option<(String, Instant, u64, u64)>,
    wifi_name: (String, Option<Instant>),
    cpu_primed: bool,
}

struct Shared {
    sources: Mutex<Sources>,
    data: Mutex<HashMap<&'static str, Value>>,
    stamp: Mutex<HashMap<&'static str, Instant>>,
    asked: Mutex<HashMap<&'static str, Instant>>,
    failed: Mutex<HashSet<&'static str>>,
    stop: AtomicBool,
    threaded: bool,
}

#[derive(Clone)]
pub struct SysInfo {
    shared: Arc<Shared>,
    started: Arc<AtomicBool>,
}

const PARTS: [&str; 7] = ["cpu", "top", "memory", "disks", "net", "battery", "temps"];

impl SysInfo {
    pub fn new(threaded: bool) -> SysInfo {
        SysInfo {
            shared: Arc::new(Shared {
                sources: Mutex::new(Sources {
                    sys: System::new(),
                    networks: Networks::new(),
                    net_prev: None,
                    wifi_name: (String::new(), None),
                    cpu_primed: false,
                }),
                data: Mutex::new(HashMap::new()),
                stamp: Mutex::new(HashMap::new()),
                asked: Mutex::new(HashMap::new()),
                failed: Mutex::new(HashSet::new()),
                stop: AtomicBool::new(false),
                threaded,
            }),
            started: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn stop(&self) {
        self.shared.stop.store(true, Ordering::Relaxed);
    }

    fn part(&self, name: &'static str) -> Value {
        let shared = &self.shared;
        let now = Instant::now();
        shared.asked.lock().unwrap().insert(name, now);
        if shared.threaded && !self.started.swap(true, Ordering::SeqCst) {
            let me = self.clone();
            std::thread::Builder::new().name("sysinfo".into()).spawn(move || me.background()).ok();
        }
        // the background thread keeps it fresh; without one (previews, tests) refresh on demand
        let missing = !shared.data.lock().unwrap().contains_key(name);
        let stale = !shared.threaded
            && shared.stamp.lock().unwrap().get(name).map(|t| now.duration_since(*t).as_secs_f64() >= every(name)).unwrap_or(true);
        if missing || stale {
            self.refresh(name);
        }
        shared.data.lock().unwrap().get(name).cloned().unwrap_or(Value::Null)
    }

    fn background(&self) {
        while !self.shared.stop.load(Ordering::Relaxed) {
            std::thread::sleep(Duration::from_millis(500));
            let now = Instant::now();
            for part in PARTS {
                let asked = self.shared.asked.lock().unwrap().get(part).copied();
                let stamp = self.shared.stamp.lock().unwrap().get(part).copied();
                let wanted = asked.map(|a| now.duration_since(a).as_secs_f64() < IDLE_AFTER).unwrap_or(false);
                let due = stamp.map(|s| now.duration_since(s).as_secs_f64() >= every(part)).unwrap_or(true);
                if wanted && due {
                    self.refresh(part);
                }
            }
        }
    }

    fn refresh(&self, part: &'static str) {
        let value = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| self.read(part)));
        let value = match value {
            Ok(v) => v,
            Err(_) => {
                if self.shared.failed.lock().unwrap().insert(part) {
                    log(format!("system info \"{part}\": reading failed"));
                }
                empty(part)
            }
        };
        self.shared.data.lock().unwrap().insert(part, value);
        self.shared.stamp.lock().unwrap().insert(part, Instant::now());
    }

    fn read(&self, part: &'static str) -> Value {
        match part {
            "cpu" => self.read_cpu(),
            "top" => self.read_top(),
            "memory" => self.read_memory(),
            "disks" => read_disks(),
            "net" => self.read_net(),
            "battery" => read_battery(),
            "temps" => read_temps(),
            _ => Value::Null,
        }
    }

    // what widgets call
    /// {total, cores: [..], count, load1, load5, load15, processes, freq_ghz}; percentages 0-100.
    pub fn cpu(&self) -> Value {
        self.part("cpu")
    }
    /// The busiest processes: [{name, cpu, mem}] (cpu as in `top`, mem = resident bytes).
    pub fn top(&self, n: usize) -> Value {
        match self.part("top") {
            Value::Array(rows) => Value::Array(rows.into_iter().take(n).collect()),
            _ => json!([]),
        }
    }
    /// Bytes: {total, used, available, cached, swap_total, swap_used}.
    pub fn memory(&self) -> Value {
        self.part("memory")
    }
    pub fn uptime(&self) -> f64 {
        System::uptime() as f64
    }
    /// Real file systems: [{name, mount, device, fstype, total, used}], the system disk first.
    pub fn disks(&self) -> Value {
        match self.part("disks") {
            Value::Null => json!([]),
            other => other,
        }
    }
    /// Usage of the disk holding `path` (the system disk by default): {total, used}.
    pub fn disk(&self, path: Option<&str>) -> Value {
        let root = system_root();
        let path = path.map(PathBuf::from).unwrap_or(root);
        match disk_usage(&path) {
            Some((total, used)) => json!({"total": total, "used": used}),
            None => json!({"total": 0, "used": 0}),
        }
    }
    /// {iface, kind: "wifi"|"ethernet", name, ip, down, up (KiB/s), total_down, total_up (bytes)}.
    pub fn net(&self) -> Value {
        self.part("net")
    }
    /// {percent, plugged, charging, full, secs_left (or nil)}; nil when the machine has no battery.
    pub fn battery(&self) -> Value {
        self.part("battery")
    }
    /// {cpu, gpu} in degrees Celsius; missing when the OS exposes no sensors.
    pub fn temps(&self) -> Value {
        self.part("temps")
    }

    // readers
    fn read_cpu(&self) -> Value {
        let mut src = self.shared.sources.lock().unwrap();
        src.sys.refresh_cpu_usage();
        if !src.cpu_primed {
            // the first measurement needs a second sample to mean anything
            src.cpu_primed = true;
            if !self.shared.threaded {
                std::thread::sleep(sysinfo::MINIMUM_CPU_UPDATE_INTERVAL);
                src.sys.refresh_cpu_usage();
            }
        }
        let cores: Vec<f64> = src.sys.cpus().iter().map(|c| c.cpu_usage() as f64).collect();
        let total = if cores.is_empty() { 0.0 } else { cores.iter().sum::<f64>() / cores.len() as f64 };
        let load = System::load_average();
        let freq = src.sys.cpus().first().map(|c| c.frequency()).unwrap_or(0);
        let processes = process_count(&mut src.sys);
        json!({
            "total": round(total),
            "cores": cores.iter().map(|c| round(*c)).collect::<Vec<_>>(),
            "count": cores.len().max(1),
            "load1": round2(load.one), "load5": round2(load.five), "load15": round2(load.fifteen),
            "processes": processes,
            "freq_ghz": (freq as f64 / 100.0).round() / 10.0,
        })
    }

    fn read_top(&self) -> Value {
        let mut src = self.shared.sources.lock().unwrap();
        src.sys.refresh_processes(ProcessesToUpdate::All, true);
        let mut rows: Vec<(String, f64, u64)> = src
            .sys
            .processes()
            .values()
            .filter_map(|p| {
                let name = p.name().to_string_lossy().into_owned();
                (!name.is_empty()).then(|| (name, (p.cpu_usage() as f64 * 10.0).round() / 10.0, p.memory()))
            })
            .collect();
        rows.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal).then(b.2.cmp(&a.2)));
        Value::Array(rows.into_iter().take(12).map(|(name, cpu, mem)| json!({"name": name, "cpu": cpu, "mem": mem})).collect())
    }

    fn read_memory(&self) -> Value {
        let mut src = self.shared.sources.lock().unwrap();
        src.sys.refresh_memory();
        let total = src.sys.total_memory().max(1);
        let available = src.sys.available_memory();
        json!({
            "total": total,
            "used": total.saturating_sub(available),
            "available": available,
            "cached": cached_memory(),
            "swap_total": src.sys.total_swap(),
            "swap_used": src.sys.used_swap(),
        })
    }

    fn read_net(&self) -> Value {
        let mut src = self.shared.sources.lock().unwrap();
        src.networks.refresh(true);
        let addr = outbound_address();
        let iface = default_interface(&src.networks, addr.as_deref());
        let Some(iface) = iface else { return empty("net") };
        let (received, sent) = src
            .networks
            .iter()
            .find(|(n, _)| **n == iface)
            .map(|(_, d)| (d.total_received(), d.total_transmitted()))
            .unwrap_or((0, 0));
        let now = Instant::now();
        let (mut down, mut up) = (0.0, 0.0);
        if let Some((prev_iface, at, prev_rx, prev_tx)) = &src.net_prev {
            if *prev_iface == iface {
                let dt = now.duration_since(*at).as_secs_f64().max(1e-3);
                down = (received.saturating_sub(*prev_rx) as f64 / dt / 1024.0).max(0.0);
                up = (sent.saturating_sub(*prev_tx) as f64 / dt / 1024.0).max(0.0);
            }
        }
        src.net_prev = Some((iface.clone(), now, received, sent));
        let wifi = is_wifi(&iface);
        if wifi && src.wifi_name.1.map(|t| now.duration_since(t).as_secs() > 30).unwrap_or(true) {
            src.wifi_name = (wifi_name(&iface), Some(now));
        }
        let ip = src
            .networks
            .iter()
            .find(|(n, _)| **n == iface)
            .and_then(|(_, d)| d.ip_networks().iter().find(|ip| ip.addr.is_ipv4()).map(|ip| ip.addr.to_string()))
            .unwrap_or_default();
        json!({
            "iface": iface,
            "kind": if wifi { "wifi" } else { "ethernet" },
            "name": if wifi { src.wifi_name.0.clone() } else { "Ethernet".to_string() },
            "ip": ip, "down": down, "up": up, "total_down": received, "total_up": sent,
        })
    }
}

fn round(v: f64) -> i64 {
    v.round() as i64
}
fn round2(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}

fn empty(part: &str) -> Value {
    match part {
        "cpu" => json!({"total": 0, "cores": [], "count": 1, "load1": 0, "load5": 0, "load15": 0, "processes": 0, "freq_ghz": 0}),
        "memory" => json!({"total": 1, "used": 0, "available": 1, "cached": 0, "swap_total": 0, "swap_used": 0}),
        "net" => json!({"iface": "", "kind": "", "name": "", "ip": "", "down": 0.0, "up": 0.0, "total_down": 0, "total_up": 0}),
        "temps" => json!({}),
        "top" | "disks" => json!([]),
        _ => Value::Null,
    }
}

fn process_count(sys: &mut System) -> usize {
    #[cfg(target_os = "linux")]
    {
        let _ = sys;
        std::fs::read_dir("/proc")
            .map(|d| d.filter_map(|e| e.ok()).filter(|e| e.file_name().to_string_lossy().bytes().all(|b| b.is_ascii_digit())).count())
            .unwrap_or(0)
    }
    #[cfg(not(target_os = "linux"))]
    {
        sys.refresh_processes(ProcessesToUpdate::All, true);
        sys.processes().len()
    }
}

/// Page cache and buffers, the way `free` counts them (Linux only).
fn cached_memory() -> u64 {
    let Ok(text) = std::fs::read_to_string("/proc/meminfo") else { return 0 };
    let mut total = 0;
    for line in text.lines() {
        let mut parts = line.split_whitespace();
        let (Some(key), Some(value)) = (parts.next(), parts.next()) else { continue };
        if matches!(key, "Cached:" | "Buffers:" | "SReclaimable:") {
            total += value.parse::<u64>().unwrap_or(0) * 1024;
        }
    }
    total
}

/// (total, used) bytes of the file system holding `path`.
pub fn disk_usage(path: &Path) -> Option<(u64, u64)> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        let c = std::ffi::CString::new(path.as_os_str().as_bytes()).ok()?;
        let mut st: libc::statvfs = unsafe { std::mem::zeroed() };
        if unsafe { libc::statvfs(c.as_ptr(), &mut st) } != 0 {
            return None;
        }
        let frsize = st.f_frsize as u64;
        let total = st.f_blocks as u64 * frsize;
        let used = (st.f_blocks as u64).saturating_sub(st.f_bfree as u64) * frsize;
        Some((total, used))
    }
    #[cfg(not(unix))]
    {
        let disks = Disks::new_with_refreshed_list();
        let best = disks
            .iter()
            .filter(|d| path.starts_with(d.mount_point()))
            .max_by_key(|d| d.mount_point().as_os_str().len())?;
        Some((best.total_space(), best.total_space().saturating_sub(best.available_space())))
    }
}

fn read_disks() -> Value {
    let root = system_root();
    let home = crate::core::paths::home().to_path_buf();
    let mut out: Vec<(bool, Value)> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    let disks = Disks::new_with_refreshed_list();
    for disk in disks.iter() {
        let fstype = disk.file_system().to_string_lossy().to_lowercase();
        let device = disk.name().to_string_lossy().into_owned();
        let mount = disk.mount_point().to_path_buf();
        let mount_text = mount.to_string_lossy().into_owned();
        if !REAL_FS.contains(&fstype.as_str()) || seen.contains(&device) {
            continue;
        }
        if SKIP_MOUNTS.iter().any(|s| mount_text.starts_with(s)) {
            continue;
        }
        let Some((total, used)) = disk_usage(&mount) else { continue };
        seen.insert(device.clone());
        let name = if mount == root {
            "system".to_string()
        } else if mount == home {
            "home".to_string()
        } else if cfg!(windows) {
            mount_text.trim_end_matches(['\\', '/']).to_string()
        } else {
            mount.file_name().map(|n| n.to_string_lossy().into_owned()).filter(|n| !n.is_empty()).unwrap_or_else(|| mount_text.clone())
        };
        let is_root = mount == root;
        out.push((is_root, json!({"name": name, "mount": mount_text, "device": device, "fstype": fstype, "total": total, "used": used})));
    }
    out.sort_by_key(|(is_root, _)| !*is_root);
    Value::Array(out.into_iter().map(|(_, v)| v).collect())
}

#[cfg(target_os = "linux")]
fn read_battery() -> Value {
    let dir = Path::new("/sys/class/power_supply");
    let Ok(entries) = std::fs::read_dir(dir) else { return Value::Null };
    let read = |p: &Path, name: &str| std::fs::read_to_string(p.join(name)).ok().map(|s| s.trim().to_string());
    let mut battery: Option<PathBuf> = None;
    let mut ac_online = false;
    let mut names: Vec<PathBuf> = entries.filter_map(|e| e.ok()).map(|e| e.path()).collect();
    names.sort();
    for p in names {
        match read(&p, "type").as_deref() {
            Some("Battery") if battery.is_none() && read(&p, "scope").as_deref() != Some("Device") && read(&p, "capacity").is_some() => {
                battery = Some(p)
            }
            Some("Mains") | Some("USB") | Some("USB_C") | Some("USB_PD") => {
                ac_online |= read(&p, "online").as_deref() == Some("1");
            }
            _ => {}
        }
    }
    let Some(b) = battery else { return Value::Null };
    let percent: f64 = read(&b, "capacity").and_then(|c| c.parse().ok()).unwrap_or(0.0);
    let status = read(&b, "status").unwrap_or_default();
    let plugged = ac_online || matches!(status.as_str(), "Charging" | "Full");
    let secs_left = if status == "Discharging" {
        let num = |name: &str| read(&b, name).and_then(|v| v.parse::<f64>().ok()).filter(|v| *v > 0.0);
        match (num("energy_now"), num("power_now")) {
            (Some(e), Some(p)) => Some((e / p * 3600.0) as i64),
            _ => match (num("charge_now"), num("current_now")) {
                (Some(c), Some(i)) => Some((c / i * 3600.0) as i64),
                _ => None,
            },
        }
    } else {
        None
    };
    json!({
        "percent": percent.round() as i64,
        "plugged": plugged,
        "charging": plugged && percent < 99.5,
        "full": plugged && percent >= 99.5,
        "secs_left": secs_left,
    })
}

#[cfg(windows)]
fn read_battery() -> Value {
    use windows::Win32::System::Power::{GetSystemPowerStatus, SYSTEM_POWER_STATUS};
    let mut status = SYSTEM_POWER_STATUS::default();
    if unsafe { GetSystemPowerStatus(&mut status) }.is_err() || status.BatteryFlag == 128 || status.BatteryLifePercent == 255 {
        return Value::Null;
    }
    let plugged = status.ACLineStatus == 1;
    let percent = status.BatteryLifePercent as f64;
    json!({
        "percent": percent as i64,
        "plugged": plugged,
        "charging": plugged && percent < 99.5,
        "full": plugged && percent >= 99.5,
        "secs_left": if status.BatteryLifeTime != u32::MAX { Some(status.BatteryLifeTime as i64) } else { None },
    })
}

#[cfg(not(any(target_os = "linux", windows)))]
fn read_battery() -> Value {
    Value::Null
}

fn read_temps() -> Value {
    let mut out = serde_json::Map::new();
    let Ok(entries) = std::fs::read_dir("/sys/class/hwmon") else { return Value::Object(out) };
    let mut first: HashMap<String, f64> = HashMap::new();
    let mut dirs: Vec<PathBuf> = entries.filter_map(|e| e.ok()).map(|e| e.path()).collect();
    dirs.sort();
    for dir in dirs {
        let Some(name) = std::fs::read_to_string(dir.join("name")).ok().map(|s| s.trim().to_string()) else { continue };
        if first.contains_key(&name) {
            continue;
        }
        let mut inputs: Vec<PathBuf> = std::fs::read_dir(&dir)
            .map(|d| d.filter_map(|e| e.ok()).map(|e| e.path()).collect())
            .unwrap_or_default();
        inputs.retain(|p| p.file_name().map(|n| { let n = n.to_string_lossy(); n.starts_with("temp") && n.ends_with("_input") }).unwrap_or(false));
        inputs.sort();
        if let Some(value) = inputs.first().and_then(|p| std::fs::read_to_string(p).ok()).and_then(|s| s.trim().parse::<f64>().ok()) {
            first.insert(name, value / 1000.0);
        }
    }
    for (key, names) in [("cpu", &["k10temp", "coretemp", "cpu_thermal", "zenpower", "acpitz"][..]), ("gpu", &["amdgpu", "nouveau", "nvidia"][..])] {
        if let Some(value) = names.iter().find_map(|n| first.get(*n)) {
            out.insert(key.into(), json!(value.round() as i64));
        }
    }
    Value::Object(out)
}

/// The local address the OS would use to reach the internet. No packets are sent: connecting a UDP
/// socket only makes the OS choose a route.
fn outbound_address() -> Option<String> {
    let socket = std::net::UdpSocket::bind("0.0.0.0:0").ok()?;
    socket.connect("192.0.2.1:9").ok()?; // TEST-NET-1: never routed anywhere
    Some(socket.local_addr().ok()?.ip().to_string())
}

/// The interface the machine reaches the internet through.
pub fn default_interface(networks: &Networks, address: Option<&str>) -> Option<String> {
    if let Some(addr) = address {
        for (name, data) in networks.iter() {
            if data.ip_networks().iter().any(|ip| ip.addr.to_string() == addr) {
                return Some(name.clone());
            }
        }
    }
    // offline or odd routing: the first interface with a real IPv4 address
    let mut names: Vec<&String> = networks.iter().map(|(n, _)| n).collect();
    names.sort();
    for name in names {
        let lowered = name.to_lowercase();
        if lowered.starts_with("lo") || lowered.starts_with("loopback") {
            continue;
        }
        let data = networks.iter().find(|(n, _)| *n == name).map(|(_, d)| d)?;
        if data.ip_networks().iter().any(|ip| ip.addr.is_ipv4() && !ip.addr.is_loopback()) {
            return Some(name.clone());
        }
    }
    None
}

/// The default interface right now and its IPv4 address, for programs that watch the LAN.
pub fn default_route_interface() -> Option<(String, String, u8)> {
    let networks = Networks::new_with_refreshed_list();
    let addr = outbound_address();
    let iface = default_interface(&networks, addr.as_deref())?;
    let data = networks.iter().find(|(n, _)| **n == iface).map(|(_, d)| d)?;
    let net = data.ip_networks().iter().find(|ip| ip.addr.is_ipv4())?;
    Some((iface, net.addr.to_string(), net.prefix))
}

pub fn is_wifi(iface: &str) -> bool {
    if cfg!(windows) {
        let lowered = iface.to_lowercase();
        return ["wi-fi", "wifi", "wireless", "wlan"].iter().any(|w| lowered.contains(w));
    }
    Path::new("/sys/class/net").join(iface).join("wireless").exists() || iface.starts_with("wl")
}

/// SSID of the network `iface` is connected to ("" when unknown).
pub fn wifi_name(iface: &str) -> String {
    #[cfg(windows)]
    {
        let _ = iface;
        let (_, out) = util::run_command("netsh", &["wlan", "show", "interfaces"], Duration::from_secs(5));
        for line in out.lines() {
            if let Some((key, value)) = line.split_once(':') {
                if key.trim() == "SSID" {
                    return value.trim().to_string();
                }
            }
        }
        String::new()
    }
    #[cfg(target_os = "linux")]
    {
        linux_ssid(iface)
    }
    #[cfg(not(any(windows, target_os = "linux")))]
    {
        let _ = iface;
        String::new()
    }
}

/// Wireless extensions ioctl SIOCGIWESSID, the same call `iwgetid` makes.
#[cfg(target_os = "linux")]
fn linux_ssid(iface: &str) -> String {
    const SIOCGIWESSID: libc::c_ulong = 0x8B1B;
    const IW_ESSID_MAX_SIZE: usize = 32;
    #[repr(C)]
    struct IwReq {
        name: [u8; 16],
        pointer: *mut libc::c_void,
        length: u16,
        flags: u16,
        pad: [u8; 8],
    }
    let mut buffer = [0u8; IW_ESSID_MAX_SIZE + 1];
    let mut request = IwReq { name: [0; 16], pointer: buffer.as_mut_ptr().cast(), length: IW_ESSID_MAX_SIZE as u16, flags: 0, pad: [0; 8] };
    for (slot, byte) in request.name.iter_mut().zip(iface.bytes().take(15)) {
        *slot = byte;
    }
    unsafe {
        let fd = libc::socket(libc::AF_INET, libc::SOCK_DGRAM, 0);
        if fd < 0 {
            return String::new();
        }
        let result = libc::ioctl(fd, SIOCGIWESSID, &mut request as *mut IwReq);
        libc::close(fd);
        if result < 0 {
            return String::new();
        }
    }
    let len = (request.length as usize).min(IW_ESSID_MAX_SIZE);
    String::from_utf8_lossy(&buffer[..len]).trim_end_matches('\0').to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn readings_have_the_documented_shape() {
        let info = SysInfo::new(false);
        let cpu = info.cpu();
        assert!(cpu["count"].as_i64().unwrap() >= 1);
        assert!(cpu["cores"].is_array());
        let mem = info.memory();
        assert!(mem["total"].as_u64().unwrap() > 0 && mem["used"].as_u64().unwrap() <= mem["total"].as_u64().unwrap());
        assert!(info.disk(None)["total"].as_u64().unwrap() > 0);
        assert!(info.uptime() > 0.0);
        assert!(info.top(3).as_array().unwrap().len() <= 3);
        assert!(info.net().get("down").is_some());
        let _ = info.disks();
        let _ = info.battery();
        let _ = info.temps();
    }
}

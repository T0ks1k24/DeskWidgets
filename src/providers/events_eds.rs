//! Up next on Linux: the selected GNOME calendars (Evolution Data Server, so Google calendars too).
//!
//! Evolution Data Server owns the calendars (and syncs the online ones); its D-Bus API hands out the
//! events of a time range as iCalendar text, which is parsed and expanded here (recurrences included).
//! The calendars are re-read every 30 seconds.
use std::collections::{HashMap, HashSet};
use std::time::Duration;

use chrono::{DateTime, Local, NaiveDate, NaiveDateTime, TimeZone, Utc};
use serde_json::{json, Value};
use zbus::blocking::Connection;
use zbus::zvariant::{OwnedObjectPath, OwnedValue, Value as ZValue};

use super::{Poller, Provider, Snapshot};
use crate::core::util::{clean, log};

const DAYS: i64 = 8;
const MAX: usize = 12;
const SOURCES_PREFIX: &str = "org.gnome.evolution.dataserver.Sources";
const CALENDAR_PREFIX: &str = "org.gnome.evolution.dataserver.Calendar";
const SOURCE_MANAGER: &str = "/org/gnome/evolution/dataserver/SourceManager";
const FACTORY: &str = "/org/gnome/evolution/dataserver/CalendarFactory";

/// Is Evolution Data Server installed (running or activatable on the session bus)?
pub fn available() -> bool {
    let Ok(conn) = Connection::session() else { return false };
    bus_name(&conn, SOURCES_PREFIX).is_some()
}

fn bus_name(conn: &Connection, prefix: &str) -> Option<String> {
    let mut names: Vec<String> = Vec::new();
    for method in ["ListNames", "ListActivatableNames"] {
        if let Ok(reply) = conn.call_method(Some("org.freedesktop.DBus"), "/org/freedesktop/DBus", Some("org.freedesktop.DBus"), method, &()) {
            if let Ok(list) = reply.body().deserialize::<Vec<String>>() {
                names.extend(list);
            }
        }
    }
    names.retain(|n| n.starts_with(prefix) && n[prefix.len()..].chars().all(|c| c.is_ascii_digit()));
    names.sort();
    names.pop()
}

// ---------------------------------------------------------------------------------------------
// sources
#[derive(Debug, Clone, PartialEq)]
pub struct CalendarSource {
    pub uid: String,
    pub name: String,
    pub color: String,
}

/// The calendar a source's key file describes, if it is an enabled, selected one.
pub fn parse_source(uid: &str, data: &str) -> Option<CalendarSource> {
    let mut section = String::new();
    let (mut name, mut enabled, mut selected, mut color, mut is_calendar) = (String::new(), true, false, String::new(), false);
    for line in data.lines() {
        let line = line.trim();
        if line.starts_with('#') || line.is_empty() {
            continue;
        }
        if let Some(s) = line.strip_prefix('[').and_then(|l| l.strip_suffix(']')) {
            section = s.to_string();
            is_calendar |= section == "Calendar";
            continue;
        }
        let Some((key, value)) = line.split_once('=') else { continue };
        match (section.as_str(), key.trim()) {
            ("Data Source", "DisplayName") => name = value.trim().to_string(),
            ("Data Source", "Enabled") => enabled = value.trim() != "false",
            ("Calendar", "Selected") => selected = value.trim() == "true",
            ("Calendar", "Color") => color = value.trim().to_string(),
            _ => {}
        }
    }
    (is_calendar && enabled && selected).then(|| CalendarSource { uid: uid.to_string(), name: clean(&name), color })
}

fn list_sources(conn: &Connection, bus: &str) -> Result<Vec<CalendarSource>, String> {
    let reply = conn
        .call_method(Some(bus), SOURCE_MANAGER, Some("org.freedesktop.DBus.ObjectManager"), "GetManagedObjects", &())
        .map_err(|e| e.to_string())?;
    let objects: HashMap<OwnedObjectPath, HashMap<String, HashMap<String, OwnedValue>>> = reply.body().deserialize().map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    for interfaces in objects.values() {
        let Some(source) = interfaces.get("org.gnome.evolution.dataserver.Source") else { continue };
        let (Some(ZValue::Str(uid)), Some(ZValue::Str(data))) = (source.get("UID").map(|v| &**v), source.get("Data").map(|v| &**v)) else { continue };
        if let Some(found) = parse_source(uid, data) {
            out.push(found);
        }
    }
    out.sort_by(|a, b| a.uid.cmp(&b.uid));
    Ok(out)
}

// ---------------------------------------------------------------------------------------------
// iCalendar
#[derive(Debug, Clone, PartialEq)]
struct Prop {
    name: String,
    params: HashMap<String, String>,
    value: String,
}

/// The properties of every VEVENT in `ical` (nested alarms are left out).
fn parse_events(ical: &str) -> Vec<Vec<Prop>> {
    let mut lines: Vec<String> = Vec::new();
    for raw in ical.split('\n') {
        let raw = raw.trim_end_matches('\r');
        if (raw.starts_with(' ') || raw.starts_with('\t')) && !lines.is_empty() {
            lines.last_mut().unwrap().push_str(&raw[1..]);
        } else {
            lines.push(raw.to_string());
        }
    }
    let (mut events, mut current, mut depth_in_event) = (Vec::new(), None::<Vec<Prop>>, 0);
    for line in lines {
        let upper = line.to_uppercase();
        if upper == "BEGIN:VEVENT" {
            current = Some(Vec::new());
            depth_in_event = 0;
        } else if upper == "END:VEVENT" {
            if let Some(props) = current.take() {
                events.push(props);
            }
        } else if let Some(props) = current.as_mut() {
            if upper.starts_with("BEGIN:") {
                depth_in_event += 1;
            } else if upper.starts_with("END:") {
                depth_in_event -= 1;
            } else if depth_in_event == 0 {
                if let Some((head, value)) = split_property(&line) {
                    let mut parts = head.split(';');
                    let name = parts.next().unwrap_or("").to_uppercase();
                    let params = parts
                        .filter_map(|p| p.split_once('='))
                        .map(|(k, v)| (k.to_uppercase(), v.trim_matches('"').to_string()))
                        .collect();
                    props.push(Prop { name, params, value: value.to_string() });
                }
            }
        }
    }
    events
}

/// "NAME;PARAM=x:value" at the first colon that is not inside a quoted parameter.
fn split_property(line: &str) -> Option<(&str, &str)> {
    let mut quoted = false;
    for (i, c) in line.char_indices() {
        match c {
            '"' => quoted = !quoted,
            ':' if !quoted => return Some((&line[..i], &line[i + 1..])),
            _ => {}
        }
    }
    None
}

fn unescape(text: &str) -> String {
    let mut out = String::new();
    let mut chars = text.chars();
    while let Some(c) = chars.next() {
        if c == '\\' {
            match chars.next() {
                Some('n') | Some('N') => out.push(' '),
                Some(other) => out.push(other),
                None => {}
            }
        } else {
            out.push(c);
        }
    }
    out
}

#[derive(Debug, Clone, PartialEq)]
enum When {
    Date(NaiveDate),
    Utc(NaiveDateTime),
    Zoned(NaiveDateTime, chrono_tz::Tz),
    Floating(NaiveDateTime),
}

/// A zone from a TZID: Olson names directly, Evolution's "/freeassociation.sourceforge.net/Europe/Kyiv" by its tail.
fn parse_tz(tzid: &str) -> Option<chrono_tz::Tz> {
    let parts: Vec<&str> = tzid.trim_matches('/').split('/').collect();
    (0..parts.len()).find_map(|i| parts[i..].join("/").parse::<chrono_tz::Tz>().ok())
}

fn parse_when(prop: &Prop) -> Option<When> {
    let v = prop.value.trim();
    let date_only = prop.params.get("VALUE").map(|x| x == "DATE").unwrap_or(false) || v.len() == 8;
    if date_only {
        return NaiveDate::parse_from_str(&v[..8.min(v.len())], "%Y%m%d").ok().map(When::Date);
    }
    let utc = v.ends_with('Z');
    let naive = NaiveDateTime::parse_from_str(v.trim_end_matches('Z'), "%Y%m%dT%H%M%S").ok()?;
    if utc {
        Some(When::Utc(naive))
    } else if let Some(tz) = prop.params.get("TZID").and_then(|t| parse_tz(t)) {
        Some(When::Zoned(naive, tz))
    } else {
        Some(When::Floating(naive))
    }
}

fn local_epoch(naive: &NaiveDateTime) -> i64 {
    match Local.from_local_datetime(naive) {
        chrono::LocalResult::Single(t) => t.timestamp(),
        chrono::LocalResult::Ambiguous(t, _) => t.timestamp(),
        chrono::LocalResult::None => Utc.from_utc_datetime(naive).timestamp(),
    }
}

fn epoch(when: &When) -> i64 {
    match when {
        When::Date(d) => local_epoch(&d.and_hms_opt(0, 0, 0).unwrap()),
        When::Floating(n) => local_epoch(n),
        When::Utc(n) => Utc.from_utc_datetime(n).timestamp(),
        When::Zoned(n, tz) => match tz.from_local_datetime(n) {
            chrono::LocalResult::Single(t) => t.timestamp(),
            chrono::LocalResult::Ambiguous(t, _) => t.timestamp(),
            chrono::LocalResult::None => Utc.from_utc_datetime(n).timestamp(),
        },
    }
}

/// "PT1H30M", "P1D", "P2W" in seconds.
fn parse_duration(text: &str) -> Option<i64> {
    let t = text.trim();
    let (negative, t) = match t.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, t.strip_prefix('+').unwrap_or(t)),
    };
    let t = t.strip_prefix('P')?;
    let (mut total, mut number, mut in_time) = (0i64, String::new(), false);
    for c in t.chars() {
        match c {
            'T' => in_time = true,
            '0'..='9' => number.push(c),
            unit => {
                let n: i64 = number.parse().ok()?;
                number.clear();
                total += n * match (unit, in_time) {
                    ('W', _) => 7 * 86400,
                    ('D', _) => 86400,
                    ('H', true) => 3600,
                    ('M', true) => 60,
                    ('S', true) => 1,
                    _ => return None,
                };
            }
        }
    }
    Some(if negative { -total } else { total })
}

#[derive(Debug, Clone, PartialEq)]
pub struct Occurrence {
    pub start: i64,
    pub end: i64,
    pub allday: bool,
    pub title: String,
}

/// The occurrences of the events in `ical` that overlap [from, to) (epoch seconds).
pub fn occurrences(ical: &str, from: i64, to: i64) -> Vec<Occurrence> {
    let events = parse_events(ical);
    // detached instances of a recurring event replace the occurrence they are for
    let mut replaced: HashSet<(String, i64)> = HashSet::new();
    for props in &events {
        let uid = find(props, "UID").map(|p| p.value.clone()).unwrap_or_default();
        if let Some(when) = find(props, "RECURRENCE-ID").and_then(parse_when) {
            replaced.insert((uid, epoch(&when)));
        }
    }
    let mut out = Vec::new();
    for props in &events {
        if find(props, "STATUS").map(|p| p.value.eq_ignore_ascii_case("CANCELLED")).unwrap_or(false) {
            continue;
        }
        let Some(start_prop) = find(props, "DTSTART") else { continue };
        let Some(when) = parse_when(start_prop) else { continue };
        let allday = matches!(when, When::Date(_));
        let start = epoch(&when);
        let mut end = find(props, "DTEND").and_then(parse_when).map(|w| epoch(&w)).or_else(|| find(props, "DURATION").and_then(|p| parse_duration(&p.value)).map(|d| start + d)).unwrap_or(start);
        if end <= start {
            end = start + if allday { 86400 } else { 1800 };
        }
        let title = clean(&unescape(&find(props, "SUMMARY").map(|p| p.value.clone()).unwrap_or_default()));
        let uid = find(props, "UID").map(|p| p.value.clone()).unwrap_or_default();
        let length = end - start;
        match find(props, "RRULE") {
            None => {
                if end > from && start < to {
                    out.push(Occurrence { start, end, allday, title });
                }
            }
            Some(rule) => {
                for s in expand(props, &when, &rule.value, from - length, to) {
                    if !replaced.contains(&(uid.clone(), s)) {
                        // a day-long event in a zone other than ours keeps its length, not its end
                        out.push(Occurrence { start: s, end: s + length, allday, title: title.clone() });
                    }
                }
            }
        }
    }
    out
}

fn find<'a>(props: &'a [Prop], name: &str) -> Option<&'a Prop> {
    props.iter().find(|p| p.name == name)
}

/// Start times (epoch) of a recurring event inside [from, to).
fn expand(props: &[Prop], when: &When, rule: &str, from: i64, to: i64) -> Vec<i64> {
    // Floating times and dates are expanded as wall-clock time in UTC and read back as local time,
    // which keeps "every day at 9:00" at 9:00 across daylight saving changes.
    let stamp = |n: &NaiveDateTime| n.format("%Y%m%dT%H%M%S").to_string();
    let (dtstart, suffix, zone): (String, &str, Option<chrono_tz::Tz>) = match when {
        When::Date(d) => (format!("{}T000000", d.format("%Y%m%d")), "Z", None),
        When::Floating(n) => (stamp(n), "Z", None),
        When::Utc(n) => (stamp(n), "Z", None),
        When::Zoned(n, tz) => (stamp(n), "", Some(*tz)),
    };
    let wall_clock = matches!(when, When::Date(_) | When::Floating(_));
    let head = match zone {
        Some(tz) => format!("DTSTART;TZID={}:{}", tz.name(), dtstart),
        None => format!("DTSTART:{dtstart}{suffix}"),
    };
    let rule = fix_until(rule, zone.is_some());
    let mut text = format!("{head}\nRRULE:{rule}");
    for ex in props.iter().filter(|p| p.name == "EXDATE") {
        for value in ex.value.split(',') {
            let prop = Prop { name: "EXDATE".into(), params: ex.params.clone(), value: value.to_string() };
            let Some(w) = parse_when(&prop) else { continue };
            let line = match (&w, zone) {
                (When::Zoned(n, tz), _) => format!("EXDATE;TZID={}:{}", tz.name(), stamp(n)),
                (When::Utc(n), _) => format!("EXDATE:{}Z", stamp(n)),
                (When::Date(d), Some(tz)) => format!("EXDATE;TZID={}:{}T{}", tz.name(), d.format("%Y%m%d"), when_time(when)),
                (When::Date(d), None) => format!("EXDATE:{}T{}Z", d.format("%Y%m%d"), when_time(when)),
                (When::Floating(n), Some(tz)) => format!("EXDATE;TZID={}:{}", tz.name(), stamp(n)),
                (When::Floating(n), None) => format!("EXDATE:{}Z", stamp(n)),
            };
            text.push('\n');
            text.push_str(&line);
        }
    }
    let set: rrule::RRuleSet = match text.parse() {
        Ok(set) => set,
        Err(e) => {
            log(format!("calendar: cannot read the recurrence {rule:?}: {e}"));
            return Vec::new();
        }
    };
    let after = Utc.timestamp_opt(from, 0).single().map(|t| t.with_timezone(&rrule::Tz::UTC));
    let before = Utc.timestamp_opt(to, 0).single().map(|t| t.with_timezone(&rrule::Tz::UTC));
    let (Some(after), Some(before)) = (after, before) else { return Vec::new() };
    let result = set.after(after).before(before).all(500);
    result
        .dates
        .iter()
        .map(|d: &DateTime<rrule::Tz>| if wall_clock { local_epoch(&d.naive_utc()) } else { d.timestamp() })
        .collect()
}

fn when_time(when: &When) -> String {
    match when {
        When::Date(_) => "000000".into(),
        When::Utc(n) | When::Floating(n) | When::Zoned(n, _) => n.format("%H%M%S").to_string(),
    }
}

/// UNTIL given as a date is widened to the end of that day, as a date-time the expander understands.
fn fix_until(rule: &str, zoned: bool) -> String {
    rule.split(';')
        .map(|part| match part.strip_prefix("UNTIL=") {
            Some(v) if !v.contains('T') => format!("UNTIL={v}T235959Z"),
            Some(v) if !v.ends_with('Z') && !zoned => format!("UNTIL={v}Z"),
            _ => part.to_string(),
        })
        .collect::<Vec<_>>()
        .join(";")
}

// ---------------------------------------------------------------------------------------------
pub struct Events {
    snapshot: Snapshot,
    poller: Option<Poller>,
}

impl Events {
    pub fn new() -> Events {
        Events { snapshot: Snapshot::default(), poller: None }
    }
}

struct Opened {
    path: String,
    bus: String,
}

fn open_calendar(conn: &Connection, factory_bus: &str, uid: &str) -> Result<Opened, String> {
    let reply = conn
        .call_method(Some(factory_bus), FACTORY, Some("org.gnome.evolution.dataserver.CalendarFactory"), "OpenCalendar", &(uid,))
        .map_err(|e| e.to_string())?;
    let (path, bus): (String, String) = reply.body().deserialize().map_err(|e| e.to_string())?;
    let _ = conn.call_method(Some(bus.as_str()), path.as_str(), Some("org.gnome.evolution.dataserver.Calendar"), "Open", &());
    Ok(Opened { path, bus })
}

fn make_time(epoch: i64) -> String {
    Utc.timestamp_opt(epoch, 0).single().unwrap_or_default().format("%Y%m%dT%H%M%SZ").to_string()
}

fn refresh(conn: &Connection, opened: &mut HashMap<String, Opened>, failed: &mut HashSet<String>) -> Result<Value, String> {
    let sources_bus = bus_name(conn, SOURCES_PREFIX).ok_or("Evolution Data Server is not available")?;
    let factory_bus = bus_name(conn, CALENDAR_PREFIX).ok_or("the Evolution calendar service is not available")?;
    let sources = list_sources(conn, &sources_bus)?;
    opened.retain(|uid, _| sources.iter().any(|s| &s.uid == uid));
    let now = Local::now();
    let midnight = local_epoch(&now.date_naive().and_hms_opt(0, 0, 0).unwrap());
    let end = midnight + DAYS * 86400;
    let sexp = format!("(occur-in-time-range? (make-time \"{}\") (make-time \"{}\"))", make_time(midnight), make_time(end));
    let mut found: Vec<(i64, i64, bool, String, String, String)> = Vec::new();
    for source in &sources {
        if !opened.contains_key(&source.uid) {
            match open_calendar(conn, &factory_bus, &source.uid) {
                Ok(o) => {
                    failed.remove(&source.uid);
                    opened.insert(source.uid.clone(), o);
                }
                Err(e) => {
                    if failed.insert(source.uid.clone()) {
                        log(format!("calendar {}: {e}", source.name));
                    }
                    continue;
                }
            }
        }
        let o = &opened[&source.uid];
        let reply = conn.call_method(Some(o.bus.as_str()), o.path.as_str(), Some("org.gnome.evolution.dataserver.Calendar"), "GetObjectList", &(sexp.as_str(),));
        let components: Vec<String> = match reply.map_err(|e| e.to_string()).and_then(|m| m.body().deserialize().map_err(|e| e.to_string())) {
            Ok(list) => list,
            Err(e) => {
                opened.remove(&source.uid); // reopen next time: the backend may have restarted
                if failed.insert(source.uid.clone()) {
                    log(format!("calendar {}: {e}", source.name));
                }
                continue;
            }
        };
        for component in components {
            for o in occurrences(&component, midnight, end) {
                found.push((o.start, o.end, o.allday, source.color.clone(), source.name.clone(), o.title));
            }
        }
    }
    let now_ts = now.timestamp();
    found.retain(|e| e.1 > now_ts);
    found.sort_by(|a, b| (a.0, !a.2, &a.5).cmp(&(b.0, !b.2, &b.5)));
    found.truncate(MAX);
    Ok(json!({
        "updated": now_ts,
        "events": found.into_iter().map(|(s, e, allday, color, calendar, title)| json!({
            "start": s, "end": e, "allday": allday, "color": color, "calendar": calendar, "title": title,
        })).collect::<Vec<_>>(),
    }))
}

impl Provider for Events {
    fn start(&mut self) -> Result<(), String> {
        let conn = Connection::session().map_err(|e| e.to_string())?;
        if bus_name(&conn, SOURCES_PREFIX).is_none() {
            return Err("Evolution Data Server is not installed".into());
        }
        let snapshot = self.snapshot.clone();
        let (mut opened, mut failed) = (HashMap::new(), HashSet::new());
        self.poller = Some(Poller::spawn("events", Duration::from_secs(30), move |_| {
            snapshot.publish(refresh(&conn, &mut opened, &mut failed)?);
            Ok(())
        }));
        Ok(())
    }

    fn stop(&mut self) {
        if let Some(p) = self.poller.as_mut() {
            p.stop();
        }
    }

    fn snapshot(&self) -> Option<Value> {
        self.snapshot.get()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ts(s: &str) -> i64 {
        Utc.from_utc_datetime(&NaiveDateTime::parse_from_str(s, "%Y%m%dT%H%M%S").unwrap()).timestamp()
    }

    #[test]
    fn selected_calendar_sources_only() {
        let data = "[Data Source]\nDisplayName[de]=Persönlich\nDisplayName=Personal\nEnabled=true\n\n[Calendar]\nColor=#62a0ea\nSelected=true\nBackendName=local\n";
        assert_eq!(parse_source("u1", data), Some(CalendarSource { uid: "u1".into(), name: "Personal".into(), color: "#62a0ea".into() }));
        assert_eq!(parse_source("u2", &data.replace("Selected=true", "Selected=false")), None);
        assert_eq!(parse_source("u3", &data.replace("Enabled=true", "Enabled=false")), None);
        assert_eq!(parse_source("u4", "[Data Source]\nDisplayName=Mail\n[Mail Account]\n"), None);
    }

    #[test]
    fn a_google_all_day_event_and_a_timed_one() {
        let ics = "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261109\r\nDTEND;VALUE=DATE:20261110\r\nUID:a\r\nDESCRIPTION:long\r\n description\r\nSUMMARY:Cultural Workers\\, Folk Artists Day\r\nBEGIN:VALARM\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\nEND:VEVENT\r\n";
        let from = ts("20261101T000000");
        let found = occurrences(ics, from, from + 8 * 86400);
        assert_eq!(found.len(), 1);
        assert!(found[0].allday);
        assert_eq!(found[0].title, "Cultural Workers, Folk Artists Day");
        assert_eq!(found[0].end - found[0].start, 86400);
        let timed = "BEGIN:VEVENT\nDTSTART:20261102T100000Z\nDURATION:PT90M\nSUMMARY:Standup\nUID:b\nEND:VEVENT\n";
        let found = occurrences(timed, from, from + 8 * 86400);
        assert_eq!((found[0].start, found[0].end - found[0].start, found[0].allday), (ts("20261102T100000"), 5400, false));
        assert!(occurrences(timed, from + 5 * 86400, from + 8 * 86400).is_empty());
    }

    #[test]
    fn evolution_style_time_zone_ids() {
        assert_eq!(parse_tz("/freeassociation.sourceforge.net/Europe/Kyiv"), Some(chrono_tz::Europe::Kyiv));
        assert_eq!(parse_tz("America/New_York"), Some(chrono_tz::America::New_York));
        assert_eq!(parse_tz("Mars/Olympus"), None);
        let ics = "BEGIN:VEVENT\nDTSTART;TZID=Europe/Kyiv:20261102T100000\nDTEND;TZID=Europe/Kyiv:20261102T110000\nSUMMARY:Call\nUID:c\nEND:VEVENT\n";
        let found = occurrences(ics, ts("20261101T000000"), ts("20261109T000000"));
        assert_eq!(found[0].start, ts("20261102T080000")); // Kyiv is UTC+2 in November
    }

    #[test]
    fn weekly_recurrence_with_an_exception_and_a_detached_instance() {
        let ics = "BEGIN:VEVENT\nDTSTART:20261102T090000Z\nDTEND:20261102T093000Z\nRRULE:FREQ=DAILY;COUNT=5\nEXDATE:20261103T090000Z\nSUMMARY:Daily\nUID:r\nEND:VEVENT\n\
BEGIN:VEVENT\nDTSTART:20261105T150000Z\nDTEND:20261105T153000Z\nRECURRENCE-ID:20261105T090000Z\nSUMMARY:Daily (moved)\nUID:r\nEND:VEVENT\n";
        let found = occurrences(ics, ts("20261101T000000"), ts("20261110T000000"));
        let mut starts: Vec<(i64, &str)> = found.iter().map(|o| (o.start, o.title.as_str())).collect();
        starts.sort();
        assert_eq!(
            starts,
            vec![
                (ts("20261102T090000"), "Daily"),
                (ts("20261104T090000"), "Daily"),
                (ts("20261105T150000"), "Daily (moved)"),
                (ts("20261106T090000"), "Daily"),
            ]
        );
    }

    #[test]
    fn durations() {
        assert_eq!(parse_duration("PT1H30M"), Some(5400));
        assert_eq!(parse_duration("P1D"), Some(86400));
        assert_eq!(parse_duration("P2W"), Some(14 * 86400));
        assert_eq!(parse_duration("garbage"), None);
    }
}

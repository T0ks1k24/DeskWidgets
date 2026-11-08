//! Docker containers for the Docker widget: one bounded `docker ps` shared by every copy of it.
use std::time::Duration;

use serde_json::{json, Value};

use super::{Poller, Provider, Snapshot};
use crate::core::util::run_command;

const FORMAT: &str = "{{.Names}}|{{.Image}}|{{.State}}|{{.Status}}";

pub struct Docker {
    snapshot: Snapshot,
    poller: Option<Poller>,
}

impl Docker {
    pub fn new() -> Docker {
        Docker { snapshot: Snapshot::default(), poller: None }
    }
}

/// {available, containers: [{name, image, state, status}]} from the output of `docker ps`.
pub fn parse(output: &str) -> Value {
    let containers: Vec<Value> = output
        .lines()
        .filter_map(|line| {
            let f: Vec<&str> = line.split('|').collect();
            (f.len() == 4).then(|| json!({"name": f[0], "image": f[1], "state": f[2], "status": f[3]}))
        })
        .collect();
    json!({"available": true, "containers": containers})
}

impl Provider for Docker {
    fn start(&mut self) -> Result<(), String> {
        let snapshot = self.snapshot.clone();
        self.poller = Some(Poller::spawn("docker", Duration::from_secs(3), move |_| {
            let (code, output) = run_command("docker", &["ps", "-a", "--format", FORMAT], Duration::from_secs(5));
            snapshot.publish(if code == Some(0) { parse(&output) } else { json!({"available": false, "containers": []}) });
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

    #[test]
    fn parses_docker_ps_lines() {
        let v = parse("web|nginx:1|running|Up 2 hours\nbroken line\ndb|postgres|exited|Exited (0) 3 days ago\n");
        let list = v["containers"].as_array().unwrap();
        assert_eq!(list.len(), 2);
        assert_eq!(list[0]["name"], "web");
        assert_eq!(list[1]["state"], "exited");
        assert_eq!(v["available"], true);
    }
}

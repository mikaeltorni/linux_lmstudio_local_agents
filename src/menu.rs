//! Interactive model-selection menu.
//!
//! Command parsing and the selection payload are separate from the process entry.

use crate::{autostarts_by_default, custom_profile_from_source, normalize_custom_profile};
use serde_json::{json, Value};
use std::io::{BufRead, Write};

struct Row {
    id: String,
    label: String,
    install: bool,
    autostart: bool,
    custom: bool,
    switch_env_var: String,
    profile: Option<Value>,
    group: i64,
}

/// Run the menu over catalog profiles and scripted command lines.
///
/// Parameters: profiles - registry profiles; lines - commands, ending at EOF or a blank line.
///
/// Returns: the selection object written for apply-selection.
pub fn run_menu<I, W>(profiles: &[Value], lines: I, mut writer: W) -> Value
where
    I: IntoIterator<Item = String>,
    W: Write,
{
    let mut rows = seed(profiles);
    let mut incoming = lines.into_iter();
    loop {
        let _ = write!(writer, "{}", render(&rows));
        let Some(command) = incoming.next() else { break };
        let low = command.trim().to_ascii_lowercase();
        if matches!(low.as_str(), "" | "done" | "q" | "ok") {
            break;
        }
        if matches!(low.as_str(), "a" | "add") {
            let _ = write!(writer, "\n  Add a model (blank cancels).\n");
            let Some(source) = incoming.next() else { break };
            let source = source.trim();
            if source.is_empty() {
                continue;
            }
            match custom_profile_from_source(source) {
                Ok(spec) => add_custom(&mut rows, spec),
                Err(message) => {
                    let _ = write!(writer, "\n  Could not add model: {message}\n");
                }
            }
            continue;
        }
        if low == "all" {
            for row in &mut rows {
                row.install = true;
            }
            continue;
        }
        if low == "none" {
            for row in &mut rows {
                row.install = false;
                row.autostart = false;
            }
            continue;
        }
        if let Some(index) = prefixed_index(&low, &["b", "s"]) {
            toggle_autostart(&mut rows, index);
            continue;
        }
        if let Some(index) = prefixed_index(&low, &["e"]) {
            let _ = write!(writer, "    Environment variable (blank disables switching): ");
            let Some(value) = incoming.next() else { break };
            if let Err(message) = set_environment(&mut rows, index, value.trim()) {
                let _ = write!(writer, "\n  Could not set environment variable: {message}\n");
            }
            continue;
        }
        if let Some(index) = prefixed_index(&low, &["g"]) {
            let _ = write!(writer, "    Group number (0 = none): ");
            let Some(value) = incoming.next() else { break };
            if value.trim().chars().all(|ch| ch.is_ascii_digit()) && !value.trim().is_empty() {
                if let Ok(group) = value.trim().parse::<i64>() {
                    if let Some(row) = rows.get_mut(index.saturating_sub(1)) {
                        row.group = group;
                        if let Some(profile) = row.profile.as_mut() {
                            profile["group"] = json!(group);
                        }
                    }
                }
            } else {
                let _ = write!(writer, "\n  Group must be a non-negative number.\n");
            }
            continue;
        }
        if low.chars().all(|ch| ch.is_ascii_digit()) && !low.is_empty() {
            if let Ok(index) = low.parse::<usize>() {
                toggle_install(&mut rows, index);
            }
            continue;
        }
        let _ = write!(writer, "\n  Unrecognized command: {command:?}\n");
    }
    selection(&rows)
}

/// Read menu commands from a buffered reader.
///
/// Parameters: profiles - registry profiles; reader - command source; writer - prompt destination.
///
/// Returns: the selection object.
pub fn run_menu_reader<R, W>(profiles: &[Value], reader: R, writer: W) -> Value
where
    R: BufRead,
    W: Write,
{
    let lines = reader.lines().map_while(Result::ok);
    run_menu(profiles, lines, writer)
}

fn seed(profiles: &[Value]) -> Vec<Row> {
    profiles
        .iter()
        .filter_map(|profile| {
            let id = profile.get("id")?.as_str()?.to_string();
            let install = profile.get("default_install").and_then(Value::as_bool).unwrap_or(false);
            Some(Row {
                label: profile.get("description").and_then(Value::as_str).unwrap_or(&id).to_string(),
                autostart: install && autostarts_by_default(profile),
                custom: profile.get("custom").and_then(Value::as_bool).unwrap_or(false),
                switch_env_var: profile.get("switch_env_var").and_then(Value::as_str).unwrap_or("").to_string(),
                profile: if profile.get("custom").and_then(Value::as_bool).unwrap_or(false) { Some(profile.clone()) } else { None },
                group: profile.get("group").and_then(Value::as_i64).unwrap_or(0),
                id,
                install,
            })
        })
        .collect()
}

fn render(rows: &[Row]) -> String {
    let mut lines = vec![
        String::new(),
        "  LM Studio models — choose what to install and autostart:".to_string(),
        String::new(),
    ];
    for (index, row) in rows.iter().enumerate() {
        let mark = if row.install { "x" } else { " " };
        let auto = if row.autostart { "boot" } else { "    " };
        let tag = if row.custom { " (custom)" } else { "" };
        let switch = if row.switch_env_var.is_empty() { String::new() } else { format!("  [env: {}]", row.switch_env_var) };
        let group = if row.group == 0 { String::new() } else { format!("  [group: {}]", row.group) };
        lines.push(format!("   {:>2}. [{mark}] [{auto}] {}{tag}{switch}{group}", index + 1, row.label));
    }
    lines.push(String::new());
    lines.push("  Commands: <n> toggle install · s<n> toggle startup · A add another URL/spec".to_string());
    lines.push("            e<n> edit env variable · g<n> set exclusivity group (0 = none)".to_string());
    lines.push("            all / none · enter to install the selection".to_string());
    lines.push(String::new());
    lines.join("\n")
}

fn toggle_install(rows: &mut [Row], index: usize) {
    if !(1..=rows.len()).contains(&index) {
        return;
    }
    let row = &mut rows[index - 1];
    row.install = !row.install;
    if !row.install {
        row.autostart = false;
    }
}

fn toggle_autostart(rows: &mut [Row], index: usize) {
    if !(1..=rows.len()).contains(&index) {
        return;
    }
    let row = &mut rows[index - 1];
    row.autostart = !row.autostart;
    if row.autostart {
        row.install = true;
    }
}

fn set_environment(rows: &mut [Row], index: usize, value: &str) -> Result<(), String> {
    if !(1..=rows.len()).contains(&index) {
        return Ok(());
    }
    let row = &mut rows[index - 1];
    let mut spec = row.profile.clone().unwrap_or_else(|| json!({"name": row.id, "model_key": row.id}));
    spec["switch_env_var"] = json!(value);
    let candidate = normalize_custom_profile(&spec)?;
    let variable = candidate.get("switch_env_var").and_then(Value::as_str).unwrap_or("").to_string();
    row.switch_env_var = variable.clone();
    if let Some(profile) = row.profile.as_mut() {
        profile["switch_env_var"] = json!(variable);
    }
    Ok(())
}

fn add_custom(rows: &mut Vec<Row>, spec: Value) {
    let profile = if spec.get("custom").and_then(Value::as_bool).unwrap_or(false) {
        spec
    } else {
        match normalize_custom_profile(&spec) {
            Ok(profile) => profile,
            Err(_) => return,
        }
    };
    let Some(id) = profile.get("id").and_then(Value::as_str).map(str::to_string) else { return };
    rows.retain(|row| row.id != id);
    rows.push(Row {
        label: profile.get("description").and_then(Value::as_str).unwrap_or(&id).to_string(),
        install: true,
        autostart: true,
        custom: true,
        switch_env_var: profile.get("switch_env_var").and_then(Value::as_str).unwrap_or("").to_string(),
        group: profile.get("group").and_then(Value::as_i64).unwrap_or(0),
        id,
        profile: Some(profile),
    });
}

fn prefixed_index(command: &str, prefixes: &[&str]) -> Option<usize> {
    let rest = command.get(1..)?.trim();
    if prefixes.iter().any(|prefix| command.starts_with(prefix)) && rest.chars().all(|ch| ch.is_ascii_digit()) && !rest.is_empty() {
        rest.parse().ok()
    } else {
        None
    }
}

fn selection(rows: &[Row]) -> Value {
    let selected: Vec<&str> = rows.iter().filter(|row| row.install).map(|row| row.id.as_str()).collect();
    let autostart: Vec<&str> = rows.iter().filter(|row| row.install && row.autostart).map(|row| row.id.as_str()).collect();
    let custom: Vec<&Value> = rows.iter().filter_map(|row| if row.custom { row.profile.as_ref() } else { None }).collect();
    json!({"selected_ids": selected, "autostart_ids": autostart, "custom_profiles": custom})
}

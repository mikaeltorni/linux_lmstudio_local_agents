//! Catalog snippets the installer and boot script used to inline in Python.

use serde_json::Value;
use std::env;
use std::fs;
use std::io::{self, Read};
use std::path::Path;

fn main() {
    let code = match run(env::args().skip(1).collect()) {
        Ok(text) => {
            print!("{text}");
            0
        }
        Err(message) => {
            eprintln!("{message}");
            1
        }
    };
    std::process::exit(code);
}

fn run(args: Vec<String>) -> Result<String, String> {
    let command = args.first().ok_or_else(|| "usage: lmstudio-snippets COMMAND".to_string())?;
    match command.as_str() {
        "selection-from-ids" => selection_from_ids(&args[1], &args[2]),
        "additive-selection" => additive_selection(&args[1], &args[2], &args[3], args.get(4).map(String::as_str).unwrap_or("")),
        "switch-commands" => switch_commands(&args[1], false),
        "stale-commands" => switch_commands(&args[1], true),
        "resolve-load-key" => resolve_load_key(&args[1], args.get(2).map(String::as_str).unwrap_or("")),
        other => Err(format!("unknown command: {other}")),
    }
}

fn selection_from_ids(catalog: &str, ids: &str) -> Result<String, String> {
    let registry = read_json(catalog)?;
    let wanted = split_ids(ids);
    let profiles = profiles(&registry);
    let selected: Vec<String> = profiles.iter().filter_map(|profile| id(profile)).filter(|pid| wanted.iter().any(|item| item == pid)).collect();
    let autostart: Vec<String> = profiles.iter().filter_map(|profile| id(profile)).filter(|pid| wanted.iter().any(|item| item == pid) && autostarts(profile_by_id(&profiles, pid))).collect();
    Ok(format!(
        "{{\"selected_ids\": {}, \"autostart_ids\": {}}}\n",
        json_strings(&selected),
        json_strings(&autostart)
    ))
}

fn additive_selection(base_path: &str, existing_path: &str, ids: &str, requested_main: &str) -> Result<String, String> {
    let base = read_json(base_path)?;
    let existing = if Path::new(existing_path).exists() { read_json(existing_path)? } else { base.clone() };
    let wanted = split_ids(ids);
    let mut selected = Vec::new();
    let mut autostart = Vec::new();
    let mut main_id: Option<String> = None;
    for profile in profiles(&existing) {
        let Some(pid) = id(&profile) else { continue };
        if truthy(profile.get("default_install")) || wanted.iter().any(|item| item == &pid) {
            selected.push(pid.clone());
        }
        if autostarts(&profile) {
            autostart.push(pid.clone());
        }
        if truthy(profile.get("default_main")) {
            main_id = Some(pid);
        }
    }
    for profile in profiles(&base) {
        let Some(pid) = id(&profile) else { continue };
        if wanted.iter().any(|item| item == &pid) && !selected.iter().any(|item| item == &pid) {
            selected.push(pid.clone());
        }
        if wanted.iter().any(|item| item == &pid) && autostarts(&profile) && !autostart.iter().any(|item| item == &pid) {
            autostart.push(pid);
        }
    }
    for profile in profiles(&base) {
        let Some(pid) = id(&profile) else { continue };
        if selected.iter().any(|item| item == &pid) && truthy(profile.get("default_main")) {
            main_id = Some(pid);
            break;
        }
    }
    if !requested_main.is_empty() && selected.iter().any(|item| item == requested_main) {
        main_id = Some(requested_main.to_string());
    }
    let main = match main_id {
        Some(value) => format!("\"{value}\""),
        None => "null".to_string(),
    };
    Ok(format!(
        "{{\"selected_ids\": {}, \"autostart_ids\": {}, \"main_id\": {main}}}\n",
        json_strings(&selected),
        json_strings(&autostart)
    ))
}

fn switch_commands(catalog: &str, stale: bool) -> Result<String, String> {
    let registry = read_json(catalog)?;
    let mut lines = Vec::new();
    for profile in profiles(&registry) {
        let Some(command) = profile.get("command").and_then(Value::as_str).filter(|value| !value.is_empty()) else { continue };
        let installed = truthy(profile.get("default_install"));
        let keep_on = truthy(profile.get("default_keep_on"));
        let emit = if stale { !installed || keep_on } else { installed && !keep_on };
        if emit {
            lines.push(format!("{command}\t{}", id(&profile).unwrap_or_default()));
        }
    }
    if lines.is_empty() { Ok(String::new()) } else { Ok(format!("{}\n", lines.join("\n"))) }
}

fn resolve_load_key(desired: &str, model_path: &str) -> Result<String, String> {
    let mut raw = String::new();
    io::stdin().read_to_string(&mut raw).map_err(|err| err.to_string())?;
    let items: Vec<Value> = serde_json::from_str(&raw).unwrap_or_default();
    for item in &items {
        if item.get("modelKey").and_then(Value::as_str) == Some(desired) {
            return Ok(format!("{desired}\n"));
        }
    }
    if !model_path.is_empty() {
        for item in &items {
            let path = item.get("path").and_then(Value::as_str);
            let indexed = item.get("indexedModelIdentifier").and_then(Value::as_str);
            if path == Some(model_path) || indexed == Some(model_path) {
                if let Some(key) = item.get("modelKey").and_then(Value::as_str) {
                    return Ok(format!("{key}\n"));
                }
            }
        }
    }
    Ok(String::new())
}

fn read_json(path: &str) -> Result<Value, String> {
    let text = fs::read_to_string(path).map_err(|err| err.to_string())?;
    serde_json::from_str(&text).map_err(|err| err.to_string())
}

fn profiles(registry: &Value) -> Vec<Value> {
    registry.get("profiles").and_then(Value::as_array).cloned().unwrap_or_default()
}

fn id(profile: &Value) -> Option<String> {
    profile.get("id").and_then(Value::as_str).map(str::to_string)
}

fn profile_by_id<'a>(profiles: &'a [Value], pid: &str) -> &'a Value {
    profiles.iter().find(|profile| id(profile).as_deref() == Some(pid)).unwrap()
}

fn json_strings(values: &[String]) -> String {
    format!("[{}]", values.iter().map(|value| format!("\"{value}\"")).collect::<Vec<_>>().join(", "))
}

fn split_ids(raw: &str) -> Vec<String> {
    raw.replace(',', " ").split_whitespace().map(str::to_string).collect()
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Bool(flag)) => *flag,
        Some(Value::Number(number)) => number.as_i64().unwrap_or(0) != 0,
        Some(Value::String(text)) => !text.is_empty(),
        _ => false,
    }
}

fn autostarts(profile: &Value) -> bool {
    if profile.get("default_startup").is_some() {
        truthy(profile.get("default_startup"))
    } else {
        truthy(profile.get("default_keep_on")) || truthy(profile.get("default_main"))
    }
}

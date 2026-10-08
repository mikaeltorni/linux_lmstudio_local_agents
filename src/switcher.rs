//! Switch the active LM Studio model.
//!
//! Profile selection is separate from the `lms` process calls.

use serde_json::Value;
use std::fs;
use std::path::Path;
use std::process::Command;
use std::thread;
use std::time::Duration;

struct Outcome {
    code: i32,
    stdout: String,
    stderr: String,
}

/// Resolve a profile, ask `lms` to load it, and return the model key.
///
/// Parameters: profile - requested id, omitted when restoring; restore - read the state file;
/// catalog - registry path; state_file - persisted choice; lms - LM Studio CLI.
///
/// Returns: the model key printed by the command.
pub fn switch(profile: Option<&str>, restore: bool, catalog: &Path, state_file: &Path, lms: &Path) -> Result<String, String> {
    if restore && profile.is_some() {
        return Err("--restore cannot be combined with a profile".into());
    }
    if !restore && profile.is_none() {
        return Err("a profile or --restore is required".into());
    }
    let profiles = load_profiles(catalog)?;
    let selected = selected_profile(&profiles, if restore { None } else { profile }, state_file)?;
    switch_model(&selected, &profiles, lms)?;
    if let Some(parent) = state_file.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let id = text(&selected, "id");
    fs::write(state_file, format!("{id}\n")).map_err(|err| err.to_string())?;
    Ok(text(&selected, "model_key"))
}

fn load_profiles(path: &Path) -> Result<Vec<Value>, String> {
    let text = fs::read_to_string(path).map_err(|err| err.to_string())?;
    let value: Value = serde_json::from_str(&text).map_err(|err| err.to_string())?;
    Ok(value.get("profiles").and_then(Value::as_array).cloned().unwrap_or_default())
}

fn selected_profile(profiles: &[Value], requested: Option<&str>, state_file: &Path) -> Result<Value, String> {
    let mut selected = requested.unwrap_or("").to_string();
    if requested.is_none() && state_file.is_file() {
        selected = fs::read_to_string(state_file).unwrap_or_default().trim().to_string();
    }
    if requested.is_none() && selected.is_empty() {
        selected = profiles.iter().find(|profile| profile.get("boot_preload").and_then(Value::as_bool).unwrap_or(false)).and_then(|profile| profile.get("id").and_then(Value::as_str)).unwrap_or("").to_string();
        if selected.is_empty() {
            return Err("no model profile is selected and no boot default is configured".into());
        }
    }
    profiles.iter().find(|profile| {
        selected == text(profile, "id") || selected == optional(profile, "command") || selected == text(profile, "model_key")
    }).cloned().ok_or_else(|| format!("Unknown LM Studio model profile: {selected}"))
}

fn switch_model(profile: &Value, profiles: &[Value], lms: &Path) -> Result<(), String> {
    let _ = run_lms(lms, &["server", "start"]);
    let loaded = run_lms(lms, &["ps", "--json"]);
    let loaded_items = parse_list(&loaded.stdout);
    let candidates: Vec<&Value> = profiles.iter().filter(|item| !item.get("default_keep_on").and_then(Value::as_bool).unwrap_or(false)).collect();
    for candidate in &candidates {
        if text(candidate, "id") == text(profile, "id") {
            continue;
        }
        let mut keys = vec![text(candidate, "model_key"), identifier(candidate)];
        keys.push(resolve_load_key(candidate, lms));
        for key in keys {
            if !key.is_empty() {
                let _ = run_lms(lms, &["unload", &key]);
            }
        }
    }
    if loaded.code == 0 && loaded_items.iter().any(|item| loaded_matches(item, profile)) {
        return Ok(());
    }
    let result = load_model(profile, lms);
    if result.code != 0 {
        let message = if result.stderr.is_empty() { result.stdout } else { result.stderr };
        if !message.is_empty() {
            eprint!("{message}");
        }
        return Err(format!("Failed to load LM Studio model: {}", identifier(profile)));
    }
    Ok(())
}

fn load_model(profile: &Value, lms: &Path) -> Outcome {
    let load_key = resolve_load_key(profile, lms);
    let target = identifier(profile);
    let ps = run_lms(lms, &["ps", "--json"]);
    if ps.code == 0 {
        if let Ok(items) = serde_json::from_str::<Vec<Value>>(&ps.stdout) {
            if items.iter().any(|item| loaded_matches(item, profile)) {
                return Outcome { code: 0, stdout: String::new(), stderr: String::new() };
            }
        }
    }
    for unload_key in [target.clone(), text(profile, "model_key")] {
        let unload = run_lms(lms, &["unload", &unload_key]);
        if unload.code == 0 {
            for _ in 0..10 {
                let check = run_lms(lms, &["ps", "--json"]);
                if check.code != 0 {
                    break;
                }
                let still = serde_json::from_str::<Vec<Value>>(&check.stdout).unwrap_or_default().iter().any(|item| {
                    let id = item.get("identifier").and_then(Value::as_str).or_else(|| item.get("modelKey").and_then(Value::as_str)).unwrap_or("");
                    id == target || id == text(profile, "model_key")
                });
                if !still {
                    break;
                }
                thread::sleep(Duration::from_millis(500));
            }
        }
    }
    if uses_advanced(profile) {
        let args = sdk_args(profile, &load_key);
        let owned: Vec<&str> = args.iter().map(String::as_str).collect();
        return run_command(&owned);
    }
    let mut args = vec![
        "load".to_string(),
        "-c".to_string(),
        number(profile, "context_length"),
        "--gpu".to_string(),
        text(profile, "gpu"),
        "--parallel".to_string(),
        number(profile, "parallel"),
        "--identifier".to_string(),
        target,
    ];
    if profile.get("ttl").and_then(Value::as_i64).unwrap_or(0) != 0 {
        args.push("--ttl".to_string());
        args.push(number(profile, "ttl"));
    }
    if profile.get("speculative_draft_mtp").and_then(Value::as_bool).unwrap_or(false) {
        args.push("--speculative-draft-mtp".to_string());
    }
    args.push(load_key);
    args.push("-y".to_string());
    let borrowed: Vec<&str> = args.iter().map(String::as_str).collect();
    run_lms(lms, &borrowed)
}

fn resolve_load_key(profile: &Value, lms: &Path) -> String {
    let configured = if optional(profile, "load_key").is_empty() { text(profile, "model_key") } else { optional(profile, "load_key") };
    let listed = run_lms(lms, &["ls", "--json"]);
    if listed.code == 0 {
        if let Ok(items) = serde_json::from_str::<Vec<Value>>(&listed.stdout) {
            if let Some(item) = items.iter().find(|item| item.get("modelKey").and_then(Value::as_str) == Some(configured.as_str())) {
                return item.get("modelKey").and_then(Value::as_str).unwrap_or(&configured).to_string();
            }
            if let Some(item) = items.iter().find(|item| model_matches(item, profile)) {
                return item.get("modelKey").and_then(Value::as_str).unwrap_or(&configured).to_string();
            }
        }
    }
    configured
}

fn model_matches(item: &Value, profile: &Value) -> bool {
    let model_path = optional(profile, "model_path");
    let model_key = text(profile, "model_key");
    let values = [
        item.get("modelKey").and_then(Value::as_str).unwrap_or(""),
        item.get("path").and_then(Value::as_str).unwrap_or(""),
        item.get("indexedModelIdentifier").and_then(Value::as_str).unwrap_or(""),
    ];
    if values.contains(&model_key.as_str()) || (!model_path.is_empty() && values.contains(&model_path.as_str())) {
        return true;
    }
    if model_path.is_empty() {
        return false;
    }
    let name = Path::new(&model_path).file_name().and_then(|name| name.to_str()).unwrap_or("");
    values.iter().any(|value| !value.is_empty() && value.ends_with(name))
}

fn loaded_matches(item: &Value, profile: &Value) -> bool {
    let loaded = item.get("identifier").and_then(Value::as_str).unwrap_or("");
    if !loaded.is_empty() && loaded != identifier(profile) {
        return false;
    }
    let configured = optional(profile, "load_key");
    if !configured.is_empty() && item.get("modelKey").and_then(Value::as_str) == Some(configured.as_str()) {
        return true;
    }
    model_matches(item, profile)
}

fn uses_advanced(profile: &Value) -> bool {
    ["k_cache_quantization_type", "v_cache_quantization_type", "context_checkpoints", "eval_batch_size", "physical_batch_size"]
        .iter()
        .any(|field| !optional(profile, field).is_empty())
}

fn sdk_args(profile: &Value, load_key: &str) -> Vec<String> {
    let loader = std::env::var("LMSTUDIO_SDK_LOADER").unwrap_or_else(|_| "lmstudio-sdk-loader.mjs".to_string());
    let mut args = vec![
        std::env::var("NODE_BIN").unwrap_or_else(|_| "node".to_string()),
        loader,
        "--model".to_string(),
        load_key.to_string(),
        "--identifier".to_string(),
        identifier(profile),
        "--context-length".to_string(),
        number(profile, "context_length"),
        "--gpu".to_string(),
        text(profile, "gpu"),
        "--parallel".to_string(),
        number(profile, "parallel"),
        "--ttl".to_string(),
        number(profile, "ttl"),
    ];
    for (field, option) in [
        ("k_cache_quantization_type", "--k-cache"),
        ("v_cache_quantization_type", "--v-cache"),
        ("context_checkpoints", "--context-checkpoints"),
        ("eval_batch_size", "--eval-batch-size"),
        ("physical_batch_size", "--physical-batch-size"),
    ] {
        let value = optional(profile, field);
        if !value.is_empty() {
            args.push(option.to_string());
            args.push(value);
        }
    }
    if profile.get("flash_attention").and_then(Value::as_bool).unwrap_or(true) {
        args.push("--flash-attention".to_string());
    }
    if profile.get("offload_kv_cache_to_gpu").and_then(Value::as_bool).unwrap_or(true) {
        args.push("--offload-kv-cache".to_string());
    }
    if profile.get("speculative_draft_mtp").and_then(Value::as_bool).unwrap_or(false) {
        args.push("--mtp".to_string());
    }
    args
}

fn run_lms(lms: &Path, args: &[&str]) -> Outcome {
    let mut command = vec![lms.to_string_lossy().to_string()];
    command.extend(args.iter().map(|arg| (*arg).to_string()));
    let borrowed: Vec<&str> = command.iter().map(String::as_str).collect();
    run_command(&borrowed)
}

fn run_command(args: &[&str]) -> Outcome {
    let Some((program, rest)) = args.split_first() else {
        return Outcome { code: 1, stdout: String::new(), stderr: String::new() };
    };
    match Command::new(program).args(rest).output() {
        Ok(output) => Outcome {
            code: output.status.code().unwrap_or(1),
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
        },
        Err(err) => Outcome { code: 1, stdout: String::new(), stderr: err.to_string() },
    }
}

fn parse_list(text: &str) -> Vec<Value> {
    serde_json::from_str(text).unwrap_or_default()
}

fn text(value: &Value, field: &str) -> String {
    match value.get(field) {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Number(number)) => number.to_string(),
        Some(Value::Bool(flag)) => flag.to_string(),
        _ => String::new(),
    }
}

fn optional(value: &Value, field: &str) -> String {
    text(value, field)
}

fn number(value: &Value, field: &str) -> String {
    value.get(field).and_then(Value::as_i64).map(|number| number.to_string()).unwrap_or_else(|| text(value, field))
}

fn identifier(profile: &Value) -> String {
    let explicit = optional(profile, "identifier");
    if explicit.is_empty() { text(profile, "model_key") } else { explicit }
}

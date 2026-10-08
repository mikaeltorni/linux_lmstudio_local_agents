//! Model registry resolution for LM Studio local agents.
//!
//! The functions here match `scripts/lmstudio_models.py`. Filesystem and `lms`
//! work stay in the installer and the model selector.

use regex::Regex;
use serde_json::{json, Map, Value};
use std::collections::{BTreeSet, HashSet};
use std::env;
use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use url::Url;

const TRUTHY: &[&str] = &["1", "true", "yes", "y", "on"];
const DEFAULT_MAIN_ENV_VAR: &str = "LMSTUDIO_MAIN_MODEL";
const BOOT_FIELDS: &[&str] = &[
    "model_key",
    "load_key",
    "model_path",
    "context_length",
    "gpu",
    "parallel",
    "ttl",
    "identifier",
    "speculative_draft_mtp",
    "k_cache_quantization_type",
    "v_cache_quantization_type",
    "context_checkpoints",
    "eval_batch_size",
    "physical_batch_size",
    "flash_attention",
    "offload_kv_cache_to_gpu",
];
const DOWNLOAD_FIELDS: &[&str] = &[
    "id",
    "download_url",
    "download_source",
    "model_path",
    "sha256",
];
const FIELD_SEPARATOR: char = '\u{1f}';

/// Run one models command.
///
/// Parameters: args - command-line arguments after the program name.
///
/// Returns: process exit code.
pub fn run(args: Vec<String>) -> i32 {
    match execute(&args) {
        Ok(output) => {
            print!("{output}");
            let _ = io::stdout().flush();
            0
        }
        Err(message) => {
            eprintln!("{message}");
            1
        }
    }
}

fn execute(args: &[String]) -> Result<String, String> {
    let parsed = parse_args(args)?;
    let registry = load_registry(&parsed.catalog)?;
    match parsed.command.as_str() {
        "upgrade-catalog" => {
            let shipped = parsed
                .shipped_catalog
                .as_ref()
                .ok_or_else(|| "upgrade-catalog requires --shipped-catalog".to_string())?;
            Ok(pretty(&upgrade_catalog(&registry, &load_registry(shipped)?)?))
        }
        "profile-from-source" => Ok(pretty(&custom_profile_from_source(&parsed.source)?)),
        "custom-selection" => {
            let mut spec = if parsed.source.trim().is_empty() {
                json!({
                    "name": parsed.name,
                    "model_key": parsed.model_key,
                    "download_url": parsed.download_url,
                    "sha256": parsed.sha256,
                })
            } else {
                custom_profile_from_source(&parsed.source)?
            };
            if parsed.switch_env_set {
                spec["switch_env_var"] = Value::String(parsed.switch_env_var.clone());
            }
            Ok(pretty(&custom_install_selection(
                &registry,
                &spec,
                &parsed.role,
            )?))
        }
        "apply-selection" => {
            let raw = match &parsed.selection {
                Some(path) => fs::read_to_string(path).map_err(|err| err.to_string())?,
                None => {
                    let mut buffer = String::new();
                    io::stdin().read_to_string(&mut buffer).map_err(|err| err.to_string())?;
                    buffer
                }
            };
            let choice: Value = serde_json::from_str(if raw.trim().is_empty() { "{}" } else { &raw })
                .map_err(|err| err.to_string())?;
            let selected = string_list(choice.get("selected_ids"));
            let autostart = string_list(choice.get("autostart_ids"));
            let main_id = choice.get("main_id").and_then(Value::as_str).map(str::to_string);
            let customs = choice
                .get("custom_profiles")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            Ok(pretty(&apply_install_selection(
                &registry,
                &selected,
                &autostart,
                main_id.as_deref(),
                &customs,
            )?))
        }
        "active-main" | "keep-on" | "boot-load" | "download-list" | "default-plan" => {
            let plan = default_plan(&registry)?;
            match parsed.command.as_str() {
                "active-main" => {
                    if plan.install_ids.is_empty() {
                        Ok(String::new())
                    } else {
                        let profile = resolve_active_main(&registry, &plan, None)?;
                        Ok(format!("{}\n", profile["model_key"].as_str().unwrap_or("")))
                    }
                }
                "keep-on" => Ok(plan
                    .keep_on_model_keys(&registry)?
                    .into_iter()
                    .map(|key| format!("{key}\n"))
                    .collect()),
                "boot-load" => Ok(boot_load_specs(&registry, &plan, None)?
                    .into_iter()
                    .map(|spec| format!("{spec}\n"))
                    .collect()),
                "download-list" => {
                    let selected = parse_selection(&env::var("LMSTUDIO_INSTALL_MODELS").unwrap_or_default());
                    let selected_ref = if selected.is_empty() { None } else { Some(selected.as_slice()) };
                    Ok(download_specs(&registry, selected_ref)?
                        .into_iter()
                        .map(|line| format!("{line}\n"))
                        .collect())
                }
                _ => Ok(pretty(&plan.as_value())),
            }
        }
        other => Err(format!("unknown command: {other}")),
    }
}

struct ParsedArgs {
    command: String,
    catalog: PathBuf,
    shipped_catalog: Option<PathBuf>,
    selection: Option<PathBuf>,
    name: String,
    model_key: String,
    download_url: String,
    source: String,
    sha256: String,
    switch_env_var: String,
    switch_env_set: bool,
    role: String,
}

fn parse_args(args: &[String]) -> Result<ParsedArgs, String> {
    if args.is_empty() || args.iter().any(|arg| arg == "-h" || arg == "--help") {
        return Err("usage: lmstudio-models COMMAND --catalog PATH".into());
    }
    let command = args[0].clone();
    let mut catalog = None;
    let mut shipped_catalog = None;
    let mut selection = None;
    let mut name = String::new();
    let mut model_key = String::new();
    let mut download_url = String::new();
    let mut source = String::new();
    let mut sha256 = String::new();
    let mut switch_env_var = String::new();
    let mut switch_env_set = false;
    let mut role = "main".to_string();
    let mut index = 1;
    while index < args.len() {
        let flag = &args[index];
        let value = args.get(index + 1).ok_or_else(|| format!("{flag} needs a value"))?;
        match flag.as_str() {
            "--catalog" => catalog = Some(PathBuf::from(value)),
            "--shipped-catalog" => shipped_catalog = Some(PathBuf::from(value)),
            "--selection" => selection = Some(PathBuf::from(value)),
            "--name" => name = value.clone(),
            "--model-key" => model_key = value.clone(),
            "--download-url" => download_url = value.clone(),
            "--source" => source = value.clone(),
            "--sha256" => sha256 = value.clone(),
            "--switch-env-var" => {
                switch_env_var = value.clone();
                switch_env_set = true;
            }
            "--role" => role = value.clone(),
            other => return Err(format!("unknown argument: {other}")),
        }
        index += 2;
    }
    if !matches!(role.as_str(), "main" | "keep-on") {
        return Err(format!("unsupported custom model role: {role}"));
    }
    Ok(ParsedArgs {
        command,
        catalog: catalog.ok_or_else(|| "--catalog is required".to_string())?,
        shipped_catalog,
        selection,
        name,
        model_key,
        download_url,
        source,
        sha256,
        switch_env_var,
        switch_env_set,
        role,
    })
}

fn pretty(value: &Value) -> String {
    format!("{}\n", serde_json::to_string_pretty(value).unwrap_or_else(|_| "{}".to_string()))
}

fn load_registry(path: &Path) -> Result<Value, String> {
    let text = fs::read_to_string(path).map_err(|err| err.to_string())?;
    let data: Value = serde_json::from_str(&text).map_err(|err| err.to_string())?;
    if !data.get("profiles").and_then(Value::as_array).is_some() {
        return Err("model registry must contain a 'profiles' list".into());
    }
    Ok(data)
}

fn profiles(registry: &Value) -> Vec<Value> {
    registry
        .get("profiles")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
}

fn as_int(value: Option<&Value>) -> i64 {
    match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(0),
        Some(Value::String(text)) => text.parse().unwrap_or(0),
        Some(Value::Bool(true)) => 1,
        _ => 0,
    }
}

fn upgrade_catalog(installed: &Value, shipped: &Value) -> Result<Value, String> {
    if as_int(installed.get("schema_version")) >= as_int(shipped.get("schema_version")) {
        return Ok(installed.clone());
    }
    let mut result = shipped.clone();
    let installed_by_id: Vec<(String, Value)> = profiles(installed)
        .into_iter()
        .filter_map(|profile| {
            let id = profile.get("id").and_then(Value::as_str)?.to_string();
            Some((id, profile))
        })
        .collect();
    let choice_fields = [
        "switch_env_var",
        "default_install",
        "default_keep_on",
        "default_main",
        "default_startup",
        "group",
    ];
    let installed_schema = as_int(installed.get("schema_version"));
    if let Some(result_profiles) = result.get_mut("profiles").and_then(Value::as_array_mut) {
        for profile in result_profiles.iter_mut() {
            let id = profile.get("id").and_then(Value::as_str).unwrap_or("");
            let Some((_, previous)) = installed_by_id.iter().find(|(pid, _)| pid == id) else {
                continue;
            };
            let install_policy = as_int(profile.get("install_policy_schema"));
            let startup_policy = as_int(profile.get("startup_policy_schema"));
            for field in choice_fields {
                if matches!(field, "default_install" | "default_keep_on" | "default_main")
                    && installed_schema < install_policy
                {
                    continue;
                }
                if field == "default_startup" && installed_schema < startup_policy {
                    continue;
                }
                if let Some(value) = previous.get(field) {
                    profile[field] = value.clone();
                }
            }
        }
    }
    let shipped_ids: HashSet<String> = profiles(shipped)
        .iter()
        .filter_map(|profile| profile.get("id").and_then(Value::as_str).map(str::to_string))
        .collect();
    if let Some(result_profiles) = result.get_mut("profiles").and_then(Value::as_array_mut) {
        for profile in profiles(installed) {
            let id = profile.get("id").and_then(Value::as_str).unwrap_or("");
            if !shipped_ids.contains(id) {
                result_profiles.push(profile);
            }
        }
    }
    Ok(result)
}

fn main_env_var(registry: &Value) -> String {
    registry
        .get("main_env_var")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(DEFAULT_MAIN_ENV_VAR)
        .to_string()
}

fn find_profile<'a>(registry: &'a Value, identifier: &str) -> Option<&'a Value> {
    if identifier.is_empty() {
        return None;
    }
    registry
        .get("profiles")
        .and_then(Value::as_array)?
        .iter()
        .find(|profile| {
            ["id", "command", "model_key"]
                .iter()
                .any(|field| profile.get(field).and_then(Value::as_str) == Some(identifier))
        })
}

struct ModelPlan {
    install_ids: Vec<String>,
    keep_on_ids: Vec<String>,
    main_id: String,
    switch_env_vars: Map<String, Value>,
    main_env_var: String,
}

impl ModelPlan {
    fn keep_on_model_keys(&self, registry: &Value) -> Result<Vec<String>, String> {
        self.keep_on_ids
            .iter()
            .map(|pid| {
                find_profile(registry, pid)
                    .and_then(|profile| profile.get("model_key").and_then(Value::as_str))
                    .map(str::to_string)
                    .ok_or_else(|| format!("missing model key for {pid}"))
            })
            .collect()
    }

    fn as_value(&self) -> Value {
        json!({
            "install_ids": self.install_ids,
            "keep_on_ids": self.keep_on_ids,
            "main_id": self.main_id,
            "switch_env_vars": self.switch_env_vars,
            "main_env_var": self.main_env_var,
        })
    }
}

fn ordered_ids(registry: &Value, ids: &HashSet<String>) -> Vec<String> {
    profiles(registry)
        .into_iter()
        .filter_map(|profile| profile.get("id").and_then(Value::as_str).map(str::to_string))
        .filter(|pid| ids.contains(pid))
        .collect()
}

fn build_plan(
    registry: &Value,
    install_ids: &[String],
    keep_on_ids: &[String],
    main_id: &str,
    switch_env_vars: Option<&Map<String, Value>>,
) -> Result<ModelPlan, String> {
    let install_set: HashSet<String> = install_ids.iter().cloned().collect();
    if install_set.is_empty() {
        return Err("at least one model must be selected for installation".into());
    }
    for pid in &install_set {
        match find_profile(registry, pid) {
            Some(profile) if profile.get("id").and_then(Value::as_str) == Some(pid) => {}
            _ => return Err(format!("unknown model id: {pid}")),
        }
    }
    let keep_on_set: HashSet<String> = keep_on_ids.iter().cloned().collect();
    let extra: BTreeSet<_> = keep_on_set.difference(&install_set).cloned().collect();
    if !extra.is_empty() {
        return Err(format!("keep-on models not in install set: {extra:?}"));
    }
    if !install_set.contains(main_id) {
        return Err(format!("main model '{main_id}' is not in the install set"));
    }
    let mut resolved = Map::new();
    for pid in ordered_ids(registry, &install_set) {
        let override_value = switch_env_vars
            .and_then(|values| values.get(&pid))
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty());
        let fallback = find_profile(registry, &pid)
            .and_then(|profile| profile.get("switch_env_var"))
            .and_then(Value::as_str)
            .unwrap_or("");
        resolved.insert(pid, Value::String(override_value.unwrap_or(fallback).to_string()));
    }
    Ok(ModelPlan {
        install_ids: ordered_ids(registry, &install_set),
        keep_on_ids: ordered_ids(registry, &keep_on_set),
        main_id: main_id.to_string(),
        switch_env_vars: resolved,
        main_env_var: main_env_var(registry),
    })
}

fn is_truthy(value: Option<&str>) -> bool {
    TRUTHY.contains(&value.unwrap_or("").trim().to_ascii_lowercase().as_str())
}

fn json_bool(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Bool(flag)) => *flag,
        Some(Value::Number(number)) => number.as_i64().unwrap_or(0) != 0,
        Some(Value::String(text)) => !text.is_empty() && text != "0" && text.to_ascii_lowercase() != "false",
        _ => false,
    }
}

fn autostarts_by_default(profile: &Value) -> bool {
    if profile.get("default_startup").is_some() {
        return json_bool(profile.get("default_startup"));
    }
    json_bool(profile.get("default_keep_on")) || json_bool(profile.get("default_main"))
}

fn default_plan(registry: &Value) -> Result<ModelPlan, String> {
    let install_ids: Vec<String> = profiles(registry)
        .into_iter()
        .filter(|profile| json_bool(profile.get("default_install")))
        .filter_map(|profile| profile.get("id").and_then(Value::as_str).map(str::to_string))
        .collect();
    let keep_on_ids: Vec<String> = profiles(registry)
        .into_iter()
        .filter(|profile| json_bool(profile.get("default_install")) && json_bool(profile.get("default_keep_on")))
        .filter_map(|profile| profile.get("id").and_then(Value::as_str).map(str::to_string))
        .collect();
    let mains: Vec<String> = profiles(registry)
        .into_iter()
        .filter(|profile| json_bool(profile.get("default_install")) && json_bool(profile.get("default_main")))
        .filter_map(|profile| profile.get("id").and_then(Value::as_str).map(str::to_string))
        .collect();
    if install_ids.is_empty() {
        return Ok(ModelPlan {
            install_ids: Vec::new(),
            keep_on_ids: Vec::new(),
            main_id: String::new(),
            switch_env_vars: Map::new(),
            main_env_var: main_env_var(registry),
        });
    }
    if mains.len() != 1 {
        return Err(format!("exactly one installed profile must set default_main, found {mains:?}"));
    }
    build_plan(registry, &install_ids, &keep_on_ids, &mains[0], None)
}

fn resolve_active_main(registry: &Value, plan: &ModelPlan, env_map: Option<&Map<String, Value>>) -> Result<Value, String> {
    let lookup = |name: &str| -> Option<String> {
        if let Some(map) = env_map {
            return map.get(name).and_then(Value::as_str).map(str::to_string);
        }
        env::var(name).ok()
    };
    for pid in &plan.install_ids {
        if let Some(var) = plan.switch_env_vars.get(pid).and_then(Value::as_str) {
            if !var.is_empty() && is_truthy(lookup(var).as_deref()) {
                return find_profile(registry, pid).cloned().ok_or_else(|| format!("missing profile {pid}"));
            }
        }
    }
    let named = lookup(&plan.main_env_var).unwrap_or_default();
    let named = named.trim().to_string();
    if !named.is_empty() {
        if let Some(candidate) = find_profile(registry, &named) {
            if plan.install_ids.iter().any(|pid| Some(pid.as_str()) == candidate.get("id").and_then(Value::as_str)) {
                return Ok(candidate.clone());
            }
        }
    }
    find_profile(registry, &plan.main_id)
        .cloned()
        .ok_or_else(|| format!("missing main profile {}", plan.main_id))
}

fn boot_scalar(value: &Value) -> String {
    match value {
        Value::Bool(flag) => if *flag { "true" } else { "false" }.to_string(),
        Value::String(text) => text.clone(),
        Value::Number(number) => number.to_string(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

fn boot_load_specs(registry: &Value, plan: &ModelPlan, env_map: Option<&Map<String, Value>>) -> Result<Vec<String>, String> {
    if plan.install_ids.is_empty() {
        return Ok(Vec::new());
    }
    let lookup = |name: &str| -> Option<String> {
        if let Some(map) = env_map {
            return map.get(name).and_then(Value::as_str).map(str::to_string);
        }
        env::var(name).ok()
    };
    let installed: Vec<Value> = profiles(registry)
        .into_iter()
        .filter(|profile| {
            profile
                .get("id")
                .and_then(Value::as_str)
                .is_some_and(|pid| plan.install_ids.iter().any(|id| id == pid))
        })
        .collect();
    let mut active: Vec<Value> = installed.iter().filter(|profile| autostarts_by_default(profile)).cloned().collect();
    for selected in &installed {
        let pid = selected.get("id").and_then(Value::as_str).unwrap_or("");
        let var = plan.switch_env_vars.get(pid).and_then(Value::as_str).unwrap_or("");
        if var.is_empty() || !is_truthy(lookup(var).as_deref()) {
            continue;
        }
        let group = as_int(selected.get("group"));
        if group != 0 {
            active.retain(|profile| as_int(profile.get("group")) != group);
        }
        if !active.iter().any(|profile| profile.get("id") == selected.get("id")) {
            active.push(selected.clone());
        }
    }
    let mut lines = Vec::new();
    let mut seen = HashSet::new();
    for profile in active {
        let pid = profile.get("id").and_then(Value::as_str).unwrap_or("").to_string();
        if !seen.insert(pid) {
            continue;
        }
        let model_key = profile.get("model_key").and_then(Value::as_str).unwrap_or("");
        let load_key = profile.get("load_key").and_then(Value::as_str).filter(|value| !value.is_empty()).unwrap_or(model_key);
        let identifier = profile.get("identifier").and_then(Value::as_str).filter(|value| !value.is_empty()).unwrap_or(model_key);
        let spec = json!({
            "model_key": model_key,
            "load_key": load_key,
            "model_path": profile.get("model_path").cloned().unwrap_or(Value::String(String::new())),
            "context_length": profile.get("context_length").cloned().ok_or("missing context_length")?,
            "gpu": profile.get("gpu").cloned().ok_or("missing gpu")?,
            "parallel": profile.get("parallel").cloned().ok_or("missing parallel")?,
            "ttl": profile.get("ttl").cloned().unwrap_or(json!(0)),
            "identifier": identifier,
            "speculative_draft_mtp": profile.get("speculative_draft_mtp").and_then(Value::as_bool).unwrap_or(false),
            "k_cache_quantization_type": profile.get("k_cache_quantization_type").and_then(Value::as_str).unwrap_or(""),
            "v_cache_quantization_type": profile.get("v_cache_quantization_type").and_then(Value::as_str).unwrap_or(""),
            "context_checkpoints": profile.get("context_checkpoints").cloned().unwrap_or(Value::String(String::new())),
            "eval_batch_size": profile.get("eval_batch_size").cloned().unwrap_or(Value::String(String::new())),
            "physical_batch_size": profile.get("physical_batch_size").cloned().unwrap_or(Value::String(String::new())),
            "flash_attention": profile.get("flash_attention").and_then(Value::as_bool).unwrap_or(true),
            "offload_kv_cache_to_gpu": profile.get("offload_kv_cache_to_gpu").and_then(Value::as_bool).unwrap_or(true),
        });
        let line = BOOT_FIELDS
            .iter()
            .map(|field| boot_scalar(&spec[field]))
            .collect::<Vec<_>>()
            .join(&FIELD_SEPARATOR.to_string());
        lines.push(line);
    }
    Ok(lines)
}

fn parse_selection(raw: &str) -> Vec<String> {
    raw.replace(',', " ")
        .split_whitespace()
        .map(str::to_string)
        .collect()
}

fn installable_ids(registry: &Value, selected: Option<&[String]>) -> Result<Vec<String>, String> {
    let known: HashSet<String> = profiles(registry)
        .iter()
        .filter_map(|profile| profile.get("id").and_then(Value::as_str).map(str::to_string))
        .collect();
    if let Some(selected) = selected {
        if !selected.is_empty() {
            let unknown: BTreeSet<_> = selected
                .iter()
                .filter(|pid| !known.contains(pid.as_str()))
                .cloned()
                .collect();
            if !unknown.is_empty() {
                return Err(format!("unknown model ids: {unknown:?}"));
            }
            let wanted: HashSet<_> = selected.iter().cloned().collect();
            return Ok(profiles(registry)
                .into_iter()
                .filter_map(|profile| profile.get("id").and_then(Value::as_str).map(str::to_string))
                .filter(|pid| wanted.contains(pid))
                .collect());
        }
    }
    Ok(profiles(registry)
        .into_iter()
        .filter(|profile| json_bool(profile.get("default_install")))
        .filter_map(|profile| profile.get("id").and_then(Value::as_str).map(str::to_string))
        .collect())
}

fn py_download_value(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(number) => number.to_string(),
        Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

fn download_specs(registry: &Value, selected: Option<&[String]>) -> Result<Vec<String>, String> {
    let mut lines = Vec::new();
    for pid in installable_ids(registry, selected)? {
        let Some(profile) = find_profile(registry, &pid) else { continue };
        let has_url = profile.get("download_url").and_then(Value::as_str).is_some_and(|value| !value.is_empty());
        let has_source = profile.get("download_source").and_then(Value::as_str).is_some_and(|value| !value.is_empty());
        if !(has_url || has_source) {
            continue;
        }
        let line = DOWNLOAD_FIELDS
            .iter()
            .map(|field| py_download_value(profile.get(field).unwrap_or(&Value::String(String::new()))))
            .collect::<Vec<_>>()
            .join(&FIELD_SEPARATOR.to_string());
        lines.push(line);
    }
    Ok(lines)
}

fn slugify_id(value: &str) -> String {
    let pattern = Regex::new(r"[^a-z0-9]+").expect("slug pattern");
    pattern
        .replace_all(&value.trim().to_ascii_lowercase(), "-")
        .trim_matches('-')
        .to_string()
}

fn switch_env_var_for(model_id: &str) -> String {
    let pattern = Regex::new(r"[^A-Z0-9]+").expect("env pattern");
    let suffix = pattern
        .replace_all(&model_id.to_ascii_uppercase(), "_")
        .trim_matches('_')
        .to_string();
    format!("LMSTUDIO_USE_{suffix}")
}

fn custom_profile_from_source(source: &str) -> Result<Value, String> {
    let raw = source.trim();
    if raw.is_empty() {
        return Err("enter a Hugging Face URL or owner/model@quant".into());
    }
    let parsed = Url::parse(raw).ok();
    let scheme = parsed.as_ref().map(|url| url.scheme()).unwrap_or("");
    if matches!(scheme, "http" | "https") {
        let url = parsed.as_ref().unwrap();
        if url.path().to_ascii_lowercase().ends_with(".gguf") {
            let name = url
                .path()
                .rsplit('/')
                .next()
                .unwrap_or("")
                .rsplit_once('.')
                .map(|(stem, _)| stem)
                .unwrap_or("");
            return normalize_custom_profile(&json!({"name": name, "download_url": raw}));
        }
        if !matches!(url.host_str().unwrap_or("").to_ascii_lowercase().as_str(), "huggingface.co" | "www.huggingface.co") {
            return Err("repository URLs must be from huggingface.co".into());
        }
        let parts: Vec<&str> = url.path().split('/').filter(|part| !part.is_empty()).collect();
        if parts.len() < 2 {
            return Err("Hugging Face URL must contain owner/model".into());
        }
        let owner = parts[0];
        let model_quant = parts[1];
        let (model, catalog_source) = if let Some((model, quant)) = model_quant.rsplit_once('@') {
            (model.to_string(), format!("{owner}/{model}@{quant}"))
        } else {
            (model_quant.to_string(), raw.to_string())
        };
        let model_key = if !model_quant.contains('@') {
            format!("{owner}/{model}")
        } else {
            catalog_source.clone()
        };
        let mut profile = normalize_custom_profile(&json!({"name": model, "model_key": model_key}))?;
        profile["download_source"] = Value::String(catalog_source);
        return Ok(profile);
    }
    let catalog_source = raw.to_string();
    let direct = Regex::new(r"^[^/\s]+/[^@/\s]+@[^@/\s]+$").expect("catalog pattern");
    if !direct.is_match(&catalog_source) {
        return Err("use owner/model@quant (for example acme/model-GGUF@Q4_K_M)".into());
    }
    let model = catalog_source
        .split_once('/')
        .map(|(_, rest)| rest.rsplit_once('@').map(|(name, _)| name).unwrap_or(rest))
        .unwrap_or("");
    let mut profile = normalize_custom_profile(&json!({"name": model, "model_key": catalog_source}))?;
    profile["download_source"] = Value::String(catalog_source);
    Ok(profile)
}

fn normalize_custom_profile(spec: &Value) -> Result<Value, String> {
    let raw_id = spec.get("id").and_then(Value::as_str).filter(|value| !value.is_empty())
        .or_else(|| spec.get("name").and_then(Value::as_str))
        .unwrap_or("");
    let model_id = slugify_id(raw_id);
    if model_id.is_empty() {
        return Err("a custom model needs a name".into());
    }
    let model_key = spec.get("model_key").and_then(Value::as_str).unwrap_or("").trim();
    let model_key = if model_key.is_empty() { model_id.clone() } else { model_key.to_string() };
    let download_url = spec.get("download_url").and_then(Value::as_str).unwrap_or("").trim().to_string();
    if !download_url.is_empty() {
        match Url::parse(&download_url) {
            Ok(url) if matches!(url.scheme(), "http" | "https") && url.host().is_some() => {}
            _ => return Err("custom model download URL must be an HTTP(S) URL".into()),
        }
    }
    let sha256 = match spec.get("sha256") {
        Some(Value::String(text)) => text.trim().to_string(),
        Some(other) if !other.is_null() => other.to_string(),
        _ => String::new(),
    };
    if !sha256.is_empty() && !Regex::new(r"^[A-Fa-f0-9]{64}$").expect("sha").is_match(&sha256) {
        return Err("custom model sha256 must contain exactly 64 hexadecimal characters".into());
    }
    let mut model_path = spec.get("model_path").and_then(Value::as_str).unwrap_or("").trim().to_string();
    if model_path.is_empty() {
        if !download_url.is_empty() {
            let base = download_url.split('?').next().unwrap_or("").trim_end_matches('/').rsplit('/').next().unwrap_or("");
            model_path = if base.is_empty() { format!("{model_id}.gguf") } else { format!("{model_id}/{base}") };
        } else {
            model_path = format!("{model_id}.gguf");
        }
    }
    let switch_env_var = if spec.get("switch_env_var").is_some() {
        spec.get("switch_env_var").and_then(Value::as_str).unwrap_or("").trim().to_string()
    } else {
        switch_env_var_for(&model_id)
    };
    if !switch_env_var.is_empty()
        && !Regex::new(r"^[A-Za-z_][A-Za-z0-9_]*$").expect("env name").is_match(&switch_env_var)
    {
        return Err(format!("invalid switch environment variable: {switch_env_var}"));
    }
    let command = spec.get("command").and_then(Value::as_str).filter(|value| !value.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| slugify_id(raw_id).replace('-', ""));
    let description = spec.get("description").and_then(Value::as_str).filter(|value| !value.is_empty())
        .or_else(|| spec.get("name").and_then(Value::as_str).filter(|value| !value.is_empty()))
        .unwrap_or(if raw_id.is_empty() { model_id.as_str() } else { raw_id })
        .to_string();
    let mut profile = json!({
        "gpu": "max",
        "context_length": 131072,
        "parallel": 1,
        "speculative_draft_mtp": false,
        "ttl": 0,
        "identifier": "",
        "sha256": "",
        "yes": true,
        "id": model_id,
        "command": command,
        "description": description,
        "model_key": model_key,
        "download_url": download_url,
        "model_path": model_path,
        "switch_env_var": switch_env_var,
        "default_install": true,
        "default_keep_on": false,
        "default_main": false,
        "default_startup": false,
        "group": as_int(spec.get("group")),
        "custom": true,
    });
    for key in ["gpu", "context_length", "parallel", "ttl", "identifier", "speculative_draft_mtp"] {
        if let Some(value) = spec.get(key) {
            if !value.is_null() && value.as_str() != Some("") {
                profile[key] = value.clone();
            }
        }
    }
    if !sha256.is_empty() {
        profile["sha256"] = Value::String(sha256.to_ascii_lowercase());
    }
    Ok(profile)
}

fn custom_install_selection(registry: &Value, spec: &Value, role: &str) -> Result<Value, String> {
    if !matches!(role, "main" | "keep-on") {
        return Err(format!("unsupported custom model role: {role}"));
    }
    let custom = if json_bool(spec.get("custom")) {
        spec.clone()
    } else {
        normalize_custom_profile(spec)?
    };
    let custom_id = custom.get("id").and_then(Value::as_str).unwrap_or("");
    let mut selected = Vec::new();
    let mut autostart = Vec::new();
    let mut current_main = None;
    for profile in profiles(registry) {
        let pid = profile.get("id").and_then(Value::as_str).unwrap_or("");
        if json_bool(profile.get("default_install")) && pid != custom_id {
            selected.push(pid.to_string());
            if json_bool(profile.get("default_keep_on")) || json_bool(profile.get("default_main")) {
                autostart.push(pid.to_string());
            }
        }
        if json_bool(profile.get("default_install")) && json_bool(profile.get("default_main")) && current_main.is_none() {
            current_main = Some(pid.to_string());
        }
    }
    selected.push(custom_id.to_string());
    autostart.push(custom_id.to_string());
    Ok(json!({
        "selected_ids": selected,
        "autostart_ids": autostart,
        "main_id": if role == "main" { custom_id.to_string() } else { current_main.unwrap_or_default() },
        "custom_profiles": [custom],
    }))
}

fn string_list(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(|item| item.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

fn apply_install_selection(
    registry: &Value,
    selected_ids: &[String],
    autostart_ids: &[String],
    main_id: Option<&str>,
    custom_profiles: &[Value],
) -> Result<Value, String> {
    let mut new_registry = registry.clone();
    if !new_registry.get("profiles").and_then(Value::as_array).is_some() {
        new_registry["profiles"] = json!([]);
    }
    {
        let base = new_registry.get_mut("profiles").and_then(Value::as_array_mut).unwrap();
        for spec in custom_profiles {
            let profile = if json_bool(spec.get("custom")) {
                spec.clone()
            } else {
                normalize_custom_profile(spec)?
            };
            let pid = profile.get("id").and_then(Value::as_str).unwrap_or("").to_string();
            base.retain(|item| item.get("id").and_then(Value::as_str) != Some(pid.as_str()));
            base.push(profile);
        }
    }
    let known: HashSet<String> = new_registry
        .get("profiles")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .filter_map(|profile| profile.get("id").and_then(Value::as_str).map(str::to_string))
        .collect();
    let unknown: BTreeSet<_> = selected_ids.iter().filter(|pid| !known.contains(pid.as_str())).cloned().collect();
    if !unknown.is_empty() {
        return Err(format!("unknown model ids: {unknown:?}"));
    }
    let selected_set: HashSet<_> = selected_ids.iter().cloned().collect();
    let autostart_set: HashSet<_> = autostart_ids.iter().cloned().collect();
    let extra: BTreeSet<_> = autostart_set.difference(&selected_set).cloned().collect();
    if !extra.is_empty() {
        return Err(format!("autostart models not selected for install: {extra:?}"));
    }
    let order: Vec<String> = new_registry
        .get("profiles")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .filter_map(|profile| profile.get("id").and_then(Value::as_str).map(str::to_string))
        .collect();
    let autostart_ordered: Vec<String> = order.iter().filter(|pid| autostart_set.contains(*pid)).cloned().collect();
    let mut resolved_main: Option<String> = None;
    if !selected_set.is_empty() {
        if let Some(main_id) = main_id {
            if selected_set.contains(main_id) {
                resolved_main = Some(main_id.to_string());
            }
        }
        if resolved_main.is_none() {
            for profile in new_registry.get("profiles").and_then(Value::as_array).unwrap() {
                let pid = profile.get("id").and_then(Value::as_str).unwrap_or("");
                if selected_set.contains(pid) && json_bool(profile.get("default_main")) {
                    resolved_main = Some(pid.to_string());
                    break;
                }
            }
        }
        if resolved_main.is_none() {
            resolved_main = autostart_ordered.first().cloned().or_else(|| {
                order.into_iter().find(|pid| selected_set.contains(pid))
            });
        }
    }
    let keep_on: HashSet<String> = autostart_set
        .into_iter()
        .filter(|pid| Some(pid) != resolved_main.as_ref())
        .collect();
    if let Some(profiles) = new_registry.get_mut("profiles").and_then(Value::as_array_mut) {
        for profile in profiles {
            let pid = profile.get("id").and_then(Value::as_str).unwrap_or("").to_string();
            profile["default_install"] = Value::Bool(selected_set.contains(&pid));
            profile["default_keep_on"] = Value::Bool(keep_on.contains(&pid));
            profile["default_main"] = Value::Bool(Some(&pid) == resolved_main.as_ref());
            profile["default_startup"] = Value::Bool(autostart_ids.iter().any(|id| id == &pid));
        }
    }
    Ok(new_registry)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slug_and_switch_variable_follow_the_python_rules() {
        assert_eq!(slugify_id(" Hello World "), "hello-world");
        assert_eq!(switch_env_var_for("hello-world"), "LMSTUDIO_USE_HELLO_WORLD");
    }

    #[test]
    fn shipped_default_plan_installs_nothing() {
        let registry = load_registry(Path::new(env!("CARGO_MANIFEST_DIR")).join("configs/lmstudio/model-profiles.json").as_path()).unwrap();
        let plan = default_plan(&registry).unwrap();
        assert!(plan.install_ids.is_empty());
        assert!(plan.keep_on_ids.is_empty());
        assert_eq!(plan.main_id, "");
        assert_eq!(plan.main_env_var, "LMSTUDIO_MAIN_MODEL");
    }

    #[test]
    fn missing_profiles_list_is_rejected() {
        let dir = std::env::temp_dir().join(format!("lmstudio-models-{}", std::process::id()));
        let _ = fs::create_dir_all(&dir);
        let path = dir.join("bad.json");
        fs::write(&path, r#"{"schema_version": 3}"#).unwrap();
        let error = load_registry(&path).unwrap_err();
        assert_eq!(error, "model registry must contain a 'profiles' list");
    }
}

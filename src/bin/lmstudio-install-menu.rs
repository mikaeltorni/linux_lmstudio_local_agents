//! LM Studio model-selection menu.

use lmstudio_models::menu::run_menu_reader;
use serde_json::Value;
use std::env;
use std::fs;
use std::io::{self, BufReader, Write};
use std::path::PathBuf;

fn main() {
    let code = match run(env::args().skip(1).collect()) {
        Ok(()) => 0,
        Err(message) => {
            eprintln!("{message}");
            2
        }
    };
    std::process::exit(code);
}

fn run(args: Vec<String>) -> Result<(), String> {
    let catalog = flag(&args, "--catalog").ok_or_else(|| "--catalog is required".to_string())?;
    let out = flag(&args, "--out").ok_or_else(|| "--out is required".to_string())?;
    let text = fs::read_to_string(&catalog).map_err(|err| err.to_string())?;
    let registry: Value = serde_json::from_str(&text).map_err(|err| err.to_string())?;
    let profiles = registry.get("profiles").and_then(Value::as_array).cloned().unwrap_or_default();
    let selection = run_menu_reader(&profiles, BufReader::new(io::stdin()), io::stdout());
    let body = serde_json::to_string_pretty(&selection).map_err(|err| err.to_string())?;
    if let Some(parent) = PathBuf::from(&out).parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let mut file = fs::File::create(&out).map_err(|err| err.to_string())?;
    writeln!(file, "{body}").map_err(|err| err.to_string())
}

fn flag(args: &[String], name: &str) -> Option<String> {
    args.iter().position(|arg| arg == name).and_then(|index| args.get(index + 1)).cloned()
}

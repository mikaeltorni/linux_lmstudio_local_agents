//! Switch the active LM Studio model and print its model key.

use lmstudio_models::switcher::switch;
use std::env;
use std::path::PathBuf;

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    let restore = args.iter().any(|arg| arg == "--restore");
    let catalog = flag(&args, "--catalog").map(PathBuf::from).unwrap_or_else(default_catalog);
    let state = flag(&args, "--state-file").map(PathBuf::from).unwrap_or_else(|| home().join(".config/lm-studio/selected-model"));
    let lms = env::var_os("LMS_CLI").map(PathBuf::from).unwrap_or_else(|| home().join(".lmstudio/bin/lms"));
    let profile = args.iter().find(|arg| !arg.starts_with("--") && args.get(args.iter().position(|item| item == *arg).unwrap().saturating_sub(1)).map(String::as_str) != Some("--catalog") && args.get(args.iter().position(|item| item == *arg).unwrap().saturating_sub(1)).map(String::as_str) != Some("--state-file")).cloned();
    match switch(profile.as_deref(), restore, &catalog, &state, &lms) {
        Ok(model_key) => {
            println!("{model_key}");
            std::process::exit(0);
        }
        Err(message) => {
            eprintln!("{message}");
            std::process::exit(1);
        }
    }
}

fn flag(args: &[String], name: &str) -> Option<String> {
    args.iter().position(|arg| arg == name).and_then(|index| args.get(index + 1)).cloned()
}

fn home() -> PathBuf {
    env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"))
}

fn default_catalog() -> PathBuf {
    home().join(".config/lmstudio-local-agents/configs/lmstudio/model-profiles.json")
}

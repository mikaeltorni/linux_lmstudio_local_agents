//! Compare the Rust model command with the Python command it replaces.

use serde_json::Value;
use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn catalog() -> PathBuf {
    root().join("configs/lmstudio/model-profiles.json")
}

fn run(program: &str, args: &[String], env: &[(&str, &str)], stdin: Option<&str>) -> (i32, String, String) {
    let mut command = Command::new(program);
    command.args(args);
    for (key, value) in env {
        command.env(key, value);
    }
    if stdin.is_some() {
        command.stdin(std::process::Stdio::piped());
    }
    command.stdout(std::process::Stdio::piped());
    command.stderr(std::process::Stdio::piped());
    let mut child = command.spawn().expect("spawn");
    if let Some(text) = stdin {
        use std::io::Write;
        child.stdin.as_mut().unwrap().write_all(text.as_bytes()).unwrap();
    }
    let output = child.wait_with_output().unwrap();
    (
        output.status.code().unwrap_or(1),
        String::from_utf8_lossy(&output.stdout).to_string(),
        String::from_utf8_lossy(&output.stderr).to_string(),
    )
}

fn compare(args: &[&str], env: &[(&str, &str)], stdin: Option<&str>, json_output: bool) {
    let catalog = catalog();
    let mut full = vec![args[0].to_string(), "--catalog".to_string(), catalog.display().to_string()];
    full.extend(args.iter().skip(1).map(|arg| (*arg).to_string()));
    let python = run("python3", &{
        let mut py = vec![root().join("scripts/lmstudio_models.py").display().to_string()];
        py.extend(full.clone());
        py
    }, env, stdin);
    let rust_bin = PathBuf::from(env!("CARGO_BIN_EXE_lmstudio-models"));
    let rust = run(rust_bin.to_str().unwrap(), &full, env, stdin);
    assert_eq!(rust.0, python.0, "status stderr rust={} python={}", rust.2, python.2);
    if json_output && python.0 == 0 {
        let left: Value = serde_json::from_str(&python.1).unwrap();
        let right: Value = serde_json::from_str(&rust.1).unwrap();
        assert_eq!(right, left, "args={args:?}");
    } else {
        assert_eq!(rust.1, python.1, "args={args:?} rust-err={} python-err={}", rust.2, python.2);
    }
}

#[test]
fn rust_cli_matches_python_on_the_shipped_catalog() {
    compare(&["default-plan"], &[], None, true);
    compare(&["active-main"], &[], None, false);
    compare(&["keep-on"], &[], None, false);
    compare(&["boot-load"], &[], None, false);
    compare(&["download-list"], &[], None, false);
    compare(&["download-list"], &[("LMSTUDIO_INSTALL_MODELS", "vibethinker")], None, false);
    compare(&["profile-from-source", "--source", "acme/model-GGUF@Q4_K_M"], &[], None, true);
    compare(
        &["profile-from-source", "--source", "https://example.com/models/Demo.Q4.gguf"],
        &[],
        None,
        true,
    );
    compare(
        &[
            "custom-selection",
            "--name",
            "Demo Model",
            "--model-key",
            "demo",
            "--download-url",
            "https://example.com/demo.gguf",
            "--role",
            "main",
        ],
        &[],
        None,
        true,
    );
    compare(&["apply-selection"], &[], Some("{}"), true);
    compare(
        &["apply-selection"],
        &[],
        Some(r#"{"selected_ids":["vibethinker"],"autostart_ids":["vibethinker"],"main_id":"vibethinker"}"#),
        true,
    );
}

#[test]
fn upgrade_keeps_a_current_catalog() {
    let shipped = catalog().display().to_string();
    compare(&["upgrade-catalog", "--shipped-catalog", &shipped], &[], None, true);
}

#[test]
fn missing_profile_list_fails_in_both_commands() {
    let path = std::env::temp_dir().join(format!("lmstudio-bad-{}.json", std::process::id()));
    fs::write(&path, r#"{"schema_version": 1}"#).unwrap();
    let py_args = vec![
        root().join("scripts/lmstudio_models.py").display().to_string(),
        "default-plan".to_string(),
        "--catalog".to_string(),
        path.display().to_string(),
    ];
    let rust_args = vec![
        "default-plan".to_string(),
        "--catalog".to_string(),
        path.display().to_string(),
    ];
    let python = run("python3", &py_args, &[], None);
    let rust_bin = PathBuf::from(env!("CARGO_BIN_EXE_lmstudio-models"));
    let rust = run(rust_bin.to_str().unwrap(), &rust_args, &[], None);
    assert_ne!(python.0, 0);
    assert_ne!(rust.0, 0);
    let _ = fs::remove_file(path);
}

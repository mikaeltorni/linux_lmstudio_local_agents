//! Menu selection and model-switch commands.

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};

fn menu() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_lmstudio-install-menu"))
}

fn switcher() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_lmstudio-model"))
}

fn catalog() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("configs/lmstudio/model-profiles.json")
}

fn first_id() -> String {
    let text = fs::read_to_string(catalog()).unwrap();
    text.split("\"id\": \"").nth(1).unwrap().split('"').next().unwrap().to_string()
}

#[test]
fn menu_selects_the_first_catalog_profile() {
    let dir = std::env::temp_dir().join(format!("lmstudio-menu-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let out = dir.join("selection.json");
    let mut child = Command::new(menu())
        .args(["--catalog", catalog().to_str().unwrap(), "--out", out.to_str().unwrap()])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.as_mut().unwrap().write_all(b"1\n\n").unwrap();
    let finished = child.wait_with_output().unwrap();
    assert!(finished.status.success(), "{}", String::from_utf8_lossy(&finished.stderr));
    let body = fs::read_to_string(&out).unwrap();
    assert!(body.contains(&format!("\"{}\"", first_id())), "{body}");
    assert!(body.contains("\"autostart_ids\": []"), "{body}");
    let _ = fs::remove_dir_all(dir);
}

#[test]
fn switcher_prints_the_catalog_model_key_and_records_the_choice() {
    let dir = std::env::temp_dir().join(format!("lmstudio-switch-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let lms = dir.join("lms");
    fs::write(&lms, "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$LMS_LOG\"\nif [ \"$1\" = ls ] || [ \"$1\" = ps ]; then printf '%s\\n' '[]'; fi\nexit 0\n").unwrap();
    let _ = std::process::Command::new("chmod").args(["0755", lms.to_str().unwrap()]).status();
    let log = dir.join("lms.log");
    let state = dir.join("selected");
    let output = Command::new(switcher())
        .args(["vibethinker", "--catalog", catalog().to_str().unwrap(), "--state-file", state.to_str().unwrap()])
        .env("LMS_CLI", &lms)
        .env("LMS_LOG", &log)
        .output()
        .unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let text = fs::read_to_string(catalog()).unwrap();
    let id = first_id();
    let after = text.split(&format!("\"id\": \"{id}\"")).nth(1).unwrap();
    let key = after.split("\"model_key\": \"").nth(1).unwrap().split('"').next().unwrap();
    assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), key);
    assert_eq!(fs::read_to_string(&state).unwrap().trim(), first_id());
    let calls = fs::read_to_string(&log).unwrap();
    assert!(calls.contains("server start"), "{calls}");
    assert!(calls.contains("load"), "{calls}");
    let _ = fs::remove_dir_all(dir);
}

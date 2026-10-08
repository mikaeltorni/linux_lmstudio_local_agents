//! Checks for the installer catalog snippets.

use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};

fn bin() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_lmstudio-snippets"))
}

fn catalog() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("configs/lmstudio/model-profiles.json")
}

#[test]
fn selection_from_ids_matches_the_shipped_vibethinker_rule() {
    let output = Command::new(bin())
        .args(["selection-from-ids", catalog().to_str().unwrap(), "vibethinker"])
        .output()
        .expect("snippets");
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).trim(),
        r#"{"selected_ids": ["vibethinker"], "autostart_ids": []}"#
    );
}

#[test]
fn resolve_load_key_prefers_the_requested_model_key() {
    let mut child = Command::new(bin())
        .args(["resolve-load-key", "wanted", "models/file.gguf"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn");
    child.stdin.as_mut().unwrap().write_all(br#"[{"modelKey":"other","path":"models/file.gguf"},{"modelKey":"wanted"}]"#).unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), "wanted");
}

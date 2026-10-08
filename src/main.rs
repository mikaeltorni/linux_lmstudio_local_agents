//! LM Studio model registry command.
//!
//! Reads a catalog and prints the same records the boot script, downloader,
//! and installer already consume.

fn main() {
    let code = lmstudio_models::run(std::env::args().skip(1).collect());
    std::process::exit(code);
}

//! Open Magi desktop shell library surface.
//!
//! The shell's decision logic lives in the dependency-free
//! [`magi_desktop_core`] crate (url policy, serve-binary resolution, bootstrap
//! readiness, lifecycle state machine). It is re-exported here so the GUI
//! binary and any integrator share a single import path. The `tauri`-dependent
//! window/navigation glue lives in `src/main.rs`; keeping it out of this lib
//! means `cargo test` over the pure logic never needs the webview toolchain.

pub use magi_desktop_core::{lifecycle, server, url_policy};

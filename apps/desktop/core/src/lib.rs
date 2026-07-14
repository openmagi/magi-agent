//! Pure, GUI-free core for the Open Magi desktop shell.
//!
//! These modules carry all of the testable decision logic for the shell:
//!   * [`url_policy`] decides which navigations load in-window vs the browser,
//!   * [`server`] locates the `magi` binary and judges bootstrap readiness,
//!   * [`lifecycle`] is the startup state machine the GUI drives.
//!
//! None of this depends on `tauri` or a system webview, so `cargo test` runs
//! the full suite on any host without the GUI toolchain.

pub mod lifecycle;
pub mod managed;
pub mod server;
pub mod url_policy;

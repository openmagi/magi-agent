//! Pure helpers for locating and supervising the `magi serve` process.
//!
//! Everything here is GUI-free and side-effect-light so it is unit-testable
//! without the tauri toolchain. The actual process spawn and HTTP polling live
//! in the GUI binary; these helpers decide *what* to spawn and *whether* a
//! response means "ready".

use std::io;
use std::net::TcpListener;
use std::path::{Path, PathBuf};

/// Path of the bundled `magi` executable inside the shipped onedir tree.
///
/// The Python runtime is built with PyInstaller `--onedir`, which produces a
/// DIRECTORY (`magi/` holding the `magi` executable plus `_internal/`), not a
/// single file. Tauri ships that tree via `bundle.resources` (`"binaries/magi":
/// "magi"`), so at runtime it lands at `<resource_dir>/magi/`. The executable
/// we launch is `<resource_dir>/magi/magi`.
pub fn bundled_resource_binary(resource_dir: &Path) -> PathBuf {
    resource_dir.join("magi").join("magi")
}

/// Resolve the `magi` binary to launch.
///
/// Resolution order, first existing wins:
///   1. the bundled onedir executable (`<resource_dir>/magi/magi`), when the
///      app ships the standalone PyInstaller `--onedir` tree as a resource,
///   2. the `MAGI_BIN` environment override,
///   3. `~/.magi/bin/magi`,
///   4. a `magi` discovered on `PATH` (where Homebrew installs land).
///
/// `exists` is injected so the ordering is testable without touching the real
/// filesystem. `path_lookup` resolves a bare command name against `PATH`.
pub fn resolve_magi_binary<E, L>(
    bundled: Option<PathBuf>,
    env_bin: Option<PathBuf>,
    home: Option<&Path>,
    exists: E,
    path_lookup: L,
) -> Option<PathBuf>
where
    E: Fn(&Path) -> bool,
    L: Fn(&str) -> Option<PathBuf>,
{
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(b) = bundled {
        candidates.push(b);
    }
    if let Some(e) = env_bin {
        candidates.push(e);
    }
    if let Some(h) = home {
        candidates.push(h.join(".magi").join("bin").join("magi"));
    }

    for candidate in candidates {
        if exists(&candidate) {
            return Some(candidate);
        }
    }

    // Last resort: a `magi` on PATH (brew installs land here).
    path_lookup("magi")
}

/// Decide whether a bootstrap response means the server is ready.
///
/// Ready iff HTTP status is 200 and the body is JSON with a top-level
/// `"ok": true`. Mirrors `/app/bootstrap.json` which returns
/// `{"ok": true, ...}` once the runtime is up.
pub fn is_ready(body: &str, status: u16) -> bool {
    if status != 200 {
        return false;
    }
    body_reports_ok(body)
}

/// Tolerant scan for a TOP-LEVEL `"ok": true` in a small JSON object.
///
/// We avoid a JSON dependency here on purpose. The bootstrap shape is fixed and
/// flat, so we only need to confirm the FIRST key of the top-level object is
/// `ok` and maps to the literal `true`. Anchoring to the first key avoids a
/// false positive on a nested `ok` (e.g. `{"data":{"ok":true},"ok":false}`),
/// where a naive "find the first `ok` anywhere" scan would wrongly read ready.
/// A non-JSON body, a different first key, a missing key, or `"ok": false` all
/// return false.
fn body_reports_ok(body: &str) -> bool {
    let trimmed = body.trim_start();
    // Must look like a JSON object to be the bootstrap payload at all.
    let Some(after_brace) = trimmed.strip_prefix('{') else {
        return false;
    };
    // The first key must be exactly `"ok"`. Skip leading whitespace, then
    // require the opening quote, the key text, and the closing quote.
    let after_first_quote = match after_brace.trim_start().strip_prefix('"') {
        Some(rest) => rest,
        None => return false,
    };
    let key_end = match after_first_quote.find('"') {
        Some(idx) => idx,
        None => return false,
    };
    if &after_first_quote[..key_end] != "ok" {
        return false;
    }
    // Past the closing quote, expect optional whitespace, the colon, more
    // whitespace, then the literal `true` as the value token.
    let after_key = &after_first_quote[key_end + 1..];
    let after_colon = match after_key.trim_start().strip_prefix(':') {
        Some(rest) => rest.trim_start(),
        None => return false,
    };
    // The value token ends at the next structural character.
    let value_end = after_colon
        .find(|c: char| c == ',' || c == '}' || c.is_whitespace())
        .unwrap_or(after_colon.len());
    &after_colon[..value_end] == "true"
}

/// Path of the desktop serve log: `~/.magi/logs/desktop-serve.log`.
pub fn log_file_path(home: &Path) -> PathBuf {
    home.join(".magi").join("logs").join("desktop-serve.log")
}

/// Pick a free TCP port on loopback by binding to port 0 and reading back the
/// assigned port. The listener is dropped before returning, so the caller can
/// hand the port to `magi serve`. There is an inherent TOCTOU window, which is
/// acceptable for a single-user desktop launch.
pub fn pick_free_port() -> io::Result<u16> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::path::PathBuf;

    fn p(s: &str) -> PathBuf {
        PathBuf::from(s)
    }

    #[test]
    fn bundled_resource_binary_points_inside_onedir_tree() {
        // The onedir tree ships at <resource_dir>/magi/, with the executable at
        // <resource_dir>/magi/magi.
        let got = bundled_resource_binary(Path::new("/app/Resources"));
        assert_eq!(got, p("/app/Resources/magi/magi"));
    }

    #[test]
    fn resolve_prefers_bundled_when_it_exists() {
        let bundled = bundled_resource_binary(Path::new("/app/Resources"));
        let exp = bundled.clone();
        let found = resolve_magi_binary(
            Some(bundled.clone()),
            Some(p("/env/magi")),
            Some(Path::new("/home/u")),
            move |path| path == exp.as_path(),
            |_| Some(p("/usr/bin/magi")),
        );
        assert_eq!(found, Some(bundled));
    }

    #[test]
    fn resolve_falls_to_env_when_bundled_missing() {
        let bundled = bundled_resource_binary(Path::new("/app/Resources"));
        let found = resolve_magi_binary(
            Some(bundled),
            Some(p("/env/magi")),
            Some(Path::new("/home/u")),
            |path| path == Path::new("/env/magi"),
            |_| Some(p("/usr/bin/magi")),
        );
        assert_eq!(found, Some(p("/env/magi")));
    }

    #[test]
    fn resolve_falls_to_home_when_bundled_and_env_missing() {
        let home = Path::new("/home/u");
        let expected = home.join(".magi").join("bin").join("magi");
        let exp = expected.clone();
        let bundled = bundled_resource_binary(Path::new("/app/Resources"));
        let found = resolve_magi_binary(
            Some(bundled),
            Some(p("/env/magi")),
            Some(home),
            move |path| path == exp.as_path(),
            |_| Some(p("/usr/bin/magi")),
        );
        assert_eq!(found, Some(expected));
    }

    #[test]
    fn resolve_falls_to_path_when_nothing_exists() {
        let bundled = bundled_resource_binary(Path::new("/app/Resources"));
        let found = resolve_magi_binary(
            Some(bundled),
            Some(p("/env/magi")),
            Some(Path::new("/home/u")),
            |_| false,
            |name| {
                assert_eq!(name, "magi");
                Some(p("/usr/local/bin/magi"))
            },
        );
        assert_eq!(found, Some(p("/usr/local/bin/magi")));
    }

    #[test]
    fn resolve_returns_none_when_path_lookup_fails() {
        let found = resolve_magi_binary(None, None, None, |_| false, |_| None);
        assert_eq!(found, None);
    }

    #[test]
    fn resolve_first_existing_wins_over_later_candidates() {
        // env exists and home would too, but env comes first in the order.
        let home = Path::new("/home/u");
        let found = resolve_magi_binary(
            None,
            Some(p("/env/magi")),
            Some(home),
            |_| true, // everything "exists"
            |_| Some(p("/path/magi")),
        );
        assert_eq!(found, Some(p("/env/magi")));
    }

    #[test]
    fn is_ready_true_on_200_and_ok_true() {
        assert!(is_ready(r#"{"ok": true, "agentUrl": ""}"#, 200));
    }

    #[test]
    fn is_ready_handles_no_space_after_colon() {
        assert!(is_ready(r#"{"ok":true}"#, 200));
    }

    #[test]
    fn is_ready_false_on_non_200() {
        assert!(!is_ready(r#"{"ok": true}"#, 500));
        assert!(!is_ready(r#"{"ok": true}"#, 404));
    }

    #[test]
    fn is_ready_false_on_ok_false() {
        assert!(!is_ready(r#"{"ok": false}"#, 200));
    }

    #[test]
    fn is_ready_false_on_non_json_body() {
        assert!(!is_ready("Internal Server Error", 200));
        assert!(!is_ready("", 200));
    }

    #[test]
    fn is_ready_false_when_ok_key_absent() {
        assert!(!is_ready(r#"{"status": "up"}"#, 200));
    }

    #[test]
    fn is_ready_does_not_match_ok_substring_in_other_value() {
        // "ok" appearing only inside a string value must not flip readiness.
        assert!(!is_ready(r#"{"message": "not ok yet"}"#, 200));
    }

    #[test]
    fn is_ready_false_on_nested_ok_true_with_top_level_ok_false() {
        // FIX 3: a nested `ok:true` must NOT mask a top-level `ok:false`.
        assert!(!is_ready(r#"{"data":{"ok":true},"ok":false}"#, 200));
    }

    #[test]
    fn is_ready_false_when_ok_is_not_the_first_key() {
        // Only the FIRST top-level key is honoured; a later `ok:true` after a
        // different first key is not the bootstrap contract.
        assert!(!is_ready(r#"{"status":"up","ok":true}"#, 200));
    }

    #[test]
    fn is_ready_true_on_top_level_ok_true_with_trailing_fields() {
        // The canonical bootstrap payload: `ok` first, then other fields.
        assert!(is_ready(
            r#"{"ok": true, "agentUrl": "x", "data": {"ok": false}}"#,
            200
        ));
    }

    #[test]
    fn is_ready_tolerates_leading_whitespace_before_first_key() {
        assert!(is_ready("{  \"ok\" : true }", 200));
    }

    #[test]
    fn log_path_is_under_magi_logs() {
        let got = log_file_path(Path::new("/home/u"));
        assert_eq!(got, p("/home/u/.magi/logs/desktop-serve.log"));
    }

    #[test]
    fn pick_free_port_returns_nonzero() {
        let port = pick_free_port().expect("should bind a free port");
        assert_ne!(port, 0);
    }

    #[test]
    fn pick_free_port_two_calls_both_bind() {
        let a = pick_free_port().expect("first port");
        let b = pick_free_port().expect("second port");
        assert_ne!(a, 0);
        assert_ne!(b, 0);
        // Both ports must be independently bindable after selection.
        let la = TcpListener::bind(("127.0.0.1", a)).expect("rebind a");
        let lb = TcpListener::bind(("127.0.0.1", b)).expect("rebind b");
        drop(la);
        drop(lb);
    }

    #[test]
    fn path_lookup_closure_receives_bare_name() {
        let seen = RefCell::new(String::new());
        let _ = resolve_magi_binary(
            None,
            None,
            None,
            |_| false,
            |name| {
                *seen.borrow_mut() = name.to_string();
                None
            },
        );
        assert_eq!(*seen.borrow(), "magi");
    }
}

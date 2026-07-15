//! Managed-inference (OSS "Magi (managed)" tier) support for the desktop shell.
//!
//! When the user connects a Magi subscription, the desktop app receives a
//! gateway token via the `ai.openmagi://token?gw=...` deep link, persists it,
//! and injects the runtime's gateway-routing env so `magi serve` routes every
//! model call through Magi's api-proxy (which meters the subscription) instead
//! of requiring a BYO provider key.
//!
//! This module is pure (no `tauri`, no GUI) so `cargo test` covers the token
//! parsing, persistence path, and env construction on any host.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// Default public api-proxy base URL the managed tier routes through. Overridable
/// at build/run time via `MAGI_MANAGED_API_PROXY_URL` so staging/self-host can
/// repoint it without a code change.
pub const DEFAULT_MANAGED_API_PROXY_URL: &str = "https://api-proxy.clawy.pro";

/// Deep-link scheme + host the desktop app registers to receive the token.
pub const DEEP_LINK_SCHEME: &str = "ai.openmagi";
pub const DEEP_LINK_TOKEN_HOST: &str = "token";

/// Resolve the on-disk path where the managed gateway token is persisted.
/// Lives beside the runtime config under `~/.magi/managed-token` (honoring
/// `MAGI_CONFIG_DIR` when set, mirroring the runtime's config resolution).
pub fn managed_token_path(home: Option<&Path>, config_dir_override: Option<&str>) -> PathBuf {
    if let Some(dir) = config_dir_override.filter(|s| !s.is_empty()) {
        return Path::new(dir).join("managed-token");
    }
    let base = home.map(Path::to_path_buf).unwrap_or_else(|| PathBuf::from("."));
    base.join(".magi").join("managed-token")
}

/// Extract the `gw` token from an `ai.openmagi://token?gw=...` deep-link URL.
///
/// Returns `None` for any URL that is not the token deep link or lacks a
/// non-empty `gw` parameter. Kept dependency-free (manual parse) so the core
/// crate stays GUI/-and-URL-crate-free.
pub fn parse_deep_link_token(url: &str) -> Option<String> {
    let rest = url.strip_prefix(&format!("{DEEP_LINK_SCHEME}://"))?;
    // Expect `token?gw=<value>` (optionally with more params).
    let (host, query) = rest.split_once('?')?;
    // host may carry a trailing slash: `token/`.
    if host.trim_end_matches('/') != DEEP_LINK_TOKEN_HOST {
        return None;
    }
    for pair in query.split('&') {
        if let Some(value) = pair.strip_prefix("gw=") {
            let decoded = percent_decode(value);
            let trimmed = decoded.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
    }
    None
}

/// Scan a process argv list for an `ai.openmagi://token?gw=...` deep link and
/// return the token. On Windows/Linux the OS launches the app with the deep-link
/// URL as an argument (delivered to the running instance via single-instance);
/// this extracts it. Returns `None` when no argument is the token deep link.
pub fn extract_token_from_args<I, S>(args: I) -> Option<String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    args.into_iter()
        .find_map(|arg| parse_deep_link_token(arg.as_ref()))
}

/// Persist the managed gateway token to `path`, creating the parent directory as
/// needed. On Unix the file is written with `0o600` perms (owner-only) so the
/// token is not world-readable. Returns an error the caller can log; token
/// persistence failure should surface to the user, not panic.
pub fn write_managed_token(path: &Path, token: &str) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(path, token.trim().as_bytes())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = std::fs::Permissions::from_mode(0o600);
        std::fs::set_permissions(path, perms)?;
    }
    Ok(())
}

/// Build the environment overrides that switch `magi serve` into managed-routing
/// mode. Empty when there is no token (BYO-key / unconnected — byte-identical to
/// today). The proxy URL falls back to [`DEFAULT_MANAGED_API_PROXY_URL`].
pub fn managed_env_vars(
    token: Option<&str>,
    api_proxy_url_override: Option<&str>,
) -> BTreeMap<String, String> {
    let mut env = BTreeMap::new();
    let token = match token.map(str::trim).filter(|t| !t.is_empty()) {
        Some(t) => t,
        None => return env,
    };
    let api_base = api_proxy_url_override
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(DEFAULT_MANAGED_API_PROXY_URL);

    // The runtime's gateway-routing seam (engine/model_runner.py
    // `_model_api_base_kwargs`): route every model build through the api-proxy,
    // present the gateway token as `x-api-key`, and enable the turn-boundary
    // credit pre-check.
    env.insert("MAGI_LLM_API_BASE".to_string(), api_base.to_string());
    env.insert("MAGI_LLM_API_KEY".to_string(), token.to_string());
    env.insert("MAGI_LLM_API_HEADER".to_string(), "x-api-key".to_string());
    env.insert("MAGI_MANAGED_INFERENCE_ENABLED".to_string(), "1".to_string());
    env
}

/// Minimal percent-decoding for the token query value (`%XX` + `+`). The token
/// is URL-safe base16-ish, but the browser may still percent-encode it.
fn percent_decode(input: &str) -> String {
    let bytes = input.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                let hi = hex_val(bytes[i + 1]);
                let lo = hex_val(bytes[i + 2]);
                match (hi, lo) {
                    (Some(h), Some(l)) => {
                        out.push((h << 4) | l);
                        i += 3;
                    }
                    _ => {
                        out.push(bytes[i]);
                        i += 1;
                    }
                }
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b => {
                out.push(b);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn hex_val(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_gw_token_from_deep_link() {
        assert_eq!(
            parse_deep_link_token("ai.openmagi://token?gw=gw_abc123"),
            Some("gw_abc123".to_string())
        );
    }

    #[test]
    fn parses_token_with_trailing_slash_host_and_extra_params() {
        assert_eq!(
            parse_deep_link_token("ai.openmagi://token/?foo=1&gw=gw_xyz&bar=2"),
            Some("gw_xyz".to_string())
        );
    }

    #[test]
    fn percent_decodes_token_value() {
        assert_eq!(
            parse_deep_link_token("ai.openmagi://token?gw=gw%5Fabc"),
            Some("gw_abc".to_string())
        );
    }

    #[test]
    fn rejects_wrong_scheme_host_or_missing_gw() {
        assert_eq!(parse_deep_link_token("https://token?gw=x"), None);
        assert_eq!(parse_deep_link_token("ai.openmagi://other?gw=x"), None);
        assert_eq!(parse_deep_link_token("ai.openmagi://token?foo=x"), None);
        assert_eq!(parse_deep_link_token("ai.openmagi://token?gw="), None);
    }

    #[test]
    fn token_path_uses_home_dot_magi_by_default() {
        let p = managed_token_path(Some(Path::new("/home/u")), None);
        assert_eq!(p, PathBuf::from("/home/u/.magi/managed-token"));
    }

    #[test]
    fn token_path_honors_config_dir_override() {
        let p = managed_token_path(Some(Path::new("/home/u")), Some("/custom/cfg"));
        assert_eq!(p, PathBuf::from("/custom/cfg/managed-token"));
    }

    #[test]
    fn env_vars_empty_without_token() {
        assert!(managed_env_vars(None, None).is_empty());
        assert!(managed_env_vars(Some("   "), None).is_empty());
    }

    #[test]
    fn env_vars_route_through_proxy_with_token() {
        let env = managed_env_vars(Some("gw_1"), None);
        assert_eq!(env.get("MAGI_LLM_API_KEY").unwrap(), "gw_1");
        assert_eq!(env.get("MAGI_LLM_API_BASE").unwrap(), DEFAULT_MANAGED_API_PROXY_URL);
        assert_eq!(env.get("MAGI_LLM_API_HEADER").unwrap(), "x-api-key");
        assert_eq!(env.get("MAGI_MANAGED_INFERENCE_ENABLED").unwrap(), "1");
    }

    #[test]
    fn env_vars_respect_api_proxy_override() {
        let env = managed_env_vars(Some("gw_1"), Some("https://staging-proxy.example"));
        assert_eq!(env.get("MAGI_LLM_API_BASE").unwrap(), "https://staging-proxy.example");
    }

    #[test]
    fn extracts_token_from_argv_deep_link() {
        let args = vec![
            "magi-desktop".to_string(),
            "ai.openmagi://token?gw=gw_from_args".to_string(),
        ];
        assert_eq!(extract_token_from_args(args), Some("gw_from_args".to_string()));
    }

    #[test]
    fn extract_returns_none_without_deep_link_arg() {
        let args = vec!["magi-desktop".to_string(), "--flag".to_string()];
        assert_eq!(extract_token_from_args(args), None);
    }

    #[test]
    fn write_then_read_round_trips_token() {
        let dir = std::env::temp_dir().join(format!("magi-managed-test-{}", std::process::id()));
        let path = dir.join("managed-token");
        write_managed_token(&path, "  gw_roundtrip  ").unwrap();
        let read = std::fs::read_to_string(&path).unwrap();
        assert_eq!(read, "gw_roundtrip"); // trimmed on write
        let _ = std::fs::remove_dir_all(&dir);
    }
}

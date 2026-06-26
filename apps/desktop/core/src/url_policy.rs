//! URL navigation policy for the desktop shell.
//!
//! Self-host simplification of the hosted Electron url-policy: a navigation
//! target is either allowed to load in-window (the loopback origin we launched)
//! or it is external (open in the system browser), or it is unparseable.
//! OAuth (Composio) completes in the system browser and the dashboard polls,
//! so there is no in-app popup category.

/// Classification of a navigation target.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UrlClass {
    /// The exact loopback origin we launched (`http://127.0.0.1:<port>`).
    /// Allowed to load in the main window.
    InApp,
    /// Any other valid URL. Open in the system browser, block in-window.
    External,
    /// The target could not be parsed as a URL.
    Invalid,
}

/// Classify `target` for a server bound on loopback `port`.
///
/// `InApp` iff the scheme is `http`, the host is exactly `127.0.0.1`, and the
/// port matches `port`. We deliberately do NOT accept `localhost`: the desktop
/// shell binds and launches the window at `127.0.0.1:<port>` (the loopback
/// bind from FIX 1), so `127.0.0.1` is the exact launched origin. `localhost`
/// can resolve differently (IPv6 `::1`, hosts-file overrides) and is not the
/// origin we loaded, so it is treated as `External`. Everything else parseable
/// is `External`. Anything that does not parse as `scheme://host[:port]/...`
/// is `Invalid`.
pub fn classify(target: &str, port: u16) -> UrlClass {
    let parsed = match ParsedUrl::parse(target) {
        Some(p) => p,
        None => return UrlClass::Invalid,
    };

    if parsed.scheme == "http" && parsed.host == "127.0.0.1" && parsed.port == Some(port) {
        UrlClass::InApp
    } else {
        UrlClass::External
    }
}

/// Minimal absolute-URL parser. We only need scheme, host, and explicit port.
/// We deliberately avoid a URL crate so this module stays dependency-free.
struct ParsedUrl<'a> {
    scheme: String,
    host: &'a str,
    port: Option<u16>,
}

impl<'a> ParsedUrl<'a> {
    fn parse(input: &'a str) -> Option<ParsedUrl<'a>> {
        let input = input.trim();
        if input.is_empty() {
            return None;
        }
        // Require an explicit scheme separator. This rejects bare strings like
        // "not a url" and relative paths, which we treat as Invalid.
        let (scheme_raw, rest) = input.split_once("://")?;
        if scheme_raw.is_empty() {
            return None;
        }
        let scheme = scheme_raw.to_ascii_lowercase();
        if !scheme
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '+' || c == '-' || c == '.')
        {
            return None;
        }

        // Authority ends at the first '/', '?', or '#'.
        let authority_end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
        let authority = &rest[..authority_end];
        if authority.is_empty() {
            return None;
        }

        // Strip any userinfo ("user:pass@host").
        let host_port = match authority.rsplit_once('@') {
            Some((_, hp)) => hp,
            None => authority,
        };
        if host_port.is_empty() {
            return None;
        }

        // Split host and optional explicit port. IPv6 bracket form is not used
        // by our loopback targets, so a single ':' split is sufficient here.
        let (host, port) = match host_port.rsplit_once(':') {
            Some((h, p)) => {
                if h.is_empty() {
                    return None;
                }
                let parsed_port: u16 = p.parse().ok()?;
                (h, Some(parsed_port))
            }
            None => (host_port, None),
        };

        Some(ParsedUrl { scheme, host, port })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PORT: u16 = 51763;

    #[test]
    fn loopback_dashboard_is_in_app_via_127() {
        let url = format!("http://127.0.0.1:{PORT}/dashboard");
        assert_eq!(classify(&url, PORT), UrlClass::InApp);
    }

    #[test]
    fn localhost_on_launch_port_is_external() {
        // FIX 6: `localhost` is NOT the launched origin (we bind 127.0.0.1),
        // so even on the launch port it must open externally, not in-window.
        let url = format!("http://localhost:{PORT}/dashboard");
        assert_eq!(classify(&url, PORT), UrlClass::External);
    }

    #[test]
    fn in_app_allows_query_and_fragment() {
        let url = format!("http://127.0.0.1:{PORT}/dashboard?tab=chat#x");
        assert_eq!(classify(&url, PORT), UrlClass::InApp);
    }

    #[test]
    fn wrong_port_is_external() {
        let url = format!("http://127.0.0.1:{}/dashboard", PORT + 1);
        assert_eq!(classify(&url, PORT), UrlClass::External);
    }

    #[test]
    fn loopback_different_port_is_external() {
        // A loopback host but a different explicit port must not load in-window.
        let url = "http://localhost:9999/dashboard";
        assert_eq!(classify(url, PORT), UrlClass::External);
    }

    #[test]
    fn https_loopback_is_external() {
        // Only http loopback is in-app; https is treated as external.
        let url = format!("https://127.0.0.1:{PORT}/dashboard");
        assert_eq!(classify(&url, PORT), UrlClass::External);
    }

    #[test]
    fn github_is_external() {
        assert_eq!(classify("https://github.com", PORT), UrlClass::External);
    }

    #[test]
    fn external_host_on_matching_port_is_external() {
        // Host check must reject non-loopback hosts even on the launch port.
        let url = format!("http://evil.example:{PORT}/dashboard");
        assert_eq!(classify(&url, PORT), UrlClass::External);
    }

    #[test]
    fn not_a_url_is_invalid() {
        assert_eq!(classify("not a url", PORT), UrlClass::Invalid);
    }

    #[test]
    fn empty_is_invalid() {
        assert_eq!(classify("", PORT), UrlClass::Invalid);
        assert_eq!(classify("   ", PORT), UrlClass::Invalid);
    }

    #[test]
    fn bare_path_is_invalid() {
        // No scheme means we cannot trust it as an absolute target.
        assert_eq!(classify("/dashboard", PORT), UrlClass::Invalid);
    }
}

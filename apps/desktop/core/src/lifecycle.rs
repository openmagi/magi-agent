//! Startup lifecycle state machine for the desktop shell.
//!
//! The GUI spawns `magi serve`, then polls the bootstrap URL. This pure state
//! machine drives that wait so the GUI glue stays thin: given the current
//! phase, the latest health result, and elapsed time, `next` returns the phase
//! to render. Crossing the deadline before health succeeds yields `Failed`.

use std::time::Duration;

/// Phases of bringing the local runtime up.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Phase {
    /// The child process is being launched. No health poll has happened yet.
    Spawning,
    /// We are polling the bootstrap URL. `attempts` counts failed polls so far.
    WaitingForHealth { attempts: u32 },
    /// Bootstrap reported ready. The webview can load the dashboard.
    Ready,
    /// We gave up: either the deadline elapsed or the child died. Carries a
    /// human-readable reason for the error page.
    Failed(String),
}

/// Compute the next phase.
///
/// Rules:
///   * `Ready` and `Failed` are terminal: they are returned unchanged.
///   * If `health_ok`, the next phase is `Ready` (even from `Spawning`).
///   * Else if `elapsed >= deadline`, the next phase is `Failed` with a
///     timeout reason.
///   * Else we are still waiting: from `Spawning` we enter
///     `WaitingForHealth { attempts: 0 }`; from `WaitingForHealth { n }` we
///     advance to `WaitingForHealth { n + 1 }`.
pub fn next(phase: &Phase, health_ok: bool, elapsed: Duration, deadline: Duration) -> Phase {
    match phase {
        Phase::Ready => Phase::Ready,
        Phase::Failed(reason) => Phase::Failed(reason.clone()),
        Phase::Spawning | Phase::WaitingForHealth { .. } => {
            if health_ok {
                return Phase::Ready;
            }
            if elapsed >= deadline {
                return Phase::Failed(format!(
                    "magi serve did not become ready within {} seconds",
                    deadline.as_secs()
                ));
            }
            match phase {
                Phase::Spawning => Phase::WaitingForHealth { attempts: 0 },
                Phase::WaitingForHealth { attempts } => Phase::WaitingForHealth {
                    attempts: attempts.saturating_add(1),
                },
                // Unreachable: outer match already narrowed the variants.
                _ => phase.clone(),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const DEADLINE: Duration = Duration::from_secs(30);

    #[test]
    fn health_ok_from_spawning_goes_ready() {
        let got = next(&Phase::Spawning, true, Duration::from_secs(1), DEADLINE);
        assert_eq!(got, Phase::Ready);
    }

    #[test]
    fn health_ok_from_waiting_goes_ready() {
        let got = next(
            &Phase::WaitingForHealth { attempts: 3 },
            true,
            Duration::from_secs(5),
            DEADLINE,
        );
        assert_eq!(got, Phase::Ready);
    }

    #[test]
    fn spawning_to_waiting_zero_on_first_failed_poll() {
        let got = next(&Phase::Spawning, false, Duration::from_secs(1), DEADLINE);
        assert_eq!(got, Phase::WaitingForHealth { attempts: 0 });
    }

    #[test]
    fn repeated_false_under_deadline_increments_attempts() {
        let got = next(
            &Phase::WaitingForHealth { attempts: 4 },
            false,
            Duration::from_secs(10),
            DEADLINE,
        );
        assert_eq!(got, Phase::WaitingForHealth { attempts: 5 });
    }

    #[test]
    fn past_deadline_without_health_fails() {
        let got = next(
            &Phase::WaitingForHealth { attempts: 9 },
            false,
            Duration::from_secs(31),
            DEADLINE,
        );
        match got {
            Phase::Failed(reason) => assert!(reason.contains("30 seconds")),
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn exactly_at_deadline_fails() {
        let got = next(
            &Phase::WaitingForHealth { attempts: 1 },
            false,
            DEADLINE,
            DEADLINE,
        );
        assert!(matches!(got, Phase::Failed(_)));
    }

    #[test]
    fn ready_is_terminal() {
        let got = next(&Phase::Ready, false, Duration::from_secs(99), DEADLINE);
        assert_eq!(got, Phase::Ready);
    }

    #[test]
    fn failed_is_terminal() {
        let start = Phase::Failed("boom".to_string());
        let got = next(&start, true, Duration::from_secs(1), DEADLINE);
        assert_eq!(got, Phase::Failed("boom".to_string()));
    }

    #[test]
    fn deadline_takes_precedence_only_when_health_false() {
        // Even past the deadline, a successful health check still wins.
        let got = next(
            &Phase::WaitingForHealth { attempts: 50 },
            true,
            Duration::from_secs(120),
            DEADLINE,
        );
        assert_eq!(got, Phase::Ready);
    }
}

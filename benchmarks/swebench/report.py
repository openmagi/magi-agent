from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Summary:
    resolved: int
    attempted: int
    resolved_pct: float
    delta_resolved: int = 0
    newly_resolved: list[str] = field(default_factory=list)
    regressed: list[str] = field(default_factory=list)


def summarize(
    *,
    resolved_ids: set[str],
    attempted_ids: set[str],
    baseline_resolved_ids: set[str] | None = None,
) -> Summary:
    attempted = len(attempted_ids)
    resolved = len(resolved_ids)
    pct = round(100.0 * resolved / attempted, 1) if attempted else 0.0
    if baseline_resolved_ids is None:
        return Summary(resolved=resolved, attempted=attempted, resolved_pct=pct)
    newly = sorted(resolved_ids - baseline_resolved_ids)
    regressed = sorted(baseline_resolved_ids - resolved_ids)
    return Summary(
        resolved=resolved,
        attempted=attempted,
        resolved_pct=pct,
        delta_resolved=resolved - len(baseline_resolved_ids),
        newly_resolved=newly,
        regressed=regressed,
    )

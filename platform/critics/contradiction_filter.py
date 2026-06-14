"""
Contradiction filter — P2 standalone utility (no caller until P4).

Deduplicates and rate-limits contradiction findings before they reach
the human queue, preventing operator flooding (RT-29).
"""

from __future__ import annotations

_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
_DEFAULT_MAX: dict[str, int] = {"HIGH": 5, "MEDIUM": 10, "LOW": 20}


def filter_contradictions(
    findings: list[dict],
    *,
    window_seconds: int = 300,
    max_per_severity: dict[str, int] | None = None,
) -> list[dict]:
    """
    Deduplicate and rate-limit contradiction findings.

    Each finding dict must have:
        finding_id:  str
        severity:    str   — "HIGH" | "MEDIUM" | "LOW"
        kind:        str   — contradiction kind (e.g. "nexus_vs_episodic")
        detected_at: float — unix timestamp

    Dedup rule:  same (finding_id, kind) → keep only the most recent within window.
    Rate-limit:  per severity tier, keep at most max_per_severity[severity] findings.
                 Default: HIGH=5, MEDIUM=10, LOW=20.

    Returns the filtered list, sorted by severity (HIGH first) then detected_at.
    """
    limits = {**_DEFAULT_MAX, **(max_per_severity or {})}

    # Group by (finding_id, kind), keep the most recent per key within the window.
    # Two findings with the same key but detected_at more than window_seconds apart
    # are treated as separate incidents — both survive.
    deduped: dict[tuple, dict] = {}
    for f in findings:
        key = (f["finding_id"], f["kind"])
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = f
        else:
            time_gap = abs(f["detected_at"] - existing["detected_at"])
            if time_gap > window_seconds:
                # Separate incidents: keep both — can't collapse into a single slot.
                # Store under a unique key to preserve both.
                deduped[(f["finding_id"], f["kind"], f["detected_at"])] = f
            elif f["detected_at"] > existing["detected_at"]:
                deduped[key] = f

    candidates = list(deduped.values())

    # Rate-limit per severity tier.
    by_severity: dict[str, list[dict]] = {}
    for f in candidates:
        sev = f.get("severity", "LOW")
        by_severity.setdefault(sev, []).append(f)

    result = []
    for sev, group in by_severity.items():
        # Within each tier, keep the most recent N findings.
        group_sorted = sorted(group, key=lambda f: f["detected_at"], reverse=True)
        limit = limits.get(sev, _DEFAULT_MAX.get(sev, 20))
        result.extend(group_sorted[:limit])

    # Sort output: HIGH first, then by detected_at ascending within each tier.
    result.sort(key=lambda f: (_SEVERITY_ORDER.get(f.get("severity", "LOW"), 99), f["detected_at"]))
    return result

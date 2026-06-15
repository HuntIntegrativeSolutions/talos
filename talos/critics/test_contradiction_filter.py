from talos.critics.contradiction_filter import filter_contradictions


def test_dedup_same_finding_keeps_most_recent():
    base_time = 1000.0
    findings = [
        {"finding_id": "F1", "kind": "nexus_vs_episodic", "severity": "HIGH",
         "detected_at": base_time + i}
        for i in range(10)
    ]
    result = filter_contradictions(findings, window_seconds=300)
    assert len(result) == 1
    assert result[0]["detected_at"] == base_time + 9


def test_rate_limit_per_severity():
    low_findings = [
        {"finding_id": f"L{i}", "kind": "k", "severity": "LOW", "detected_at": float(i)}
        for i in range(100)
    ]
    high_findings = [
        {"finding_id": f"H{i}", "kind": "k", "severity": "HIGH", "detected_at": float(i)}
        for i in range(6)
    ]
    result = filter_contradictions(low_findings + high_findings)
    lows = [f for f in result if f["severity"] == "LOW"]
    highs = [f for f in result if f["severity"] == "HIGH"]
    assert len(lows) <= 20
    assert len(highs) <= 5


def test_outside_window_not_deduped():
    findings = [
        {"finding_id": "F1", "kind": "nexus_vs_episodic", "severity": "MEDIUM",
         "detected_at": 0.0},
        {"finding_id": "F1", "kind": "nexus_vs_episodic", "severity": "MEDIUM",
         "detected_at": 400.0},  # 400s apart — outside 300s window
    ]
    result = filter_contradictions(findings, window_seconds=300)
    assert len(result) == 2


def test_severity_sort_order():
    findings = [
        {"finding_id": "L1", "kind": "k", "severity": "LOW",    "detected_at": 1.0},
        {"finding_id": "H1", "kind": "k", "severity": "HIGH",   "detected_at": 2.0},
        {"finding_id": "M1", "kind": "k", "severity": "MEDIUM", "detected_at": 3.0},
    ]
    result = filter_contradictions(findings)
    severities = [f["severity"] for f in result]
    assert severities == ["HIGH", "MEDIUM", "LOW"]

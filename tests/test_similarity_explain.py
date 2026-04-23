from mlss_monitor.incident_grouper import explain_similarity


def test_explain_returns_top_matching_axes():
    # Two vectors that agree strongly on severity-critical (idx 28) + ML method (idx 21)
    a = [0.0] * 32
    b = [0.0] * 32
    a[28] = b[28] = 1.0  # both critical
    a[21] = b[21] = 1.0  # both ML-detected
    explanation = explain_similarity(a, b)
    assert "severity" in explanation.lower() or "critical" in explanation.lower()
    assert "ml" in explanation.lower() or "method" in explanation.lower()


def test_explain_empty_vectors_returns_fallback():
    assert explain_similarity([], []) == "No comparable signal."


def test_explain_unequal_lengths_returns_fallback():
    assert explain_similarity([1.0] * 32, [1.0] * 31) == "No comparable signal."

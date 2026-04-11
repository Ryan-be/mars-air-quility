"""Tests for /api/inferences/* endpoints and inference_engine event_category()."""
from database.db_logger import save_inference


# ---------------------------------------------------------------------------
# event_category unit tests
# ---------------------------------------------------------------------------

def test_event_category_returns_alert_for_tvoc_spike(app_client):
    from mlss_monitor.inference_engine import event_category
    assert event_category("tvoc_spike") == "alert"


def test_event_category_returns_attribution_for_fingerprint_match(app_client):
    from mlss_monitor.inference_engine import event_category
    assert event_category("fingerprint_match") == "attribution"


def test_event_category_returns_attribution_for_ml_learned_source(app_client):
    from mlss_monitor.inference_engine import event_category
    assert event_category("ml_learned_personal_care") == "attribution"
    assert event_category("ml_learned_cooking") == "attribution"
    assert event_category("ml_learned_biological_offgas") == "attribution"


def test_event_category_returns_pattern_for_annotation_context(app_client):
    from mlss_monitor.inference_engine import event_category
    assert event_category("annotation_context_user_range") == "pattern"


def test_event_category_returns_anomaly_for_anomaly_prefix(app_client):
    from mlss_monitor.inference_engine import event_category
    assert event_category("anomaly_combustion_signature") == "anomaly"


def test_event_category_returns_other_for_unknown_type(app_client):
    from mlss_monitor.inference_engine import event_category
    assert event_category("some_random_event") == "other"


# ---------------------------------------------------------------------------
# CATEGORIES unit tests
# ---------------------------------------------------------------------------

def test_categories_includes_all_base_categories(app_client):
    from mlss_monitor.inference_engine import CATEGORIES
    expected_keys = {"alert", "warning", "summary", "pattern", "anomaly", "attribution", "other"}
    assert expected_keys.issubset(CATEGORIES.keys()), (
        f"Missing base categories. Expected {expected_keys}, got {set(CATEGORIES.keys())}"
    )


def test_categories_includes_attribution(app_client):
    from mlss_monitor.inference_engine import CATEGORIES
    assert "attribution" in CATEGORIES
    assert CATEGORIES["attribution"] == "Attribution"


# ---------------------------------------------------------------------------
# /api/inferences/categories endpoint tests
# ---------------------------------------------------------------------------

def test_categories_endpoint_returns_base_categories(app_client):
    client, _ = app_client
    resp = client.get("/api/inferences/categories")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "alert" in data
    assert "attribution" in data
    assert data["alert"] == "Alerts"
    assert data["attribution"] == "Attribution"


def test_categories_endpoint_includes_saved_fingerprint_sources(app_client, db):
    client, _ = app_client
    save_inference(
        event_type="ml_learned_personal_care",
        title="Personal care detected",
        description="ML detected personal care products",
        action="none",
        severity="info",
        confidence=0.72,
        evidence={"attribution_source": "personal_care", "attribution_confidence": 0.72},
    )
    save_inference(
        event_type="ml_learned_cooking",
        title="Cooking detected",
        description="ML detected cooking",
        action="none",
        severity="info",
        confidence=0.65,
        evidence={"attribution_source": "cooking", "attribution_confidence": 0.65},
    )
    resp = client.get("/api/inferences/categories")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "personal_care" in data, f"personal_care not in categories: {list(data.keys())}"
    assert "cooking" in data
    assert data["personal_care"] == "Personal Care"


def test_categories_endpoint_omits_sources_already_in_base_categories(app_client, db):
    client, _ = app_client
    save_inference(
        event_type="tvoc_spike",
        title="TVOC spike",
        description="desc",
        action="act",
        severity="warning",
        confidence=0.9,
        evidence={},
    )
    resp = client.get("/api/inferences/categories")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "alert" not in data or data.get("alert") == "Alerts"


# ---------------------------------------------------------------------------
# /api/inferences endpoint tests
# ---------------------------------------------------------------------------

def test_list_inferences_includes_category_field(app_client, db):
    client, _ = app_client
    save_inference(
        event_type="tvoc_spike",
        title="TVOC spike",
        description="desc",
        action="act",
        severity="warning",
        confidence=0.9,
        evidence={},
    )
    resp = client.get("/api/inferences")
    assert resp.status_code == 200
    rows = resp.get_json()
    assert len(rows) >= 1
    inf = next((r for r in rows if r["event_type"] == "tvoc_spike"), None)
    assert inf is not None
    assert inf["category"] == "alert"


def test_list_inferences_category_is_attribution_for_ml_learned(app_client, db):
    client, _ = app_client
    save_inference(
        event_type="ml_learned_personal_care",
        title="Personal care",
        description="desc",
        action="act",
        severity="info",
        confidence=0.72,
        evidence={"attribution_source": "personal_care", "attribution_confidence": 0.72},
    )
    resp = client.get("/api/inferences")
    assert resp.status_code == 200
    rows = resp.get_json()
    inf = next((r for r in rows if r["event_type"] == "ml_learned_personal_care"), None)
    assert inf is not None
    assert inf["category"] == "attribution"


def test_list_inferences_filter_by_base_category(app_client, db):
    client, _ = app_client
    save_inference(
        event_type="tvoc_spike",
        title="TVOC spike",
        description="desc",
        action="act",
        severity="warning",
        confidence=0.9,
        evidence={},
    )
    save_inference(
        event_type="ml_learned_personal_care",
        title="Personal care",
        description="desc",
        action="act",
        severity="info",
        confidence=0.72,
        evidence={"attribution_source": "personal_care", "attribution_confidence": 0.72},
    )
    resp = client.get("/api/inferences?category=alert")
    assert resp.status_code == 200
    rows = resp.get_json()
    for r in rows:
        assert r["category"] == "alert", f"Non-alert row in alert filter: {r['category']}"


def test_list_inferences_filter_by_fingerprint_source(app_client, db):
    client, _ = app_client
    save_inference(
        event_type="fingerprint_match",
        title="Personal care match",
        description="desc",
        action="act",
        severity="info",
        confidence=0.8,
        evidence={"attribution_source": "personal_care", "attribution_confidence": 0.8},
    )
    save_inference(
        event_type="fingerprint_match",
        title="Cooking match",
        description="desc",
        action="act",
        severity="info",
        confidence=0.75,
        evidence={"attribution_source": "cooking", "attribution_confidence": 0.75},
    )
    resp = client.get("/api/inferences?category=personal_care")
    assert resp.status_code == 200
    rows = resp.get_json()
    assert len(rows) >= 1
    for r in rows:
        src = (r.get("evidence") or {}).get("attribution_source")
        assert src == "personal_care", f"Non personal_care row in personal_care filter: {src}"


def test_list_inferences_filter_by_attribution_category(app_client, db):
    client, _ = app_client
    save_inference(
        event_type="ml_learned_cooking",
        title="Cooking ML",
        description="desc",
        action="act",
        severity="info",
        confidence=0.68,
        evidence={"attribution_source": "cooking", "attribution_confidence": 0.68},
    )
    resp = client.get("/api/inferences?category=attribution")
    assert resp.status_code == 200
    rows = resp.get_json()
    assert len(rows) >= 1
    for r in rows:
        assert r["category"] == "attribution", f"Non-attribution row in attribution filter: {r['category']}"

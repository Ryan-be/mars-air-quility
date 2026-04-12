"""UI structure tests for the dashboard page.

These tests assert that AstroUXDS CSS classes have been applied to the
dashboard HTML. They fail until Task 3 (HTML update) is complete.
"""
from bs4 import BeautifulSoup


def test_stat_grid_has_astro_class(app_client):
    client, _ = app_client
    resp = client.get("/")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find(class_="stat-grid") is not None, "stat-grid wrapper missing"


def test_stat_cards_have_astro_status_bar_class(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    cards = soup.find_all(class_="stat-card")
    assert len(cards) >= 6, f"Expected at least 6 stat cards, got {len(cards)}"
    for card in cards:
        assert "astro-card" in card.get("class", []), (
            f"stat-card missing 'astro-card' class: {card.get('class')}"
        )


def test_insight_cards_have_astro_class(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    cards = soup.find_all(class_="insight-card")
    assert len(cards) >= 6, f"Expected at least 6 insight cards, got {len(cards)}"
    for card in cards:
        assert "astro-card" in card.get("class", []), (
            f"insight-card missing 'astro-card' class: {card.get('class')}"
        )


def test_health_card_has_astro_class(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    health = soup.find(class_="health-card")
    assert health is not None, "health-card div missing"
    assert "astro-card" in health.get("class", []), "health-card missing 'astro-card' class"


def test_inference_feed_exists(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find(id="inferenceFeed") is not None, "#inferenceFeed missing"


def test_inference_dialog_preserved(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    dlg = soup.find("dialog", {"id": "inferenceDialog"})
    assert dlg is not None, "inferenceDialog missing"
    assert soup.find(id="infTitle") is not None, "#infTitle missing"
    assert soup.find(id="infEvidence") is not None, "#infEvidence missing"
    assert soup.find(id="infSparklineChart") is not None, "#infSparklineChart missing"


def test_sensor_dialog_preserved(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find("dialog", {"id": "sensorDialog"}) is not None, "sensorDialog missing"
    assert soup.find(id="sdSensor") is not None, "#sdSensor missing"
    assert soup.find(id="sdCurrent") is not None, "#sdCurrent missing"


def test_forecast_dialogs_preserved(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find("dialog", {"id": "forecastDialog"}) is not None
    assert soup.find("dialog", {"id": "forecastDailyDialog"}) is not None
    assert soup.find(id="fdTemp") is not None, "#fdTemp missing"
    assert soup.find(id="fddHigh") is not None, "#fddHigh missing"


def test_all_sensor_value_ids_preserved(app_client):
    """JS reads these IDs; they must survive the HTML rewrite."""
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    required_ids = [
        "tempValue", "humValue", "eco2Value", "tvocValue",
        "gasCoValue", "gasNo2SubValue", "gasNh3SubValue", "gasTrend",
        "pm25Value", "pm1SubValue", "pm10SubValue", "pmStaleHint",
        "aqValue", "aqSub", "dewValue", "hiValue",
        "co2AlertValue", "co2AlertSub", "vpdValue", "tttValue",
        "aht20Status", "sgp30Status", "pmStatus", "mics6814Status",
        "plugStatus", "cpuUsage", "memoryUsage", "diskUsage",
        "dbSize", "uptime", "serviceUptime",
        "inferenceCount", "inferenceFeed", "inferenceFilters",
        "infSeverity", "infTime", "infConfidence",
        "infDescription", "infEvidence", "infAction",
        "infNotes", "infSaveNote", "infThresholds",
    ]
    for el_id in required_ids:
        assert soup.find(id=el_id) is not None, f"#{el_id} missing from dashboard HTML"


def test_section_headings_use_astro_class(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    headings = soup.find_all(class_="section-heading")
    assert len(headings) >= 3, "Expected at least 3 section headings"


def test_inference_filter_buttons_present(app_client):
    client, _ = app_client
    resp = client.get("/")
    soup = BeautifulSoup(resp.data, "html.parser")
    filters = soup.find_all(class_="inf-filter")
    categories = [b.get("data-category") for b in filters]
    for cat in ["all", "alert", "warning", "pattern", "anomaly", "attribution", "summary"]:
        assert cat in categories, f"inf-filter for category '{cat}' missing"

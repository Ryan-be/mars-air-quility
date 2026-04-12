"""UI structure tests for the history page."""
from bs4 import BeautifulSoup


def test_history_page_loads(app_client):
    client, _ = app_client
    resp = client.get("/history")
    assert resp.status_code == 200


def test_tab_bar_has_six_tabs(app_client):
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    tabs = soup.find_all(class_="tab-btn")
    assert len(tabs) == 6, f"Expected 6 tab buttons, got {len(tabs)}"


def test_tab_panels_all_present(app_client):
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    panel_ids = ["tab-climate", "tab-air-quality", "tab-particulate",
                 "tab-environment", "tab-correlation", "tab-detections"]
    for pid in panel_ids:
        assert soup.find(id=pid) is not None, f"#{pid} panel missing"


def test_plotly_div_ids_preserved(app_client):
    """Plotly renders into these divs — IDs must never change."""
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    plot_ids = [
        "tempPlot", "humPlot",
        "eco2Plot", "tvocPlot", "gasTimeSeriesPlot",
        "pmTimeSeriesPlot",
        "tempOverlayPlot", "humOverlayPlot", "absHumPlot",
        "dewPointPlot", "fanStatePlot", "vpdPlot",
        "corrBrushPlot",
        "tvocEco2ScatterPlot", "tempHumScatterPlot",
        "pm25TvocScatterPlot", "pm25Eco2ScatterPlot",
        "diHeatmap", "diBandsChart",
    ]
    for pid in plot_ids:
        assert soup.find(id=pid) is not None, f"Plotly div #{pid} missing"


def test_range_tag_ml_elements_preserved(app_client):
    """These IDs drive the ML tagging workflow — must be preserved."""
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    ml_ids = [
        "corrRangeTagSelect",
        "corrCreateRangeInferenceBtn",
        "corrRangeInferenceStatus",
        "corrRangeTagSection",
        "corrAnalysisPanel",
        "corrInferenceGrid",
        "corrBrushPlot",
        "corrResetBtn",
        "corrRangeLabel",
        "corrShowDetections",
    ]
    for el_id in ml_ids:
        assert soup.find(id=el_id) is not None, f"ML element #{el_id} missing"


def test_inference_dialog_in_history(app_client):
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    dlg = soup.find("dialog", {"id": "inferenceDialog"})
    assert dlg is not None, "inferenceDialog missing from history page"
    assert soup.find(id="infTagsList") is not None, "#infTagsList missing"
    assert soup.find(id="infTagSelect") is not None, "#infTagSelect missing"
    assert soup.find(id="infAddTag") is not None, "#infAddTag missing"


def test_detection_tab_di_ids_preserved(app_client):
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    di_ids = [
        "diPeriodSummary", "diTrendIndicators", "diAttributionBreakdown",
        "diDonutChart", "diFingerprints", "diFingerprintCards",
        "diHeatmapSection", "diPatternSentence",
        "diBandsSection", "diToggles",
        "diInferenceFeed", "diInferenceFilters",
    ]
    for el_id in di_ids:
        assert soup.find(id=el_id) is not None, f"DI element #{el_id} missing"


def test_tab_buttons_have_astro_pill_class(app_client):
    """Fails until history.html gets the astro-tab class added."""
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    tabs = soup.find_all(class_="tab-btn")
    for tab in tabs:
        assert "astro-tab" in tab.get("class", []), (
            f"tab-btn missing 'astro-tab' class: {tab.get('class')}"
        )


def test_channel_chips_present(app_client):
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    chips = soup.find_all(class_="channel-chip")
    assert len(chips) >= 8, f"Expected at least 8 channel chips, got {len(chips)}"


def test_range_select_in_topbar(app_client):
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    assert soup.find(id="range") is not None, "#range select missing"

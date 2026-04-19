"""UI structure tests for the history page."""
from bs4 import BeautifulSoup


def test_history_page_loads(app_client):
    client, _ = app_client
    resp = client.get("/history")
    assert resp.status_code == 200


def test_tab_bar_has_six_tabs(app_client):
    """The history page's astrouxds tab-bar must expose exactly six tabs.

    Post migration the template uses the `<rux-tab>` web component instead of
    `<button class="tab-btn">`; BeautifulSoup's HTML parser lowercases custom
    element names, so we look for the raw tag here.
    """
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    tabs = soup.find_all("rux-tab")
    assert len(tabs) == 6, f"Expected 6 rux-tab elements, got {len(tabs)}"


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


def test_inference_panel_in_history(app_client):
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    panel = soup.find(id="inferencePanel")
    assert panel is not None, "inferencePanel missing from history page"
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


def test_tab_buttons_have_expected_ids(app_client):
    """Post astrouxds migration: verify every `<rux-tab>` keeps its
    `tab-btn-<panel>` id so the JS tab controller can find it."""
    client, _ = app_client
    resp = client.get("/history")
    soup = BeautifulSoup(resp.data, "html.parser")
    expected_ids = {
        "tab-btn-climate", "tab-btn-air-quality", "tab-btn-particulate",
        "tab-btn-environment", "tab-btn-correlation", "tab-btn-detections",
    }
    tab_ids = {t.get("id") for t in soup.find_all("rux-tab")}
    missing = expected_ids - tab_ids
    assert not missing, f"rux-tab elements missing expected ids: {missing}"


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

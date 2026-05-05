from pathlib import Path

DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "PLANT_GROW_UNIT_ARCHITECTURE.md"


def test_doc_exists(): assert DOC.exists()


def test_doc_covers_dev_topics():
    text = DOC.read_text().lower()
    for topic in [
        "websocket", "bearer token", "enrollment key",
        "contracts", "package", "abc", "sensor",
        "pid", "soak window", "buffer", "telemetry_id",
    ]:
        assert topic in text, f"missing topic: {topic}"


def test_doc_links_to_spec():
    text = DOC.read_text()
    assert "2026-05-03-plant-grow-unit-system-design.md" in text

from pathlib import Path

DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "PLANT_GROW_UNIT_USAGE.md"


def test_doc_exists():
    assert DOC.exists()


def test_doc_covers_user_topics():
    text = DOC.read_text().lower()
    for topic in ["identify", "water now", "schedule", "soak window",
                  "phase", "calibrat", "offline"]:
        assert topic in text, f"missing topic: {topic}"


def test_doc_no_placeholders():
    text = DOC.read_text()
    for bad in ["TBD", "TODO", "XXX"]:
        assert bad not in text

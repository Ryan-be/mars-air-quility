"""SETUP.md covers required topics + has working internal references."""
from pathlib import Path
import re

DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "PLANT_GROW_UNIT_SETUP.md"


def test_doc_exists():
    assert DOC.exists()


def test_doc_covers_required_topics():
    text = DOC.read_text().lower()
    for topic in [
        "prerequisites", "enrollment", "first unit",
        "/boot/mlss-grow.yaml", "install.sh", "troubleshooting",
    ]:
        assert topic in text, f"missing topic: {topic}"


def test_doc_links_to_hardware_doc():
    text = DOC.read_text()
    assert "PLANT_GROW_UNIT_HARDWARE.md" in text


def test_doc_includes_install_oneliner_example():
    text = DOC.read_text()
    assert re.search(r"curl.*-k.*api/grow/install\.sh.*sudo bash", text)


def test_doc_no_obvious_placeholders():
    text = DOC.read_text()
    for bad in ["TBD", "TODO", "XXX", "FIXME"]:
        assert bad not in text, f"placeholder {bad} found"

"""Tests for AttributionEngine classifier persistence."""
import pickle
import pytest
from unittest.mock import patch, MagicMock


def _make_engine(config_path, monkeypatch):
    """Build an AttributionEngine with DB training stubbed out."""
    monkeypatch.setattr(
        "mlss_monitor.attribution.engine.AttributionEngine.train_on_tags",
        lambda self: None,
    )
    from mlss_monitor.attribution.engine import AttributionEngine
    return AttributionEngine(config_path)


def test_train_on_tags_saves_pickle(tmp_path, monkeypatch):
    """train_on_tags() should write classifier.pkl to data/ dir."""
    config_path = tmp_path / "config" / "fingerprints.yaml"
    config_path.parent.mkdir()
    config_path.write_text("sources: []\n")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    pkl_path = tmp_path / "data" / "classifier.pkl"

    # Capture the real train_on_tags before _make_engine stubs it out
    from mlss_monitor.attribution.engine import AttributionEngine
    real_train_on_tags = AttributionEngine.train_on_tags

    engine = _make_engine(str(config_path), monkeypatch)

    # Patch _pkl_path property to point at our tmp location
    monkeypatch.setattr(
        AttributionEngine,
        "_pkl_path",
        property(lambda self: pkl_path),
    )

    # Restore the real train_on_tags and stub the DB call
    monkeypatch.setattr(AttributionEngine, "train_on_tags", real_train_on_tags)

    with patch("mlss_monitor.attribution.engine.get_inferences", return_value=[]):
        engine.train_on_tags()

    assert pkl_path.exists(), "classifier.pkl should be written after training"


def test_init_loads_existing_pickle(tmp_path, monkeypatch):
    """__init__ should load classifier from pickle and skip DB retraining."""
    config_path = tmp_path / "config" / "fingerprints.yaml"
    config_path.parent.mkdir()
    config_path.write_text("sources: []\n")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    from river import linear_model, preprocessing
    model = preprocessing.StandardScaler() | linear_model.LogisticRegression()
    pkl_path = data_dir / "classifier.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(model, f)

    train_called = []

    def fake_train(self):
        train_called.append(True)

    monkeypatch.setattr(
        "mlss_monitor.attribution.engine.AttributionEngine.train_on_tags",
        fake_train,
    )

    # Patch _pkl_path property to return our tmp path
    with patch("mlss_monitor.attribution.engine.AttributionEngine._pkl_path",
               new_callable=lambda: property(lambda self: pkl_path)):
        from mlss_monitor.attribution.engine import AttributionEngine
        engine = AttributionEngine(str(config_path))

    assert not train_called, "train_on_tags should NOT be called when pickle exists"


def test_corrupt_pickle_falls_back_to_training(tmp_path, monkeypatch):
    """Corrupt classifier.pkl should be deleted and DB retraining used."""
    config_path = tmp_path / "config" / "fingerprints.yaml"
    config_path.parent.mkdir()
    config_path.write_text("sources: []\n")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    pkl_path = data_dir / "classifier.pkl"
    pkl_path.write_bytes(b"not a valid pickle")

    train_called = []

    def fake_train(self):
        train_called.append(True)

    monkeypatch.setattr(
        "mlss_monitor.attribution.engine.AttributionEngine.train_on_tags",
        fake_train,
    )

    with patch("mlss_monitor.attribution.engine.AttributionEngine._pkl_path",
               new_callable=lambda: property(lambda self: pkl_path)):
        from mlss_monitor.attribution.engine import AttributionEngine
        engine = AttributionEngine(str(config_path))

    assert train_called, "train_on_tags should be called as fallback after corrupt pickle"
    assert not pkl_path.exists(), "corrupt pickle should be deleted"

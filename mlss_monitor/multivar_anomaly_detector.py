# mlss_monitor/multivar_anomaly_detector.py
"""MultivarAnomalyDetector: composite multi-dimensional HalfSpaceTrees models."""
from __future__ import annotations

import logging
import pickle
import time
from pathlib import Path

import yaml
from river.anomaly import HalfSpaceTrees

from mlss_monitor.feature_vector import FeatureVector

log = logging.getLogger(__name__)

_HST_PARAMS = dict(n_trees=10, height=8, window_size=150, seed=42)
_EMA_ALPHA = 0.05   # ~20-reading half-life


class MultivarAnomalyDetector:
    """Five composite anomaly models, each fed a multi-dimensional dict.

    A reading is only learned/scored when ALL channels for that model have
    non-None values in the FeatureVector. Models are persisted as pickle
    files with the prefix ``multivar_``.
    """

    _SAVE_EVERY_N: int = 3

    def __init__(self, config_path: str | Path, model_dir: str | Path) -> None:
        self._config_path = Path(config_path)
        self._model_dir = Path(model_dir)
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._config: dict = {}
        self._models: dict[str, HalfSpaceTrees] = {}
        self._n_seen: dict[str, int] = {}
        self._ema: dict[str, dict[str, float]] = {}   # model_id → {channel → ema}
        self._calls_since_save: int = 0
        self._load_config()
        self._load_models()

    # ── Config ────────────────────────────────────────────────────────────────

    def _load_config(self) -> None:
        with open(self._config_path) as f:
            self._config = yaml.safe_load(f).get("multivar_anomaly", {})

    def _model_defs(self) -> list[dict]:
        return self._config.get("models", [])

    # ── Public helpers ────────────────────────────────────────────────────────

    def model_channels(self, model_id: str) -> list[str]:
        for m in self._model_defs():
            if m["id"] == model_id:
                return list(m["channels"])
        return []

    def model_label(self, model_id: str) -> str:
        for m in self._model_defs():
            if m["id"] == model_id:
                return m.get("label", model_id)
        return model_id

    def baselines(self, model_id: str) -> dict[str, float | None]:
        """Return EMA baseline per channel for a given model."""
        return dict(self._ema.get(model_id, {}))

    # ── Persistence ───────────────────────────────────────────────────────────

    def _pkl_path(self, model_id: str) -> Path:
        return self._model_dir / f"multivar_{model_id}.pkl"

    def _load_models(self) -> None:
        for m in self._model_defs():
            mid = m["id"]
            path = self._pkl_path(mid)
            if path.exists():
                try:
                    with open(path, "rb") as f:
                        saved = pickle.load(f)
                    model = saved["model"]
                    if (getattr(model, "n_trees", None) != _HST_PARAMS["n_trees"] or
                            getattr(model, "height", None) != _HST_PARAMS["height"]):
                        log.info("MultivarAnomalyDetector: params changed for %r, recreating", mid)
                        self._models[mid] = HalfSpaceTrees(**_HST_PARAMS)
                        self._n_seen[mid] = 0
                        continue
                    self._models[mid] = model
                    self._n_seen[mid] = saved.get("n_seen", 0)
                    self._ema[mid] = saved.get("ema", {})
                    continue
                except Exception as exc:
                    log.warning("MultivarAnomalyDetector: could not load %r: %s", mid, exc)
            self._models[mid] = HalfSpaceTrees(**_HST_PARAMS)
            self._n_seen[mid] = 0

    def _save_models(self) -> None:
        for mid, model in self._models.items():
            path = self._pkl_path(mid)
            try:
                with open(path, "wb") as f:
                    pickle.dump({
                        "model": model,
                        "n_seen": self._n_seen[mid],
                        "ema": self._ema.get(mid, {}),
                    }, f)
            except Exception as exc:
                log.warning("MultivarAnomalyDetector: could not save %r: %s", mid, exc)

    # ── Core API ──────────────────────────────────────────────────────────────

    def learn_and_score(self, fv: FeatureVector) -> dict[str, float | None]:
        """Score then train all composite models.

        Returns model_id → score (float) or None if any channel is missing
        or the model is still in cold-start.
        """
        cold_start = self._config.get("cold_start_readings", 500)
        scores: dict[str, float | None] = {}

        for m in self._model_defs():
            mid = m["id"]
            channels = m["channels"]

            # Extract values — skip entire model if any channel is None
            x: dict[str, float] = {}
            for ch in channels:
                val = getattr(fv, ch, None)
                if val is None:
                    x = {}
                    break
                x[ch] = float(val)

            # PM channels cannot physically be 0 — treat as sensor failure
            _PM_CHANNELS = {"pm1_current", "pm25_current", "pm10_current"}
            for ch in channels:
                if ch in _PM_CHANNELS and x.get(ch, None) == 0.0:
                    x = {}  # skip this reading
                    break

            if not x:
                scores[mid] = None
                continue

            model = self._models[mid]

            try:
                raw_score = float(model.score_one(x))
            except Exception:
                raw_score = 0.0
            model.learn_one(x)
            self._n_seen[mid] = self._n_seen.get(mid, 0) + 1

            # Update EMA per channel
            ema = self._ema.setdefault(mid, {})
            for ch, val in x.items():
                ema[ch] = _EMA_ALPHA * val + (1 - _EMA_ALPHA) * ema.get(ch, val)

            scores[mid] = None if self._n_seen[mid] < cold_start else raw_score

        self._calls_since_save += 1
        if self._calls_since_save >= self._SAVE_EVERY_N:
            self._save_models()
            self._calls_since_save = 0

        return scores

    def anomalous_models(self, scores: dict[str, float | None]) -> list[str]:
        """Return model IDs whose score exceeds the configured threshold."""
        threshold = self._config.get("threshold", 0.75)
        return [mid for mid, s in scores.items() if s is not None and s > threshold]

    def bootstrap(self, channel_data: dict[str, list[dict]]) -> None:
        """Feed historical multi-dimensional readings into models.

        Args:
            channel_data: model_id → list of {channel: value} dicts, oldest first.
        """
        for mid, readings in channel_data.items():
            if mid not in self._models:
                continue
            model = self._models[mid]
            ema = self._ema.setdefault(mid, {})
            for i, x in enumerate(readings):
                model.learn_one(x)
                self._n_seen[mid] = self._n_seen.get(mid, 0) + 1
                for ch, val in x.items():
                    ema[ch] = _EMA_ALPHA * val + (1 - _EMA_ALPHA) * ema.get(ch, val)
                if i % 100 == 0:
                    time.sleep(0)  # yield GIL
            log.info("MultivarAnomalyDetector.bootstrap: fed %d readings into %r", len(readings), mid)
        self._save_models()

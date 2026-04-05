"""AnomalyDetector: per-channel river HalfSpaceTrees with pickle persistence."""
from __future__ import annotations

import logging
import pickle
import time
from pathlib import Path

import yaml
from river.anomaly import HalfSpaceTrees

from mlss_monitor.feature_vector import FeatureVector

log = logging.getLogger(__name__)

# Maps anomaly config channel name → FeatureVector field name for current value.
_CHANNEL_TO_FV_FIELD: dict[str, str] = {
    "tvoc_ppb":      "tvoc_current",
    "eco2_ppm":      "eco2_current",
    "temperature_c": "temperature_current",
    "humidity_pct":  "humidity_current",
    "pm1_ug_m3":     "pm1_current",
    "pm25_ug_m3":    "pm25_current",
    "pm10_ug_m3":    "pm10_current",
    "co_ppb":        "co_current",
    "no2_ppb":       "no2_current",
    "nh3_ppb":       "nh3_current",
}


class AnomalyDetector:
    """Per-channel streaming anomaly detection using river HalfSpaceTrees.

    One model instance per channel. Models are persisted to disk as pickle
    files so they survive restarts and accumulate learning over time.
    Scores are suppressed (returned as None) during the cold-start period.
    """

    # Save models every N learn_and_score calls to reduce SD card write wear.
    # At 60s cycles: N=3 → saves every ~3 minutes instead of every minute.
    _SAVE_EVERY_N: int = 3

    def __init__(self, config_path: str | Path, model_dir: str | Path) -> None:
        self._config_path = Path(config_path)
        self._model_dir = Path(model_dir)
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._config: dict = {}
        self._models: dict[str, HalfSpaceTrees] = {}
        self._n_seen: dict[str, int] = {}
        self._calls_since_save: int = 0
        self._ema: dict[str, float] = {}
        self._last_scores: dict[str, float | None] = {}
        self._load_config()
        self._load_models()

    def _load_config(self) -> None:
        with open(self._config_path) as f:
            self._config = yaml.safe_load(f).get("anomaly", {})

    def _channels(self) -> list[str]:
        return self._config.get("channels", list(_CHANNEL_TO_FV_FIELD.keys()))

    def _load_models(self) -> None:
        for ch in self._channels():
            model_path = self._model_dir / f"{ch}.pkl"
            if model_path.exists():
                try:
                    with open(model_path, "rb") as f:
                        saved = pickle.load(f)
                    model = saved["model"]
                    # Recreate if params differ (avoids slow old models blocking startup)
                    if (getattr(model, 'n_trees', None) != 10 or
                            getattr(model, 'height', None) != 8):
                        log.info("AnomalyDetector: model params changed for %r, recreating", ch)
                        self._models[ch] = HalfSpaceTrees(n_trees=10, height=8, window_size=150, seed=42)
                        self._n_seen[ch] = 0
                        continue
                    self._models[ch] = model
                    self._n_seen[ch] = saved.get("n_seen", 0)
                    continue
                except Exception as exc:
                    log.warning("AnomalyDetector: could not load model %r: %s", ch, exc)
            self._models[ch] = HalfSpaceTrees(n_trees=10, height=8, window_size=150, seed=42)
            self._n_seen[ch] = 0

    def _save_models(self) -> None:
        for ch, model in self._models.items():
            model_path = self._model_dir / f"{ch}.pkl"
            try:
                with open(model_path, "wb") as f:
                    pickle.dump({"model": model, "n_seen": self._n_seen[ch]}, f)
            except Exception as exc:
                log.warning("AnomalyDetector: could not save model %r: %s", ch, exc)

    def learn_and_score(self, fv: FeatureVector) -> dict[str, float | None]:
        """Score then train all channel models with the current FeatureVector.

        Scores before learning so the model hasn't yet seen this point.
        Returns channel → score (0.0–1.0) or None if channel has no data
        or is in the cold-start period.
        """
        cold_start = self._config.get("cold_start_readings", 1440)
        scores: dict[str, float | None] = {}

        for ch in self._channels():
            fv_field = _CHANNEL_TO_FV_FIELD.get(ch)
            if fv_field is None:
                scores[ch] = None
                continue
            value = getattr(fv, fv_field, None)
            if value is None:
                scores[ch] = None
                continue

            x = {"value": float(value)}
            model = self._models[ch]

            try:
                raw_score = float(model.score_one(x))
            except Exception:
                raw_score = 0.0
            model.learn_one(x)
            self._n_seen[ch] = self._n_seen.get(ch, 0) + 1
            _alpha = 0.05
            self._ema[ch] = _alpha * value + (1 - _alpha) * self._ema.get(ch, value)

            # Suppress during cold start
            scores[ch] = None if self._n_seen[ch] < cold_start else raw_score

        self._last_scores = scores
        self._calls_since_save += 1
        if self._calls_since_save >= self._SAVE_EVERY_N:
            self._save_models()
            self._calls_since_save = 0
        return scores

    def bootstrap(self, channel_data: dict[str, list[float]]) -> None:
        """Feed historical values into channel models to warm up cold-start.

        Skips channels not in self._models. After all channels are processed,
        persists models to disk.

        Args:
            channel_data: mapping of channel name → list of historical float values
                          ordered oldest-first.
        """
        for ch, values in channel_data.items():
            if ch not in self._models:
                continue
            model = self._models[ch]
            for i, v in enumerate(values):
                model.learn_one({"value": float(v)})
                self._n_seen[ch] = self._n_seen.get(ch, 0) + 1
                if i % 100 == 0:
                    time.sleep(0)  # yield GIL to keep Flask responsive
            log.info(
                "AnomalyDetector.bootstrap: fed %d readings into channel %r",
                len(values),
                ch,
            )
        self._save_models()

    def anomalous_channels(self, scores: dict[str, float | None]) -> list[str]:
        """Return channel names whose score exceeds the configured threshold."""
        threshold = self._config.get("score_threshold", 0.7)
        return [ch for ch, s in scores.items() if s is not None and s > threshold]

    def baseline(self, channel: str) -> float | None:
        """Return the EMA baseline for a channel, or None if not yet seen."""
        return self._ema.get(channel)

    def reset_channel(self, channel: str) -> None:
        """Reset a single channel model and delete its persisted pickle.

        Use when a sensor was faulty for a period and accumulated bad training
        data. After reset the channel re-enters the cold-start suppression
        period and rebuilds from scratch.
        """
        if channel not in self._models:
            log.warning("AnomalyDetector.reset_channel: unknown channel %r", channel)
            return
        self._models[channel] = HalfSpaceTrees(n_trees=10, height=8, window_size=150, seed=42)
        self._n_seen[channel] = 0
        self._ema.pop(channel, None)
        pkl_path = self._model_dir / f"{channel}.pkl"
        try:
            pkl_path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("AnomalyDetector.reset_channel: could not delete %s: %s", pkl_path, exc)
        log.info("AnomalyDetector: reset channel %r", channel)

    def live_scores(self) -> dict[str, float | None]:
        """Return the most-recent EMA value per channel (not an anomaly score).

        Returns None for channels with no readings yet.  Used by the anomaly
        settings UI to show live sensor levels without running a full detection
        cycle.
        """
        return {ch: self._ema.get(ch) for ch in self._channels()}

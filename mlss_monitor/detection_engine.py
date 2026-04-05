"""DetectionEngine: orchestrates RuleEngine + AnomalyDetector → inferences.

In dry_run=True (shadow) mode: evaluates rules and logs what would fire,
but never calls save_inference. Used during parallel validation against
the old inference_engine.

In dry_run=False (live) mode: calls save_inference for each event.
Switch mode by changing the dry_run flag in app.py once parity is confirmed.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


from database.db_logger import (
    DB_FILE,
    get_recent_inference_by_type,
    save_inference,
)
from mlss_monitor.anomaly_detector import AnomalyDetector
from mlss_monitor.attribution import AttributionEngine
from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.inference_evidence import (
    build_sensor_snapshot,
    anomaly_description,
    anomaly_action,
)
from mlss_monitor.threshold_engine import RuleEngine

log = logging.getLogger(__name__)

# Maps DB/anomaly-config channel name → FeatureVector field name used by inference_evidence
_DB_CH_TO_FV_FIELD: dict[str, str] = {
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


def _vpd_kpa(temp_c: float | None, rh: float | None) -> float | None:
    """Vapour pressure deficit in kPa."""
    if temp_c is None or rh is None or rh <= 0:
        return None
    svp = 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))
    return svp * (1 - rh / 100)


class DetectionEngine:
    """Orchestrates RuleEngine + AnomalyDetector.

    dry_run=True: shadow/validation mode — logs what would fire, no DB writes.
    dry_run=False: live mode — calls save_inference for each event.
    """

    def __init__(
        self,
        rules_path: str | Path,
        anomaly_config_path: str | Path,
        model_dir: str | Path,
        fingerprints_path: str | Path | None = None,
        multivar_config_path: str | Path | None = None,
        dry_run: bool = True,
    ) -> None:
        self._dry_run = dry_run
        self._rules_path = Path(rules_path)
        self._anomaly_config_path = Path(anomaly_config_path)
        self._fingerprints_path = Path(fingerprints_path) if fingerprints_path is not None else None
        self._rule_engine = RuleEngine(rules_path)
        self._anomaly_detector = AnomalyDetector(anomaly_config_path, model_dir)
        self._attribution_engine: AttributionEngine | None = None
        if fingerprints_path is not None:
            try:
                self._attribution_engine = AttributionEngine(fingerprints_path)
            except Exception as exc:
                log.error("DetectionEngine: could not load fingerprints: %s", exc)
        self._multivar_detector = None
        if multivar_config_path is not None:
            try:
                from mlss_monitor.multivar_anomaly_detector import MultivarAnomalyDetector
                self._multivar_detector = MultivarAnomalyDetector(multivar_config_path, model_dir)
            except Exception as exc:
                log.error("DetectionEngine: could not load multivar config: %s", exc)

    # ── Attribution helper ────────────────────────────────────────────────────

    def _attribute(self, fv: FeatureVector):
        """Run attribution scoring. Returns None if engine not configured or no match."""
        if self._attribution_engine is None:
            return None
        try:
            return self._attribution_engine.attribute(fv)
        except Exception as exc:
            log.warning("DetectionEngine: attribution error: %s", exc)
            return None

    # ── Bootstrap from historical DB ──────────────────────────────────────────

    def bootstrap_from_db(self, db_file: str) -> None:
        """Warm up the AnomalyDetector with historical data from SQLite.

        Queries both ``hot_tier`` (recent high-resolution data) and the legacy
        ``sensor_data`` table, merging them so the oldest readings come first.
        Silently returns if there is no AnomalyDetector configured.

        Args:
            db_file: path to the SQLite database file.
        """
        if self._anomaly_detector is None:
            return

        # Check if per-channel models need bootstrapping
        cold_start = self._anomaly_detector._config.get("cold_start_readings", 1440)
        min_seen = min(
            self._anomaly_detector._n_seen.get(ch, 0)
            for ch in self._anomaly_detector._channels()
        )
        per_channel_warm = min_seen >= cold_start // 2

        # Check if multivar models need bootstrapping
        multivar_warm = True
        if self._multivar_detector is not None:
            mv_cold_start = self._multivar_detector._config.get("cold_start_readings", 500)
            if self._multivar_detector._n_seen:
                multivar_warm = min(self._multivar_detector._n_seen.values()) >= mv_cold_start // 2
            else:
                multivar_warm = False  # no models yet = not warm

        if per_channel_warm and multivar_warm:
            log.info(
                "DetectionEngine.bootstrap_from_db: all models already warm "
                "(per-channel min n_seen=%d >= %d, multivar warm=%s), skipping bootstrap",
                min_seen, cold_start // 2, multivar_warm,
            )
            return

        _HOT_TIER_COLS = [
            "tvoc_ppb",
            "eco2_ppm",
            "temperature_c",
            "humidity_pct",
            "pm1_ug_m3",
            "pm25_ug_m3",
            "pm10_ug_m3",
            "co_ppb",
            "no2_ppb",
            "nh3_ppb",
        ]

        try:
            conn = sqlite3.connect(db_file, timeout=15)
            try:
                channel_data: dict[str, list[float]] = {}

                # Cap per-channel to last 300 rows — enough to meaningfully warm
                # HalfSpaceTrees without blocking Flask startup for minutes.
                _LIMIT = 300

                # Fetch hot_tier columns (1s resolution, up to 60 min)
                for col in _HOT_TIER_COLS:
                    rows = conn.execute(
                        f"SELECT {col} FROM hot_tier WHERE {col} IS NOT NULL"
                        f" ORDER BY timestamp DESC LIMIT {_LIMIT}"
                    ).fetchall()
                    channel_data[col] = [r[0] for r in reversed(rows)]

                # Fetch all available sensor_data columns (prepend as older history)
                _SD_COL_MAP = {
                    "tvoc":        "tvoc_ppb",
                    "eco2":        "eco2_ppm",
                    "temperature": "temperature_c",
                    "humidity":    "humidity_pct",
                    "pm1_0":       "pm1_ug_m3",
                    "pm2_5":       "pm25_ug_m3",
                    "pm10":        "pm10_ug_m3",
                    "gas_co":      "co_ppb",
                    "gas_no2":     "no2_ppb",
                    "gas_nh3":     "nh3_ppb",
                }
                for sd_col, ch in _SD_COL_MAP.items():
                    try:
                        rows = conn.execute(
                            f"SELECT {sd_col} FROM sensor_data"
                            f" WHERE {sd_col} IS NOT NULL"
                            f" ORDER BY timestamp DESC LIMIT {_LIMIT}"
                        ).fetchall()
                    except Exception:
                        continue  # column may not exist on older DB schemas
                    if rows:
                        cold = [r[0] for r in reversed(rows)]
                        channel_data[ch] = cold + channel_data.get(ch, [])

            finally:
                conn.close()

            total = sum(len(v) for v in channel_data.values())
            log.info("DetectionEngine.bootstrap_from_db: fetched %d total readings", total)

            if not per_channel_warm:
                log.info("DetectionEngine.bootstrap_from_db: bootstrapping per-channel models")
                self._anomaly_detector.bootstrap(channel_data)

            # Bootstrap composite models from the same historical data
            if not multivar_warm and self._multivar_detector is not None:
                # channel_data is keyed by DB names; multivar channels use FV field names
                _FV_TO_DB = {
                    "tvoc_current": "tvoc_ppb", "eco2_current": "eco2_ppm",
                    "temperature_current": "temperature_c", "humidity_current": "humidity_pct",
                    "pm1_current": "pm1_ug_m3", "pm25_current": "pm25_ug_m3",
                    "pm10_current": "pm10_ug_m3", "co_current": "co_ppb",
                    "no2_current": "no2_ppb", "nh3_current": "nh3_ppb",
                }
                # Pre-compute VPD series for thermal_moisture model
                temp_vals = channel_data.get("temperature_c", [])
                hum_vals  = channel_data.get("humidity_pct",  [])
                vpd_vals  = [
                    _vpd_kpa(t, h)
                    for t, h in zip(temp_vals, hum_vals)
                ]

                mv_channel_data: dict[str, list[dict]] = {}
                for m in self._multivar_detector._model_defs():
                    mid = m["id"]
                    channels = m["channels"]
                    # Resolve each FV field name to its DB data series
                    series: dict[str, list] = {}
                    for ch in channels:
                        if ch == "vpd_kpa":
                            series[ch] = vpd_vals
                        else:
                            db_key = _FV_TO_DB.get(ch)
                            if db_key:
                                series[ch] = channel_data.get(db_key, [])
                    # Only bootstrap if all channels have data
                    lengths = [len(v) for v in series.values()]
                    if not lengths or min(lengths) == 0:
                        continue
                    n = min(lengths)
                    readings = []
                    for i in range(n):
                        row = {ch: series[ch][i] for ch in channels if i < len(series.get(ch, []))}
                        # Skip rows where any value is None (vpd_kpa can be None)
                        if len(row) == len(channels) and all(v is not None for v in row.values()):
                            readings.append(row)
                    if readings:
                        mv_channel_data[mid] = readings
                if mv_channel_data:
                    self._multivar_detector.bootstrap(mv_channel_data)

        except Exception as exc:
            log.error("DetectionEngine.bootstrap_from_db failed: %s", exc)
            return

    # ── Short-term detection (call at _CYCLE_60S) ─────────────────────────────

    def run(self, fv: FeatureVector) -> list[str]:
        """Evaluate threshold rules + anomaly detector against the FeatureVector.

        Returns a list of event_type strings that fired (for shadow-mode logging).
        In dry_run=True mode, never calls save_inference.
        In dry_run=False mode, calls save_inference for each new event (respects
        dedupe window via get_recent_inference_by_type).
        """
        fired: list[str] = []

        # 1. Threshold rule events
        matches = self._rule_engine.evaluate(fv)
        for match in matches:
            if get_recent_inference_by_type(match.event_type, hours=match.dedupe_hours):
                continue  # within dedupe window
            fired.append(match.event_type)
            if not self._dry_run:
                try:
                    attribution = self._attribute(fv)
                    evidence: dict = {"fv_timestamp": fv.timestamp.isoformat()}
                    if attribution is not None:
                        evidence["attribution"] = attribution.source_id
                        evidence["attribution_confidence"] = round(attribution.confidence, 3)
                        if attribution.runner_up_id is not None:
                            evidence["runner_up"] = attribution.runner_up_id
                            evidence["runner_up_confidence"] = round(attribution.runner_up_confidence, 3)

                    title = match.title
                    description = match.description
                    if attribution is not None:
                        title = f"{match.title} — {attribution.label} ({attribution.confidence:.0%})"
                        description = f"{match.description}\n\n{attribution.description}"
                    action = attribution.action if attribution is not None else match.action

                    save_inference(
                        event_type=match.event_type,
                        severity=match.severity,
                        title=title,
                        description=description,
                        action=action,
                        evidence=evidence,
                        confidence=match.confidence,
                    )
                except Exception as exc:
                    log.error(
                        "DetectionEngine: save_inference failed for %r: %s",
                        match.event_type,
                        exc,
                    )

        # 2. Per-channel anomaly detection
        scores = self._anomaly_detector.learn_and_score(fv)
        anomalous = self._anomaly_detector.anomalous_channels(scores)
        for ch in anomalous:
            event_type = f"anomaly_{ch}"
            if get_recent_inference_by_type(event_type, hours=1):
                continue
            score = scores[ch]
            fired.append(event_type)
            if not self._dry_run:
                try:
                    fv_field = _DB_CH_TO_FV_FIELD.get(ch, ch)  # translate to FV field name
                    baselines = {fv_field: self._anomaly_detector.baseline(ch)}
                    snapshot = build_sensor_snapshot(fv, [fv_field], baselines)
                    description = anomaly_description(snapshot)
                    action = anomaly_action(channel=ch)
                    save_inference(
                        event_type=event_type,
                        severity="warning",
                        title=(
                            f"Anomaly: {snapshot[0]['label'] if snapshot else ch.replace('_', ' ')}"
                            f" — {score:.2f} score"
                        ),
                        description=description,
                        action=action,
                        evidence={
                            "sensor_snapshot": snapshot,
                            "anomaly_score": round(score, 4),
                        },
                        confidence=round(score, 2),
                    )
                except Exception as exc:
                    log.error("DetectionEngine: save_inference failed for anomaly %r: %s", ch, exc)

        # 3. Composite multivariate anomaly detection
        if self._multivar_detector is not None:
            mv_scores = self._multivar_detector.learn_and_score(fv)
            for mid in self._multivar_detector.anomalous_models(mv_scores):
                event_type = f"anomaly_{mid}"
                if get_recent_inference_by_type(event_type, hours=1):
                    continue
                score = mv_scores[mid]
                fired.append(event_type)
                if not self._dry_run:
                    try:
                        channels = self._multivar_detector.model_channels(mid)
                        label = self._multivar_detector.model_label(mid)
                        baselines = self._multivar_detector.baselines(mid)
                        snapshot = build_sensor_snapshot(fv, channels, baselines)
                        description = anomaly_description(snapshot, model_label=label)
                        action = anomaly_action(model_id=mid)
                        save_inference(
                            event_type=event_type,
                            severity="warning",
                            title=f"Composite anomaly: {label} — {score:.2f} score",
                            description=description,
                            action=action,
                            evidence={
                                "sensor_snapshot": snapshot,
                                "anomaly_score": round(score, 4),
                                "model_id": mid,
                            },
                            confidence=round(score, 2),
                        )
                    except Exception as exc:
                        log.error("DetectionEngine: save_inference failed for multivar %r: %s", mid, exc)

        if fired:
            mode = "DRY-RUN" if self._dry_run else "LIVE"
            log.info("[DetectionEngine][%s] fired: %s", mode, fired)

        return fired

    # ── Long-term summaries (call at _CYCLE_1H / _CYCLE_24H) ─────────────────

    def run_hourly(self, fv: FeatureVector) -> None:
        """Run hourly summary detector."""
        try:
            self._hourly_summary(fv)
        except Exception as exc:
            log.error("DetectionEngine: hourly summary error: %s", exc)

    def run_daily(self, fv: FeatureVector) -> None:
        """Run daily summary, pattern, and overnight buildup detectors."""
        try:
            self._daily_summary(fv)
        except Exception as exc:
            log.error("DetectionEngine: daily summary error: %s", exc)
        try:
            self._detect_daily_patterns(fv)
        except Exception as exc:
            log.error("DetectionEngine: daily pattern error: %s", exc)
        try:
            self._overnight_buildup(fv)
        except Exception as exc:
            log.error("DetectionEngine: overnight buildup error: %s", exc)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _fetch_recent(self, minutes: int = 30) -> list[dict]:
        """Fetch sensor_data rows from the last N minutes, oldest first."""
        conn = None
        try:
            conn = sqlite3.connect(DB_FILE)
            conn.row_factory = sqlite3.Row
            since = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
            rows = conn.execute(
                "SELECT * FROM sensor_data WHERE timestamp >= ? ORDER BY timestamp ASC",
                (since,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            if conn:
                conn.close()

    def _hourly_summary(self, fv: FeatureVector) -> None:
        """Analyse the last hour of data and produce a summary inference.

        Uses fv for current-state values (slopes); queries DB for historical stats.
        """
        if get_recent_inference_by_type("hourly_summary", hours=1):
            return

        rows = self._fetch_recent(minutes=60)
        if len(rows) < 20:
            return

        temps = [r["temperature"] for r in rows if r["temperature"] is not None]
        hums  = [r["humidity"]    for r in rows if r["humidity"] is not None]
        tvocs = [r["tvoc"]        for r in rows if r["tvoc"] is not None]
        eco2s = [r["eco2"]        for r in rows if r["eco2"] is not None]

        if not temps or not hums or not tvocs or not eco2s:
            return

        def _mean(vals):
            return sum(vals) / len(vals) if vals else 0

        def _std(vals):
            if len(vals) < 2:
                return 0
            m = _mean(vals)
            return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))

        temp_mean, temp_std = _mean(temps), _std(temps)
        hum_mean,  hum_std  = _mean(hums),  _std(hums)
        tvoc_mean            = _mean(tvocs)
        eco2_mean            = _mean(eco2s)
        tvoc_peak            = max(tvocs)
        eco2_peak            = max(eco2s)

        # Use FeatureVector for slopes (more accurate, from hot tier)
        temp_slope = fv.temperature_slope_1m or 0
        hum_slope  = fv.humidity_slope_1m  or 0
        tvoc_slope = fv.tvoc_slope_1m      or 0
        eco2_slope = fv.eco2_slope_1m      or 0

        def _trend_word(slope, threshold=0.05):
            if slope > threshold:
                return "rising"
            if slope < -threshold:
                return "falling"
            return "stable"

        temp_trend = _trend_word(temp_slope, 0.02)
        hum_trend  = _trend_word(hum_slope, 0.1)
        tvoc_trend = _trend_word(tvoc_slope, 0.5)
        eco2_trend = _trend_word(eco2_slope, 1.0)

        issues = []
        if tvoc_mean > 250:
            issues.append(f"avg TVOC {int(tvoc_mean)} ppb (above 250)")
        if eco2_mean > 800:
            issues.append(f"avg eCO₂ {int(eco2_mean)} ppm (above 800)")
        if temp_mean > 28.0 or temp_mean < 15.0:
            issues.append(f"avg temp {temp_mean:.1f}°C (outside 15–28°C)")
        if hum_mean > 70.0 or hum_mean < 30.0:
            issues.append(f"avg humidity {hum_mean:.0f}% (outside 30–70%)")

        sev = "warning" if len(issues) >= 2 else "info"
        quality = "Poor" if len(issues) >= 2 else "Fair" if issues else "Good"

        stability_issues = []
        if temp_std > 2.0:
            stability_issues.append(f"temperature varied ±{temp_std:.1f}°C")
        if hum_std > 8.0:
            stability_issues.append(f"humidity varied ±{hum_std:.0f}%")
        stability = ("Unstable — " + ", ".join(stability_issues)) if stability_issues else "Stable"

        desc_parts = [
            f"Over the past hour ({len(rows)} readings):",
            f"Temperature: {temp_mean:.1f}°C (±{temp_std:.1f}), {temp_trend}.",
            f"Humidity: {hum_mean:.0f}% (±{hum_std:.0f}), {hum_trend}.",
            f"TVOC: avg {int(tvoc_mean)} ppb, peak {int(tvoc_peak)} ppb, {tvoc_trend}.",
            f"eCO₂: avg {int(eco2_mean)} ppm, peak {int(eco2_peak)} ppm, {eco2_trend}.",
        ]
        if issues:
            desc_parts.append(f"Issues: {'; '.join(issues)}.")
        if stability_issues:
            desc_parts.append(f"Stability: {stability}.")

        action_str = (
            "Address the issues noted above. " + ("; ".join(issues) + "." if issues else "")
            if issues else
            "No action needed — environment is within normal ranges."
        )

        if self._dry_run:
            log.info("[DetectionEngine][DRY-RUN] Would fire: hourly_summary")
            return

        save_inference(
            event_type="hourly_summary",
            severity=sev,
            title=f"Hourly summary — {quality} air quality",
            description=" ".join(desc_parts),
            action=action_str,
            evidence={
                "period": "1 hour",
                "readings": str(len(rows)),
                "temp_avg": f"{temp_mean:.1f}°C",
                "temp_trend": temp_trend,
                "humidity_avg": f"{hum_mean:.0f}%",
                "humidity_trend": hum_trend,
                "tvoc_avg": f"{int(tvoc_mean)} ppb",
                "tvoc_peak": f"{int(tvoc_peak)} ppb",
                "tvoc_trend": tvoc_trend,
                "eco2_avg": f"{int(eco2_mean)} ppm",
                "eco2_peak": f"{int(eco2_peak)} ppm",
                "eco2_trend": eco2_trend,
                "stability": stability,
                "overall": quality,
            },
            confidence=0.9,
        )

    def _daily_summary(self, fv: FeatureVector) -> None:
        """Analyse the last 24 hours and produce a daily environment report."""
        rows = self._fetch_recent(minutes=1440)
        if len(rows) < 100:
            return
        if get_recent_inference_by_type("daily_summary", hours=23):
            return

        temps = [r["temperature"] for r in rows if r["temperature"] is not None]
        hums  = [r["humidity"]    for r in rows if r["humidity"] is not None]
        tvocs = [r["tvoc"]        for r in rows if r["tvoc"] is not None]
        eco2s = [r["eco2"]        for r in rows if r["eco2"] is not None]

        if not all([temps, hums, tvocs, eco2s]):
            return

        def _mean(vals): return sum(vals) / len(vals) if vals else 0

        # Basic stats
        temp_mean, temp_min, temp_max = _mean(temps), min(temps), max(temps)
        hum_mean, hum_min, hum_max = _mean(hums), min(hums), max(hums)
        tvoc_mean, tvoc_peak = _mean(tvocs), max(tvocs)
        eco2_mean, eco2_peak = _mean(eco2s), max(eco2s)

        # Hardcoded thresholds
        tvoc_moderate = 250
        temp_high = 28.0
        temp_low = 15.0
        hum_high = 70.0
        hum_low = 30.0
        vpd_low = 0.4
        vpd_high = 1.6

        # Time in bad zones
        tvoc_high_pct = sum(1 for v in tvocs if v > tvoc_moderate) / len(tvocs) * 100
        eco2_high_pct = sum(1 for v in eco2s if v > 800) / len(eco2s) * 100
        temp_out_pct  = sum(1 for v in temps if v > temp_high or v < temp_low) / len(temps) * 100
        hum_out_pct   = sum(1 for v in hums if v > hum_high or v < hum_low) / len(hums) * 100

        # VPD analysis
        vpds = [_vpd_kpa(r.get("temperature"), r.get("humidity")) for r in rows]
        vpds = [v for v in vpds if v is not None]
        vpd_mean = _mean(vpds) if vpds else None
        vpd_opt_pct = sum(1 for v in vpds if vpd_low <= v <= vpd_high) / len(vpds) * 100 if vpds else 0

        # Overall score (simple weighted)
        score = 100
        if tvoc_high_pct > 0:
            score -= tvoc_high_pct * 0.3
        if eco2_high_pct > 0:
            score -= eco2_high_pct * 0.3
        if temp_out_pct > 0:
            score -= temp_out_pct * 0.2
        if hum_out_pct > 0:
            score -= hum_out_pct * 0.2
        score = max(0, min(100, round(score)))

        if score >= 80:
            quality, sev = "Good", "info"
        elif score >= 50:
            quality, sev = "Fair", "info"
        else:
            quality, sev = "Poor", "warning"

        # Annotations count
        annotated = [r for r in rows if r.get("annotation")]
        anno_str = f" You added {len(annotated)} annotation(s) during this period." if annotated else ""

        # Build description
        desc = (
            f"24-hour environment report ({len(rows)} readings). "
            f"Temperature: {temp_mean:.1f}°C avg (range {temp_min:.1f}–{temp_max:.1f}°C), "
            f"outside comfort zone {temp_out_pct:.0f}% of the time. "
            f"Humidity: {hum_mean:.0f}% avg (range {hum_min:.0f}–{hum_max:.0f}%), "
            f"outside ideal range {hum_out_pct:.0f}% of the time. "
            f"TVOC: avg {int(tvoc_mean)} ppb, peak {int(tvoc_peak)} ppb, "
            f"above moderate ({tvoc_moderate} ppb) for {tvoc_high_pct:.0f}% of readings. "
            f"eCO₂: avg {int(eco2_mean)} ppm, peak {int(eco2_peak)} ppm, "
            f"above 800 ppm for {eco2_high_pct:.0f}% of readings."
        )
        if vpd_mean is not None:
            desc += (
                f" VPD: avg {vpd_mean:.2f} kPa, "
                f"in optimal range {vpd_opt_pct:.0f}% of the time."
            )
        desc += (
            f" Overall environment score: {score}/100 ({quality}).{anno_str}"
        )

        # Action items
        actions = []
        if tvoc_high_pct > 20:
            actions.append(f"TVOC was elevated {tvoc_high_pct:.0f}% of the day — investigate persistent VOC sources")
        if eco2_high_pct > 20:
            actions.append(f"eCO₂ was high {eco2_high_pct:.0f}% of the day — improve base ventilation rate")
        if temp_out_pct > 30:
            actions.append(
                f"Temperature was outside comfort zone {temp_out_pct:.0f}% of the day — check heating/cooling"
            )
        if hum_out_pct > 30:
            actions.append(
                f"Humidity was outside ideal range {hum_out_pct:.0f}% of the day"
                " — consider humidifier/dehumidifier"
            )
        if vpds and vpd_opt_pct < 50:
            actions.append(
                f"VPD was optimal only {vpd_opt_pct:.0f}% of the day"
                " — adjust temp/humidity for plant health"
            )
        action = (
            ". ".join(actions) + "." if actions
            else "Environment was generally within acceptable ranges — no action needed."
        )

        # Annotation context
        annotation_context = None
        if annotated:
            annotation_texts = [r["annotation"] for r in annotated if r.get("annotation")]
            annotation_context = " | ".join(annotation_texts) if annotation_texts else None

        if self._dry_run:
            log.info("[DetectionEngine][DRY-RUN] Would fire: daily_summary")
            return

        save_inference(
            event_type="daily_summary",
            severity=sev,
            title=f"Daily report — {score}/100 ({quality})",
            description=desc,
            action=action,
            evidence={
                "period": "24 hours",
                "readings": str(len(rows)),
                "score": f"{score}/100",
                "temp_avg": f"{temp_mean:.1f}°C",
                "temp_range": f"{temp_min:.1f} – {temp_max:.1f}°C",
                "temp_out_of_range": f"{temp_out_pct:.0f}%",
                "humidity_avg": f"{hum_mean:.0f}%",
                "humidity_range": f"{hum_min:.0f} – {hum_max:.0f}%",
                "humidity_out_of_range": f"{hum_out_pct:.0f}%",
                "tvoc_avg": f"{int(tvoc_mean)} ppb",
                "tvoc_peak": f"{int(tvoc_peak)} ppb",
                "tvoc_above_moderate": f"{tvoc_high_pct:.0f}%",
                "eco2_avg": f"{int(eco2_mean)} ppm",
                "eco2_peak": f"{int(eco2_peak)} ppm",
                "eco2_above_800": f"{eco2_high_pct:.0f}%",
                "vpd_avg": f"{vpd_mean:.2f} kPa" if vpd_mean else "N/A",
                "vpd_optimal_time": f"{vpd_opt_pct:.0f}%",
                "annotations": str(len(annotated)),
            },
            confidence=0.95,
            start_id=rows[0].get("id"),
            end_id=rows[-1].get("id"),
            annotation=annotation_context,
        )

    def _detect_daily_patterns(self, fv: FeatureVector) -> None:
        """Detect recurring patterns in the 24h data (e.g. regular spikes at certain times)."""
        rows = self._fetch_recent(minutes=1440)
        if len(rows) < 100:
            return
        if get_recent_inference_by_type("daily_pattern", hours=23):
            return

        def _mean(vals): return sum(vals) / len(vals) if vals else 0

        # Hardcoded thresholds
        tvoc_moderate = 250

        # Bucket readings by hour
        hourly_tvoc: dict[int, list] = {}
        hourly_eco2: dict[int, list] = {}
        for r in rows:
            try:
                hour = datetime.fromisoformat(r["timestamp"]).hour
            except (ValueError, TypeError):
                continue
            if r.get("tvoc") is not None:
                hourly_tvoc.setdefault(hour, []).append(r["tvoc"])
            if r.get("eco2") is not None:
                hourly_eco2.setdefault(hour, []).append(r["eco2"])

        # Find hours with notably high averages
        overall_tvoc_mean = _mean([v for vs in hourly_tvoc.values() for v in vs])
        overall_eco2_mean = _mean([v for vs in hourly_eco2.values() for v in vs])

        problem_hours = []
        for hour in sorted(hourly_tvoc.keys()):
            h_tvoc = _mean(hourly_tvoc.get(hour, []))
            h_eco2 = _mean(hourly_eco2.get(hour, []))
            if (h_tvoc > overall_tvoc_mean * 1.5 and h_tvoc > tvoc_moderate) or \
               (h_eco2 > overall_eco2_mean * 1.5 and h_eco2 > 800):
                problem_hours.append({
                    "hour": hour,
                    "tvoc_avg": int(h_tvoc),
                    "eco2_avg": int(h_eco2),
                })

        if not problem_hours:
            return

        hours_str = ", ".join(f"{h['hour']:02d}:00" for h in problem_hours)
        details = "; ".join(
            f"{h['hour']:02d}:00 (TVOC {h['tvoc_avg']} ppb, eCO₂ {h['eco2_avg']} ppm)"
            for h in problem_hours
        )

        if self._dry_run:
            log.info("[DetectionEngine][DRY-RUN] Would fire: daily_pattern")
            return

        save_inference(
            event_type="daily_pattern",
            severity="info",
            title=f"Recurring pollution pattern — peaks at {hours_str}",
            description=(
                f"Analysis of the last 24 hours shows air quality consistently "
                f"degrades at certain times of day: {details}. "
                f"This suggests a recurring activity (cooking, commute, heating "
                f"schedule, occupancy pattern) is responsible. Identifying the cause "
                f"lets you pre-emptively ventilate."
            ),
            action=(
                f"Consider starting ventilation 15 minutes before the typical "
                f"spike times ({hours_str}). Add annotations at these times to help "
                f"identify the specific activity."
            ),
            evidence={
                "peak_hours": hours_str,
                "details": details,
                "overall_tvoc_avg": f"{int(overall_tvoc_mean)} ppb",
                "overall_eco2_avg": f"{int(overall_eco2_mean)} ppm",
                "hours_analysed": str(len(hourly_tvoc)),
            },
            confidence=0.7,
            start_id=rows[0].get("id"),
            end_id=rows[-1].get("id"),
        )

    def _overnight_buildup(self, fv: FeatureVector) -> None:
        """Detect overnight build-up (eCO₂/TVOC rising while likely sleeping)."""
        rows = self._fetch_recent(minutes=1440)
        if len(rows) < 100:
            return
        if get_recent_inference_by_type("overnight_buildup", hours=23):
            return

        def _mean(vals): return sum(vals) / len(vals) if vals else 0

        # Hardcoded thresholds
        eco2_cognitive = 1000

        # Filter to 23:00–07:00 window
        night_rows = []
        for r in rows:
            try:
                hour = datetime.fromisoformat(r["timestamp"]).hour
            except (ValueError, TypeError):
                continue
            if hour >= 23 or hour < 7:
                night_rows.append(r)

        if len(night_rows) < 20:
            return

        eco2s = [r["eco2"] for r in night_rows if r["eco2"] is not None]
        tvocs = [r["tvoc"] for r in night_rows if r["tvoc"] is not None]
        if len(eco2s) < 20:
            return

        eco2_start = _mean(eco2s[:5])
        eco2_end = _mean(eco2s[-5:])
        eco2_rise = eco2_end - eco2_start

        if eco2_rise > 200 and eco2_end > 800:
            if self._dry_run:
                log.info("[DetectionEngine][DRY-RUN] Would fire: overnight_buildup")
                return

            save_inference(
                event_type="overnight_buildup",
                severity="warning" if eco2_end > eco2_cognitive else "info",
                title=f"Overnight CO₂ build-up — rose by {int(eco2_rise)} ppm",
                description=(
                    f"eCO₂ rose from ~{int(eco2_start)} ppm to ~{int(eco2_end)} ppm "
                    f"between 23:00 and 07:00. This is a common pattern in bedrooms "
                    f"with closed windows — one sleeping adult produces ~200 mL/min of "
                    f"CO₂. By morning, levels can significantly exceed the 1000 ppm "
                    f"cognitive impairment threshold, leading to poor sleep quality "
                    f"and grogginess."
                ),
                action=(
                    "Consider cracking a window at night or running a quiet fan on a "
                    "low setting. Even a small gap provides enough air exchange to keep "
                    "CO₂ below 1000 ppm in most rooms."
                ),
                evidence={
                    "period": "23:00 – 07:00",
                    "eco2_at_start": f"{int(eco2_start)} ppm",
                    "eco2_at_end": f"{int(eco2_end)} ppm",
                    "eco2_rise": f"+{int(eco2_rise)} ppm",
                    "night_readings": str(len(night_rows)),
                    "tvoc_avg": f"{int(_mean(tvocs))} ppb" if tvocs else "N/A",
                },
                confidence=0.85,
                start_id=night_rows[0].get("id"),
                end_id=night_rows[-1].get("id"),
            )

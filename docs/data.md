## Data Role (3/15 pts)

**Owner: Data**

---

### D1. Ingestion quality check ✅

**Status:** Done — `pipeline1_initial/ingestion_checks.py`

**When:** end of pipeline1, after feature engineering, before S3 upload (`run_pipeline.sh` Step 2.5)

**What it checks (per split: train / val / test / production):**
- Row count above minimum threshold
- All FINAL_COLS columns present
- No nulls in any required column
- `is_engaged` only contains 0 or 1
- `is_engaged` mean between 0.50–0.85 (label distribution check)
- `user_skip_rate` in range 0.0–1.0
- `genre_encoded` in range 0–50
- `subgenre_encoded` in range 0–300
- `release_year` in range 1900–2030

**Row count thresholds:**
| Split | Threshold |
|-------|-----------|
| train | > 40,000,000 |
| val | > 5,000,000 |
| test | > 1,000,000 |
| production | > 1,000,000 |

**Hard fail:** raises `ValueError` if any split fails, pipeline stops before uploading to S3.

**Note:** `SKIP_THRESHOLD` changed from 30s → 180s (3 minutes) to fix label imbalance.
Previously 90.8% of rows were `is_engaged=1` (trivial baseline). At 180s threshold, ~70% engaged, giving a meaningful learning signal.

---

### D2. Training set compilation check ✅

**Status:** Done

**Feedback raw format check** — `pipeline2_retrain/feedback_checks.py`
- Row count ≥ 1
- `session_id`, `video_id`, `rank_position` not null
- `actual_is_engaged` in {0, 1}
- `predicted_engagement_prob` between 0.0–1.0

**Compiled retrain dataset check** — `pipeline2_retrain/retrain.py` `_check_retrain_dataset()`
- Row count ≥ 500,000 (set to 1,500 temporarily for testing)
- All required feature columns present
- No nulls in any feature column
- `is_engaged` ratio between 50%–85%

**When:** pipeline2, after `build_retrain_rows()`, before saving to parquet

---

### D3. Live inference data quality + drift monitoring ✅

**Status:** Done

**Live inference data quality** — `serving/lightgbm_app/app.py`
- Pydantic `Field` constraints on `CandidateSong` and `UserFeatures`
- Invalid requests automatically return HTTP 422 with error detail
- Validated ranges: `genre_encoded` 0–50, `subgenre_encoded` 0–300, `release_year` 1900–2030, `user_skip_rate` 0.0–1.0, `user_watch_time_avg` ≥ 0.0

**Drift monitoring** — `pipeline2_retrain/retrain.py` (`detect_drift`)
- Runs inside pipeline2 after feedback is loaded
- Compares feature means between recent feedback and baseline `production.parquet`
- Logs ⚠ DRIFT warning if any feature mean deviates > 20% from baseline
- Drift report written to `metadata.json`

**Features monitored:**
- `user_skip_rate`
- `user_favorite_genre_encoded`
- `user_watch_time_avg`
- `genre_encoded`

---

### D4. Synthetic data hard fail ✅

**Status:** Done — `pipeline1_initial/feature_engineering.py` line 215

**When:** pipeline1 `feature_engineering.py` line 215 — synthetic label generation

**Change needed:**
```python
# Current (only prints, doesn't stop)
print(f"  WARNING: diff {diff:.3f} > 0.05")

# Change to
if diff > 0.05:
    raise ValueError(
        f"Synthetic label distribution check failed: "
        f"diff {diff:.3f} > 0.05 (original={orig_e:.3f}, synthetic={synth_e:.3f}). "
        "Aborting pipeline."
    )
```

---

## Summary

| Task | Status | File |
|------|--------|------|
| D1 Ingestion GX check | ✅ Done | `pipeline1_initial/ingestion_checks.py` |
| D2 Feedback raw format check | ✅ Done | `pipeline2_retrain/feedback_checks.py` |
| D2 Compiled retrain dataset check | ✅ Done | `pipeline2_retrain/retrain.py` `_check_retrain_dataset()` |
| D3 Live inference data quality | ✅ Done | `serving/lightgbm_app/app.py` Field constraints |
| D3 Drift monitoring | ✅ Done | `pipeline2_retrain/retrain.py` `detect_drift()` |
| D4 Synthetic hard fail | ✅ Done | `pipeline1_initial/feature_engineering.py` line 215 |
| SKIP_THRESHOLD 30s → 180s | ✅ Done | `pipeline1_initial/feature_engineering.py` |
| Generator → POST /session/end | ✅ Done | `pipelines/generator/generator.py` |
| Generator → POST /queue 打通 | ✅ Done | `pipelines/generator/generator.py` |
| Serving active_sessions state | ✅ Done (組員) | `serving/lightgbm_app/app.py` |
| Serving MOCK_MODE | ✅ Done | `serving/lightgbm_app/app.py` |

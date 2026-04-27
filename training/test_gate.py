"""
Unit tests for the quality gate logic in train_ranking_processed.py.

The gate functions are copied here verbatim so tests run without the
training script's heavy dependencies (lightgbm, pandas, mlflow server).

Run with:
    cd training
    .venv/bin/pytest test_gate.py -v       # using the local venv
    # or: pip install pytest && pytest test_gate.py -v
"""

import pytest
from unittest.mock import MagicMock

# ── Constants (must match train_ranking_processed.py) ─────────────────────────
MODEL_REGISTRY_NAME = "smartqueue-ranking"
AUC_FLOOR     = 0.75
LOGLOSS_CEILING = 0.65
AUC_MIN_DELTA   = 0.002

# ── Functions under test (copied verbatim from train_ranking_processed.py) ────

def get_baseline_metrics(client):
    for stage in ["Production", "Staging"]:
        try:
            versions = client.get_latest_versions(MODEL_REGISTRY_NAME, stages=[stage])
            if versions:
                run = client.get_run(versions[0].run_id)
                return {
                    "val_auc":     float(run.data.metrics.get("val_auc", 0.0)),
                    "val_logloss": float(run.data.metrics.get("val_logloss", 999.0)),
                }
        except Exception as e:
            print(f"[gate] Could not fetch {stage} model metrics: {e}")
    return None


def evaluate_quality_gate(val_auc, val_logloss, client):
    if val_auc < AUC_FLOOR:
        return False, f"val_auc {val_auc:.4f} < floor {AUC_FLOOR}"
    if val_logloss > LOGLOSS_CEILING:
        return False, f"val_logloss {val_logloss:.4f} > ceiling {LOGLOSS_CEILING}"

    baseline = get_baseline_metrics(client)
    if baseline is not None:
        if val_auc < baseline["val_auc"] + AUC_MIN_DELTA:
            return False, f"val_auc {val_auc:.4f} does not improve baseline {baseline['val_auc']:.4f} by {AUC_MIN_DELTA}"
        print(f"[gate] Beats baseline AUC by required margin: {val_auc:.4f} > {baseline['val_auc']:.4f} + {AUC_MIN_DELTA}")
    else:
        print("[gate] No registered model found — applying absolute thresholds only")

    return True, "all checks passed"


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_client(production_auc=None, staging_auc=None):
    """Build a mock MlflowClient with configurable Production / Staging versions."""
    client = MagicMock()

    def get_latest_versions(name, stages):
        stage = stages[0]
        auc = production_auc if stage == "Production" else staging_auc
        if auc is None:
            return []
        version = MagicMock()
        version.run_id = f"fake-run-{stage}"
        run = MagicMock()
        run.data.metrics.get.side_effect = lambda key, default=None: (
            auc if key == "val_auc" else 0.45
        )
        client.get_run.return_value = run
        return [version]

    client.get_latest_versions.side_effect = get_latest_versions
    return client


# ── get_baseline_metrics ──────────────────────────────────────────────────────

class TestGetBaselineMetrics:
    def test_returns_none_when_registry_empty(self):
        assert get_baseline_metrics(make_client()) is None

    def test_returns_production_when_present(self):
        result = get_baseline_metrics(make_client(production_auc=0.87, staging_auc=0.85))
        assert result["val_auc"] == pytest.approx(0.87)

    def test_falls_back_to_staging_when_no_production(self):
        result = get_baseline_metrics(make_client(staging_auc=0.85))
        assert result["val_auc"] == pytest.approx(0.85)

    def test_returns_none_on_client_exception(self):
        client = MagicMock()
        client.get_latest_versions.side_effect = Exception("MLflow unavailable")
        assert get_baseline_metrics(client) is None


# ── evaluate_quality_gate ─────────────────────────────────────────────────────

class TestEvaluateQualityGate:
    def test_first_model_passes_with_no_baseline(self):
        passed, _ = evaluate_quality_gate(0.85, 0.45, make_client())
        assert passed is True

    def test_fails_auc_floor(self):
        passed, reason = evaluate_quality_gate(0.74, 0.45, make_client())
        assert passed is False
        assert "floor" in reason

    def test_fails_logloss_ceiling(self):
        passed, reason = evaluate_quality_gate(0.85, 0.66, make_client())
        assert passed is False
        assert "ceiling" in reason

    def test_fails_when_not_improving_over_staging(self):
        # Same AUC as existing Staging — must improve by AUC_MIN_DELTA (0.002)
        passed, reason = evaluate_quality_gate(0.8495, 0.45, make_client(staging_auc=0.8495))
        assert passed is False
        assert "baseline" in reason

    def test_passes_when_improving_over_staging(self):
        passed, _ = evaluate_quality_gate(0.852, 0.45, make_client(staging_auc=0.8495))
        assert passed is True

    def test_production_takes_priority_over_staging(self):
        # Production is higher — gate compares against Production, not lower Staging
        passed, _ = evaluate_quality_gate(0.871, 0.45, make_client(production_auc=0.87, staging_auc=0.85))
        assert passed is False  # 0.871 < 0.87 + 0.002

    def test_just_above_delta_boundary_passes(self):
        passed, _ = evaluate_quality_gate(0.852, 0.45, make_client(staging_auc=0.85))
        assert passed is True  # 0.852 >= 0.85 + 0.002

    def test_just_below_delta_boundary_fails(self):
        passed, _ = evaluate_quality_gate(0.8519, 0.45, make_client(staging_auc=0.85))
        assert passed is False

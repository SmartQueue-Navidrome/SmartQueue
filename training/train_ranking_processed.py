"""
SmartQueue Stage B - Personalized Ranking Model Training
Trains a skip/non-skip prediction model using processed Pipeline 1 output.

Input data: processed/train.parquet (7 features + label, with user features)
Features: release_year, context_segment, genre_encoded, subgenre_encoded,
          user_skip_rate, user_favorite_genre_encoded, user_watch_time_avg

Usage:
    python train_ranking_processed.py configs/stage_b_lgbm_v4.yaml
"""

import os
import sys
import time
import yaml
import joblib
import mlflow
import mlflow.sklearn
import mlflow.lightgbm
from mlflow.tracking import MlflowClient
import pandas as pd
import numpy as np
import pyarrow.parquet as pq
import lightgbm as lgb
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, log_loss

# ── Quality Gate Config ────────────────────────────────────────────────────────
MODEL_REGISTRY_NAME = "smartqueue-ranking"
AUC_FLOOR = 0.75          # absolute minimum — 50% of the way from random (0.5) to perfect (1.0)
LOGLOSS_CEILING = 0.65    # absolute maximum logloss allowed
AUC_MIN_DELTA = 0.002     # must improve production AUC by at least this margin to deploy
# ──────────────────────────────────────────────────────────────────────────────

# ── Local Model Save Path (for serving fallback / rollback) ───────────────────
LOCAL_MODEL_PATH = "/data/models/ranking_model_latest.pkl"
# ──────────────────────────────────────────────────────────────────────────────

FEATURE_COLS = [
    "release_year",
    "context_segment",
    "genre_encoded",
    "subgenre_encoded",
    "user_skip_rate",
    "user_favorite_genre_encoded",
    "user_watch_time_avg",
]

NEEDED_COLS = ["session_id", "is_engaged"] + FEATURE_COLS


def load_config(config_path: str) -> dict:
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)
    return cfg


BASE_DATA_S3_KEY   = "processed/train.parquet"
BASE_DATA_LOCAL    = "/tmp/base_train.parquet"


def _load_parquet_rows(path: str, n: int) -> pd.DataFrame:
    pf = pq.ParquetFile(path)
    batches, rows_read = [], 0
    for batch in pf.iter_batches(batch_size=min(n, 10000), columns=NEEDED_COLS):
        batches.append(batch.to_pandas())
        rows_read += len(batches[-1])
        if rows_read >= n:
            break
    return pd.concat(batches, ignore_index=True).head(n) if batches else pd.DataFrame()


def _download_base_data() -> str | None:
    """Download processed/train.parquet from S3 for combining with retrain data."""
    bucket   = os.environ.get("S3_BUCKET")
    endpoint = os.environ.get("S3_ENDPOINT") or os.environ.get("MLFLOW_S3_ENDPOINT_URL")
    key_id   = os.environ.get("AWS_ACCESS_KEY_ID")
    secret   = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not all([bucket, endpoint, key_id, secret]):
        return None
    try:
        import boto3
        s3 = boto3.client("s3", endpoint_url=endpoint,
                          aws_access_key_id=key_id,
                          aws_secret_access_key=secret)
        s3.download_file(bucket, BASE_DATA_S3_KEY, BASE_DATA_LOCAL)
        print(f"[data] Downloaded base data from s3://{bucket}/{BASE_DATA_S3_KEY}")
        return BASE_DATA_LOCAL
    except Exception as e:
        print(f"[data] Could not download base data: {e}")
        return None


def load_and_prepare_data(cfg: dict) -> tuple[pd.DataFrame, dict]:
    """
    Load training data.  In retrain mode (DATA_PATH env var set), combines
    retrain data with historical base data from S3 to maintain ~max_samples total.
    Returns (dataframe, data_stats_dict) for MLflow logging.
    """
    data_path   = os.environ.get("DATA_PATH") or cfg["data_path"]
    max_samples = cfg.get("max_samples", 200000)
    is_retrain  = bool(os.environ.get("DATA_PATH"))

    if is_retrain:
        retrain_df = _load_parquet_rows(data_path, max_samples)
        n_retrain  = len(retrain_df)
        print(f"[data] Retrain rows: {n_retrain:,} from {data_path}")

        n_base    = max(0, max_samples - n_retrain)
        base_path = _download_base_data() if n_base > 0 else None
        if base_path:
            base_df = _load_parquet_rows(base_path, n_base)
            df      = pd.concat([retrain_df, base_df], ignore_index=True)
            print(f"[data] Combined: {n_retrain:,} retrain + {len(base_df):,} base = {len(df):,} rows")
            stats = {"n_retrain_rows": n_retrain, "n_base_rows": len(base_df)}
        else:
            df    = retrain_df
            print(f"[data] Retrain only (no base data): {len(df):,} rows")
            stats = {"n_retrain_rows": n_retrain, "n_base_rows": 0}
    else:
        df    = _load_parquet_rows(data_path, max_samples)
        print(f"[data] Loaded {len(df):,} rows from {data_path}")
        stats = {"n_retrain_rows": 0, "n_base_rows": len(df)}

    print(f"[data] Label distribution:")
    print(f"  engaged (1): {(df['is_engaged'] == 1).sum()}")
    print(f"  skipped (0): {(df['is_engaged'] == 0).sum()}")
    print(f"[data] Features: {FEATURE_COLS}")
    return df, stats


def split_by_session(df: pd.DataFrame, train_ratio: float = 0.8, seed: int = 42):
    print("[split] Splitting by session_id to prevent data leakage...")
    unique_sessions = df["session_id"].unique()
    np.random.seed(seed)
    np.random.shuffle(unique_sessions)

    split_idx = int(len(unique_sessions) * train_ratio)
    train_sessions = set(unique_sessions[:split_idx])

    train_mask = df["session_id"].isin(train_sessions)
    train_df = df[train_mask]
    val_df = df[~train_mask]

    print(f"[split] Train sessions: {split_idx}, Val sessions: {len(unique_sessions) - split_idx}")
    print(f"[split] Train rows: {len(train_df)}, Val rows: {len(val_df)}")
    return train_df, val_df


def train_logistic_regression(X_train, y_train, X_val, y_val, cfg):
    print("[train] Training Logistic Regression baseline...")
    model_params = cfg.get("model_params", {})
    model = LogisticRegression(
        C=model_params.get("C", 1.0),
        max_iter=model_params.get("max_iter", 1000),
        solver=model_params.get("solver", "lbfgs"),
    )
    model.fit(X_train, y_train)
    y_pred = model.predict_proba(X_val)[:, 1]
    return model, y_pred


def train_lightgbm(X_train, y_train, X_val, y_val, cfg):
    print("[train] Training LightGBM...")
    model_params = cfg.get("model_params", {})
    model_params.setdefault("objective", "binary")
    model_params.setdefault("metric", "binary_logloss")
    model_params.setdefault("verbosity", -1)

    train_data = lgb.Dataset(X_train, label=y_train)
    val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)

    model = lgb.train(
        model_params,
        train_data,
        valid_sets=[train_data, val_data],
        valid_names=["train", "val"],
        num_boost_round=cfg.get("num_boost_round", 200),
        callbacks=[lgb.log_evaluation(period=50)],
    )
    y_pred = model.predict(X_val)
    return model, y_pred


def save_model_locally(model) -> None:
    """
    Save model to local filesystem as fallback for serving.
    Overwrites ranking_model_latest.pkl so serving always has the newest passing model.
    """
    os.makedirs(os.path.dirname(LOCAL_MODEL_PATH), exist_ok=True)
    joblib.dump(model, LOCAL_MODEL_PATH)
    print(f"[save] Model saved locally to {LOCAL_MODEL_PATH}")


def get_baseline_metrics(client: MlflowClient) -> dict | None:
    """
    Fetch val_auc and val_logloss of the best existing model in registry.
    Checks Production first, then falls back to the latest Staging version.
    Returns None if no registered model exists yet.
    """
    for stage in ["Production", "Staging"]:
        try:
            versions = client.get_latest_versions(MODEL_REGISTRY_NAME, stages=[stage])
            if versions:
                run = client.get_run(versions[0].run_id)
                return {
                    "val_auc": float(run.data.metrics.get("val_auc", 0.0)),
                    "val_logloss": float(run.data.metrics.get("val_logloss", 999.0)),
                }
        except Exception as e:
            print(f"[gate] Could not fetch {stage} model metrics: {e}")
    return None


def evaluate_quality_gate(val_auc: float, val_logloss: float, client: MlflowClient) -> tuple[bool, str]:
    """
    Returns (passed: bool, reason: str).

    Gate rules (all must pass):
      1. val_auc >= AUC_FLOOR
      2. val_logloss <= LOGLOSS_CEILING
      3. val_auc > current best model's val_auc + AUC_MIN_DELTA  (skipped if no registered model)
    """
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


def register_model(run_id: str, val_auc: float, val_logloss: float, client: MlflowClient) -> None:
    """
    Register model to MLflow Model Registry, transition to Staging.
    Dan (serving) will manually promote to Production when ready.
    """
    model_uri = f"runs:/{run_id}/model"
    mv = mlflow.register_model(model_uri, MODEL_REGISTRY_NAME)

    client.transition_model_version_stage(
        name=MODEL_REGISTRY_NAME,
        version=mv.version,
        stage="Staging",
        archive_existing_versions=True,
    )
    client.set_model_version_tag(MODEL_REGISTRY_NAME, mv.version, "val_auc", f"{val_auc:.4f}")
    client.set_model_version_tag(MODEL_REGISTRY_NAME, mv.version, "val_logloss", f"{val_logloss:.4f}")
    client.set_model_version_tag(MODEL_REGISTRY_NAME, mv.version, "gate_status", "passed")

    print(f"[registry] Registered as '{MODEL_REGISTRY_NAME}' version {mv.version} → Staging")
    print(f"[registry] Serving team can promote version {mv.version} to Production when ready")


def main():
    if len(sys.argv) < 2:
        print("Usage: python train_ranking_processed.py <config_path>")
        sys.exit(1)

    config_path = sys.argv[1]
    cfg = load_config(config_path)
    print(f"[config] Loaded: {config_path}")
    print(f"[config] model_type={cfg['model_type']}")

    mlflow.set_experiment("smartqueue-stage-b")

    with mlflow.start_run(log_system_metrics=False):
        # Log all config params
        flat_params = {}
        for k, v in cfg.items():
            if isinstance(v, dict):
                for k2, v2 in v.items():
                    flat_params[f"{k}.{k2}"] = v2
            else:
                flat_params[k] = v
        mlflow.log_params(flat_params)

        git_sha = os.popen("git rev-parse --short HEAD 2>/dev/null").read().strip()
        if git_sha:
            mlflow.set_tag("git_sha", git_sha)

        gpu_info = os.popen("nvidia-smi 2>/dev/null || echo 'No GPU - CPU only'").read()
        mlflow.log_text(gpu_info, "environment-info.txt")

        # --- Load data ---
        df, data_stats = load_and_prepare_data(cfg)
        mlflow.log_params(data_stats)

        # --- Split ---
        train_df, val_df = split_by_session(df, cfg.get("train_ratio", 0.8), cfg.get("random_seed", 42))

        X_train = train_df[FEATURE_COLS].values
        y_train = train_df["is_engaged"].values
        X_val = val_df[FEATURE_COLS].values
        y_val = val_df["is_engaged"].values

        # --- Train ---
        start_time = time.time()
        model_type = cfg["model_type"]
        client = MlflowClient()

        if model_type == "logistic_regression":
            model, y_pred = train_logistic_regression(X_train, y_train, X_val, y_val, cfg)
        elif model_type == "lightgbm":
            model, y_pred = train_lightgbm(X_train, y_train, X_val, y_val, cfg)
            importances = model.feature_importance(importance_type="gain")
            for feat, imp in zip(FEATURE_COLS, importances):
                mlflow.log_metric(f"feat_importance_{feat}", float(imp))
        else:
            raise ValueError(f"Unknown model_type: {model_type}")

        training_time = time.time() - start_time

        # --- Evaluate ---
        val_auc = roc_auc_score(y_val, y_pred)
        val_logloss = log_loss(y_val, y_pred)

        print(f"\n{'='*50}")
        print(f"[result] Validation AUC:     {val_auc:.4f}")
        print(f"[result] Validation LogLoss: {val_logloss:.4f}")
        print(f"[result] Training time:      {training_time:.1f}s")
        print(f"{'='*50}\n")

        mlflow.log_metrics({
            "val_auc": val_auc,
            "val_logloss": val_logloss,
            "training_time_seconds": training_time,
            "train_samples": len(X_train),
            "val_samples": len(X_val),
            "num_features": len(FEATURE_COLS),
        })

        if model_type == "logistic_regression":
            mlflow.sklearn.log_model(model, "model")
        elif model_type == "lightgbm":
            mlflow.lightgbm.log_model(model, "model")

        print("[done] Run logged to MLflow successfully!")

        # ── Quality Gate ───────────────────────────────────────────────────────
        run_id = mlflow.active_run().info.run_id

        print(f"\n[gate] Evaluating quality gate...")
        print(f"[gate]   val_auc     = {val_auc:.4f}  (floor: {AUC_FLOOR}, min_delta_vs_prod: {AUC_MIN_DELTA})")
        print(f"[gate]   val_logloss = {val_logloss:.4f}  (ceiling: {LOGLOSS_CEILING})")

        passed, reason = evaluate_quality_gate(val_auc, val_logloss, client)

        mlflow.log_param("gate_passed", passed)
        mlflow.log_param("gate_reason", reason)

        if passed:
            print(f"[gate] ✅ PASSED — {reason}")
            save_model_locally(model)
            register_model(run_id, val_auc, val_logloss, client)
        else:
            print(f"[gate] ❌ FAILED — {reason}")
            print(f"[gate] Model logged to MLflow but NOT registered or saved locally.")
        # ──────────────────────────────────────────────────────────────────────

        with open("/tmp/run_id.txt", "w") as f:
            f.write(run_id)


if __name__ == "__main__":
    main()

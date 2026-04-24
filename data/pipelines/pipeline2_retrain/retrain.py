"""
Pipeline 2 - Batch Retrain

Reads feedback JSONL files from two S3 prefixes and builds a retrain dataset:

  feedback/{date}/generator/ → join production.parquet for features
  feedback/{date}/real/      → join Postgres song_catalog + user_profiles for features

Output:
  data/retrain/v{YYYYMMDD}/
    train.parquet   — retrain rows (generator + real combined)
    metadata.json   — feedback count, label distribution, timestamp

Usage:
    python retrain.py [--data-dir /path/to/SmartQueue/data]
"""

import os
import sys
import json
import time
import argparse
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import psycopg2
import psycopg2.extras

from feedback_checks import run_checks

sys.path.insert(0, str(Path(__file__).resolve().parent / "utils"))
import s3

LOCAL_MODE = os.getenv("LOCAL_MODE", "false").lower() == "true"

SKIP_THRESHOLD = 30
DEFAULT_DATA   = Path(os.getenv("DATA_DIR", "/app/data"))


# ── Postgres connection ───────────────────────────────────────────────────────

def _pg_conn():
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
    )


# ── user feature computation (same logic as feature_service.py) ───────────────

def compute_user_features(events: list[dict]) -> dict:
    times  = [e["time_in_video"] for e in events]
    genres = [e["genre_encoded"]  for e in events]

    skip_rate = round(sum(1 for t in times if t < SKIP_THRESHOLD) / len(times), 4)
    watch_avg = round(sum(times) / len(times), 2)

    genre_counts = {}
    for g in genres:
        genre_counts[g] = genre_counts.get(g, 0) + 1
    fav_genre = max(genre_counts, key=genre_counts.get)

    return {
        "user_skip_rate":              skip_rate,
        "user_favorite_genre_encoded": fav_genre,
        "user_watch_time_avg":         watch_avg,
    }


# ── load feedback ─────────────────────────────────────────────────────────────

def _read_jsonl_dir(directory: Path, source: str) -> list[dict]:
    records = []
    for f in sorted(directory.glob("*.jsonl")):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append({**json.loads(line), "source": source})
    return records


def load_feedback(feedback_dir: Path, date_str: str) -> pd.DataFrame:
    all_records = []

    if LOCAL_MODE:
        for source in ("generator", "real"):
            src_dir = feedback_dir / date_str / source
            if src_dir.exists():
                all_records.extend(_read_jsonl_dir(src_dir, source))
        if not all_records:
            raise FileNotFoundError(f"No feedback files found for date {date_str} in {feedback_dir}")
    else:
        for source in ("generator", "real"):
            prefix = f"feedback/{date_str}/{source}/"
            objects = s3.list_objects(prefix=prefix)
            if not objects:
                continue
            src_dir = feedback_dir / date_str / source
            src_dir.mkdir(parents=True, exist_ok=True)

            def _download(obj, d=src_dir):
                s3.download_file(obj["Key"], d / Path(obj["Key"]).name)

            with ThreadPoolExecutor(max_workers=32) as pool:
                list(pool.map(_download, objects))

            all_records.extend(_read_jsonl_dir(src_dir, source))

        if not all_records:
            raise FileNotFoundError(f"No feedback files found on S3 for date {date_str}")

    df = pd.DataFrame(all_records)
    gen_count  = int((df["source"] == "generator").sum())
    real_count = int((df["source"] == "real").sum())
    print(f"  Loaded {len(df):,} records from {df['session_id'].nunique():,} sessions "
          f"(generator={gen_count:,}, real={real_count:,})")
    return df


# ── generator path: join production.parquet ───────────────────────────────────

def _build_generator_rows(gen_df: pd.DataFrame, production_df: pd.DataFrame) -> pd.DataFrame:
    prod_video = production_df[["video_id", "genre_encoded", "subgenre_encoded",
                                "release_year", "context_segment"]].drop_duplicates("video_id")
    merged = gen_df.merge(prod_video, on="video_id", how="inner")

    dropped = len(gen_df) - len(merged)
    if dropped:
        print(f"  Generator: {dropped:,} rows dropped (video_id not in production)")

    prod_profile = production_df.groupby("session_id")[
        ["user_skip_rate", "user_favorite_genre_encoded", "user_watch_time_avg"]
    ].first()

    prod_time = production_df[["session_id", "video_id", "time_in_video"]]
    merged = merged.merge(prod_profile, on="session_id", how="left")
    merged = merged.merge(prod_time, on=["session_id", "video_id"], how="left")
    merged["time_in_video"] = merged["time_in_video"].fillna(10.0)

    rows = []
    for session_id, group in merged.groupby("session_id"):
        pre = prod_profile.loc[session_id] if session_id in prod_profile.index else None
        events = []
        if pre is not None:
            events.append({
                "time_in_video": float(pre["user_watch_time_avg"]),
                "genre_encoded": int(pre["user_favorite_genre_encoded"]),
            })
        for _, row in group.iterrows():
            events.append({
                "time_in_video": float(row["time_in_video"]),
                "genre_encoded": int(row["genre_encoded"]),
            })
        uf = compute_user_features(events)
        for _, row in group.iterrows():
            rows.append({
                "session_id":                  session_id,
                "video_id":                    row["video_id"],
                "is_engaged":                  int(row["actual_is_engaged"]),
                "genre_encoded":               int(row["genre_encoded"]),
                "subgenre_encoded":            int(row["subgenre_encoded"]),
                "release_year":                int(row["release_year"]),
                "context_segment":             int(row["context_segment"]),
                "user_skip_rate":              uf["user_skip_rate"],
                "user_favorite_genre_encoded": uf["user_favorite_genre_encoded"],
                "user_watch_time_avg":         uf["user_watch_time_avg"],
            })

    return pd.DataFrame(rows)


# ── real-user path: join Postgres song_catalog + user_profiles ────────────────

def _build_real_rows(real_df: pd.DataFrame) -> pd.DataFrame:
    conn = _pg_conn()
    try:
        video_ids = real_df["video_id"].dropna().unique().tolist()
        user_ids  = real_df["user_id"].dropna().unique().tolist()

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT navidrome_id, genre_encoded, subgenre_encoded, release_year, context_segment "
                "FROM song_catalog WHERE navidrome_id = ANY(%s)",
                (video_ids,),
            )
            song_map = {r["navidrome_id"]: dict(r) for r in cur.fetchall()}

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id, skip_rate, fav_genre_encoded, watch_time_avg "
                "FROM user_profiles WHERE user_id = ANY(%s)",
                (user_ids,),
            )
            user_map = {r["user_id"]: dict(r) for r in cur.fetchall()}
    finally:
        conn.close()

    rows = []
    dropped = 0
    for _, row in real_df.iterrows():
        song = song_map.get(row["video_id"])
        if song is None:
            dropped += 1
            continue

        user = user_map.get(row.get("user_id", ""))
        if user is not None:
            user_skip_rate = float(user["skip_rate"])
            user_fav_genre = int(user["fav_genre_encoded"]) if user["fav_genre_encoded"] >= 0 else int(song["genre_encoded"])
            user_watch_avg = float(user["watch_time_avg"])
        else:
            user_skip_rate = 0.5
            user_fav_genre = int(song["genre_encoded"])
            user_watch_avg = 0.0

        rows.append({
            "session_id":                  row["session_id"],
            "video_id":                    row["video_id"],
            "is_engaged":                  int(row["actual_is_engaged"]),
            "genre_encoded":               int(song["genre_encoded"]),
            "subgenre_encoded":            int(song["subgenre_encoded"]),
            "release_year":                int(song["release_year"]),
            "context_segment":             int(song["context_segment"]),
            "user_skip_rate":              user_skip_rate,
            "user_favorite_genre_encoded": user_fav_genre,
            "user_watch_time_avg":         user_watch_avg,
        })

    if dropped:
        print(f"  Real-user: {dropped:,} rows dropped (video_id not in song_catalog)")

    return pd.DataFrame(rows)


# ── combined build ────────────────────────────────────────────────────────────

def build_retrain_rows(feedback_df: pd.DataFrame, production_df: pd.DataFrame) -> pd.DataFrame:
    gen_df  = feedback_df[feedback_df["source"] == "generator"]
    real_df = feedback_df[feedback_df["source"] == "real"]

    frames = []

    if len(gen_df):
        gen_rows = _build_generator_rows(gen_df, production_df)
        print(f"  Generator rows: {len(gen_rows):,}")
        frames.append(gen_rows)

    if len(real_df):
        real_rows = _build_real_rows(real_df)
        print(f"  Real-user rows: {len(real_rows):,}")
        frames.append(real_rows)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


# ── drift monitoring ─────────────────────────────────────────────────────────

DRIFT_FEATURES = [
    "user_skip_rate",
    "user_favorite_genre_encoded",
    "user_watch_time_avg",
    "genre_encoded",
]
DRIFT_THRESHOLD = 0.20


def detect_drift(new_rows: pd.DataFrame, production_df: pd.DataFrame) -> dict:
    print("  Drift monitoring:")
    drift_report = {}
    any_drift = False

    for feat in DRIFT_FEATURES:
        baseline_mean = production_df[feat].mean()
        recent_mean   = new_rows[feat].mean()
        diff_pct = 0.0 if baseline_mean == 0 else abs(recent_mean - baseline_mean) / abs(baseline_mean)

        drifted = diff_pct > DRIFT_THRESHOLD
        status  = "⚠ DRIFT" if drifted else "✓"
        print(f"    {status} {feat}: baseline={baseline_mean:.3f}  recent={recent_mean:.3f}  diff={diff_pct:.1%}")

        drift_report[feat] = {
            "baseline_mean": round(float(baseline_mean), 4),
            "recent_mean":   round(float(recent_mean), 4),
            "diff_pct":      round(float(diff_pct), 4),
            "drifted":       bool(drifted),
        }
        if drifted:
            any_drift = True

    if any_drift:
        print("  [WARN] Drift detected — model may need attention.")
    else:
        print("  No significant drift detected.")

    return drift_report


# ── retrain dataset quality check ─────────────────────────────────────────────

RETRAIN_FEATURE_COLS = [
    "session_id", "video_id", "is_engaged",
    "genre_encoded", "subgenre_encoded", "release_year", "context_segment",
    "user_skip_rate", "user_favorite_genre_encoded", "user_watch_time_avg",
]
RETRAIN_MIN_ROWS = 1500


def _check_retrain_dataset(df: pd.DataFrame) -> None:
    print("  Retrain dataset quality checks:")
    errors = []

    if len(df) < RETRAIN_MIN_ROWS:
        errors.append(f"✗ row count {len(df):,} < {RETRAIN_MIN_ROWS:,} (not enough data to retrain)")
    else:
        print(f"    ✓ row count {len(df):,} ≥ {RETRAIN_MIN_ROWS:,}")

    missing = [c for c in RETRAIN_FEATURE_COLS if c not in df.columns]
    if missing:
        errors.append(f"✗ missing columns: {missing}")
    else:
        print(f"    ✓ all required columns present")

    null_cols = [c for c in RETRAIN_FEATURE_COLS if df[c].isnull().any()]
    if null_cols:
        errors.append(f"✗ null values found in: {null_cols}")
    else:
        print(f"    ✓ no nulls in feature columns")

    engaged_rate = df["is_engaged"].mean()
    if not (0.50 <= engaged_rate <= 0.85):
        errors.append(
            f"✗ is_engaged ratio {engaged_rate:.1%} outside 50%–85% "
            "(degenerate label distribution)"
        )
    else:
        print(f"    ✓ is_engaged ratio {engaged_rate:.1%} within 50%–85%")

    if errors:
        raise ValueError(
            "Retrain dataset quality checks failed:\n" +
            "\n".join(f"  {e}" for e in errors)
        )
    print("  All retrain checks passed.")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA))
    parser.add_argument("--date", default=None,
                        help="Date to process (YYYYMMDD). Defaults to yesterday (UTC).")
    args = parser.parse_args()

    data_dir      = Path(args.data_dir)
    feedback_dir  = data_dir / "feedback"
    processed_dir = data_dir / "processed"
    yesterday     = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
    date_str      = args.date or yesterday
    retrain_dir   = data_dir / "retrain" / f"v{date_str}"
    retrain_dir.mkdir(parents=True, exist_ok=True)

    total_start = time.perf_counter()

    # 1. Load feedback (both sources, tagged with source column)
    print("\n[1/4] Loading feedback ...")
    t = time.perf_counter()
    feedback_df = load_feedback(feedback_dir, date_str)
    print(f"[1/4] Done in {time.perf_counter()-t:.1f}s")

    # 1b. Data quality checks
    print("\n[1b] Running data quality checks ...")
    t = time.perf_counter()
    run_checks(feedback_df)
    print(f"[1b] Done in {time.perf_counter()-t:.1f}s")

    # 2. Load production.parquet (needed for generator path)
    print("\n[2/4] Loading production.parquet ...")
    t = time.perf_counter()
    prod_parquet = processed_dir / "production.parquet"
    if not LOCAL_MODE and not prod_parquet.exists():
        print("  Downloading production.parquet from S3 ...")
        processed_dir.mkdir(parents=True, exist_ok=True)
        s3.download_file("processed/production.parquet", prod_parquet)
    production_df = pd.read_parquet(prod_parquet)
    print(f"  {len(production_df):,} rows  ({time.perf_counter()-t:.1f}s)")

    # 3. Build retrain rows (generator → production.parquet, real → Postgres)
    print("\n[3/4] Building retrain rows ...")
    t = time.perf_counter()
    new_rows = build_retrain_rows(feedback_df, production_df)
    print(f"  {len(new_rows):,} total rows built  ({time.perf_counter()-t:.1f}s)")

    # 3b. Quality checks
    print("\n[3b] Running retrain dataset quality checks ...")
    t = time.perf_counter()
    _check_retrain_dataset(new_rows)
    print(f"[3b] Done in {time.perf_counter()-t:.1f}s")

    # 3c. Drift monitoring
    print("\n[3c] Drift monitoring ...")
    drift_report = detect_drift(new_rows, production_df)

    # 4. Save
    print("\n[4/4] Saving retrain dataset ...")
    t = time.perf_counter()
    out_path = retrain_dir / "train.parquet"
    new_rows.to_parquet(out_path, index=False)
    print(f"  {len(new_rows):,} rows → {out_path}  ({time.perf_counter()-t:.1f}s)")

    gen_count  = int((feedback_df["source"] == "generator").sum())
    real_count = int((feedback_df["source"] == "real").sum())
    metadata = {
        "timestamp":          datetime.now(timezone.utc).isoformat(),
        "version":            f"v{date_str}",
        "feedback_generator": gen_count,
        "feedback_real":      real_count,
        "feedback_total":     gen_count + real_count,
        "feedback_sessions":  int(feedback_df["session_id"].nunique()),
        "total_rows":         int(len(new_rows)),
        "engaged_rate":       round(float(new_rows["is_engaged"].mean()), 4),
        "drift":              drift_report,
    }
    with open(retrain_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    elapsed = time.perf_counter() - total_start
    print(f"\n{'='*50}")
    print(f"Total time: {elapsed:.1f}s")
    print(f"Retrain data → {retrain_dir}")
    print(f"{'='*50}")

    # 5. Upload to S3 + cleanup
    if not LOCAL_MODE:
        print("\n[5/4] Uploading to S3 ...")
        t = time.perf_counter()
        s3_prefix = f"retrain/v{date_str}"
        n = s3.upload_dir(retrain_dir, s3_prefix)
        print(f"  {n} file(s) → s3://{s3.BUCKET}/{s3_prefix}/  ({time.perf_counter()-t:.1f}s)")

        print("\n[6/4] Cleaning up local files ...")
        for source in ("generator", "real"):
            src_dir = feedback_dir / date_str / source
            if src_dir.exists():
                shutil.rmtree(src_dir)
                print(f"  Deleted {src_dir}")
        shutil.rmtree(retrain_dir)
        print(f"  Deleted {retrain_dir}")
    else:
        print("\nLOCAL_MODE=true — skipping S3 upload and cleanup.")


if __name__ == "__main__":
    main()

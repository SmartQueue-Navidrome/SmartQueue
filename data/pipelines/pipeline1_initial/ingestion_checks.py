"""
Ingestion quality checks for pipeline1 output using Great Expectations.

Validates all four processed splits (train, val, test, production) after
feature engineering. Hard fails if any check doesn't pass.

Checks per split:
  - All required columns present
  - Row count above minimum threshold
  - No nulls in any required column
  - is_engaged only contains 0 or 1
  - is_engaged ratio between 50% and 85%
  - user_skip_rate in range 0.0–1.0
  - genre_encoded in range 0–50
  - subgenre_encoded in range 0–300
  - release_year in range 1900–2030

Usage:
    python ingestion_checks.py --output-dir /path/to/data
"""

import argparse
import sys
from pathlib import Path

import great_expectations as gx
import pandas as pd

REQUIRED_COLS = [
    "session_id",
    "video_id",
    "is_engaged",
    "genre_encoded",
    "subgenre_encoded",
    "release_year",
    "context_segment",
    "user_skip_rate",
    "user_favorite_genre_encoded",
    "user_watch_time_avg",
]

ROW_COUNT_THRESHOLDS = {
    "train":      40_000_000,
    "val":         5_000_000,
    "test":        1_000_000,
    "production":  1_000_000,
}


def run_checks(split_name: str, df: pd.DataFrame) -> bool:
    print(f"\n  [{split_name}] {len(df):,} rows")

    context = gx.get_context(mode="ephemeral")
    ds = context.data_sources.add_pandas(f"ingestion_{split_name}")
    asset = ds.add_dataframe_asset(f"{split_name}_df")
    batch_def = asset.add_batch_definition_whole_dataframe("batch")
    batch = batch_def.get_batch(batch_parameters={"dataframe": df})

    suite = context.suites.add(gx.ExpectationSuite(name=f"{split_name}_suite"))

    # Row count
    suite.add_expectation(
        gx.expectations.ExpectTableRowCountToBeBetween(
            min_value=ROW_COUNT_THRESHOLDS[split_name]
        )
    )

    # Required columns exist
    suite.add_expectation(
        gx.expectations.ExpectTableColumnsToMatchSet(
            column_set=REQUIRED_COLS,
            exact_match=False,
        )
    )

    # No nulls in required columns
    for col in REQUIRED_COLS:
        suite.add_expectation(
            gx.expectations.ExpectColumnValuesToNotBeNull(column=col)
        )

    # is_engaged only 0 or 1
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="is_engaged", value_set=[0, 1]
        )
    )

    # is_engaged ratio 50%–85%
    suite.add_expectation(
        gx.expectations.ExpectColumnMeanToBeBetween(
            column="is_engaged", min_value=0.50, max_value=0.85
        )
    )

    # user_skip_rate in 0.0–1.0
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeBetween(
            column="user_skip_rate", min_value=0.0, max_value=1.0
        )
    )

    # genre_encoded in 0–50
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeBetween(
            column="genre_encoded", min_value=0, max_value=50
        )
    )

    # subgenre_encoded in 0–300
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeBetween(
            column="subgenre_encoded", min_value=0, max_value=300
        )
    )

    # release_year in 1900–2030
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeBetween(
            column="release_year", min_value=1900, max_value=2030
        )
    )

    result = batch.validate(suite)

    all_passed = True
    for r in result.results:
        status = "✓" if r["success"] else "✗"
        kwargs = r["expectation_config"].get("kwargs", {})
        col = kwargs.get("column", "")
        min_v = kwargs.get("min_value")
        max_v = kwargs.get("max_value")
        value_set = kwargs.get("value_set")

        check_type = r["expectation_config"]["type"]
        if check_type == "expect_table_row_count_to_be_between":
            label = f"row count ≥ {min_v:,}"
        elif check_type == "expect_table_columns_to_match_set":
            label = f"required columns present"
        elif check_type == "expect_column_values_to_not_be_null":
            label = f"{col}: no nulls"
        elif check_type == "expect_column_values_to_be_in_set":
            label = f"{col}: values in {value_set}"
        elif check_type == "expect_column_mean_to_be_between":
            label = f"{col}: mean between {min_v}–{max_v}"
        elif check_type == "expect_column_values_to_be_between":
            label = f"{col}: values between {min_v}–{max_v}"
        else:
            label = f"{check_type}({col})" if col else check_type

        print(f"    {status} {label}")
        if not r["success"]:
            all_passed = False

    return all_passed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=".", help="Base data directory")
    args = parser.parse_args()

    processed_dir = Path(args.output_dir) / "processed"
    splits = ["train", "val", "test", "production"]

    print("============================================")
    print(" Ingestion Quality Checks (Great Expectations)")
    print("============================================")

    failed = []
    for split in splits:
        path = processed_dir / f"{split}.parquet"
        if not path.exists():
            print(f"\n  [{split}] ✗ File not found: {path}")
            failed.append(split)
            continue

        df = pd.read_parquet(path)
        passed = run_checks(split, df)
        if not passed:
            failed.append(split)

    print("\n============================================")
    if failed:
        raise ValueError(
            f"Ingestion quality checks FAILED for: {', '.join(failed)}. "
            "Fix the data issues before proceeding."
        )
    else:
        print(" All ingestion checks passed.")
        print("============================================")


if __name__ == "__main__":
    main()

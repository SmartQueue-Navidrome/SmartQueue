"""
Data quality checks for feedback data using Great Expectations.

Runs before retrain to ensure feedback data is valid.
Raises ValueError if any check fails, preventing bad data from entering training.

Checks:
  - actual_is_engaged values are 0 or 1 only
  - session_id has no nulls
  - video_id has no nulls
  - rank_position has no nulls
  - predicted_engagement_prob values between 0.0 and 1.0
  - at least 1 row of feedback
"""

import great_expectations as gx
import pandas as pd


def run_checks(feedback_df: pd.DataFrame) -> None:
    """
    Validate feedback DataFrame with Great Expectations.
    Raises ValueError if any check fails.
    """
    context = gx.get_context(mode="ephemeral")
    ds = context.data_sources.add_pandas("feedback")
    asset = ds.add_dataframe_asset("feedback_df")
    batch_def = asset.add_batch_definition_whole_dataframe("batch")
    batch = batch_def.get_batch(batch_parameters={"dataframe": feedback_df})

    suite = context.suites.add(gx.ExpectationSuite(name="feedback_suite"))
    suite.add_expectation(
        gx.expectations.ExpectTableRowCountToBeBetween(min_value=1)
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToNotBeNull(column="session_id")
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToNotBeNull(column="video_id")
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToNotBeNull(column="rank_position")
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="actual_is_engaged", value_set=[0, 1]
        )
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeBetween(
            column="predicted_engagement_prob", min_value=0.0, max_value=1.0
        )
    )

    result = batch.validate(suite)

    print("  Feedback data quality checks:")
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
        elif check_type == "expect_column_values_to_not_be_null":
            label = f"{col}: no nulls"
        elif check_type == "expect_column_values_to_be_in_set":
            label = f"{col}: values in {value_set}"
        elif check_type == "expect_column_values_to_be_between":
            label = f"{col}: values between {min_v}–{max_v}"
        else:
            label = f"{check_type}({col})" if col else check_type

        print(f"    {status} {label}")
        if not r["success"]:
            all_passed = False

    if not all_passed:
        raise ValueError("Feedback data quality checks failed — retrain aborted.")

    print("  All checks passed.")

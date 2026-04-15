"""
Quick test to verify S3 connection to Chameleon object storage.
Lists all objects in the bucket to confirm credentials work and data exists.

Usage:
    python utils/test_connection.py
"""

from s3 import BUCKET, get_client

s3 = get_client()

PREFIXES = ["raw/", "processed/", "feedback/", "retrain/"]

def count_objects(prefix: str) -> int:
    total = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        total += len(page.get("Contents", []))
    return total

try:
    s3.head_bucket(Bucket=BUCKET)
    print(f"[ok] Connected to bucket '{BUCKET}'\n")
    for prefix in PREFIXES:
        count = count_objects(prefix)
        status = "ok" if count > 0 else "empty"
        print(f"  [{status}] {prefix:<12} {count} objects")
except Exception as e:
    print(f"[error] {e}")
    raise SystemExit(1)

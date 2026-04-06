"""
Pipeline 1 - Step 1: Ingestion
Copies local XITE parquet to output directory and writes metadata.json.
If local file is not found, downloads and extracts from official URL.

Usage:
    python ingest.py [--output-dir /tmp/smartqueue]
    python ingest.py --output-dir /tmp/smartqueue --source /path/to/xite_msd.parquet
"""

import os
import json
import argparse
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path
from datetime import datetime, timezone

import pyarrow.parquet as pq
from dotenv import load_dotenv

load_dotenv()

XITE_URL = "https://millionsessionsdataset.xite.com/xite_msd.zip"
XITE_FILENAME = "xite_msd.parquet"

# Default: look for local file relative to repo root (overridden by --source or SOURCE env var)
_p = Path(__file__).resolve().parents
DEFAULT_SOURCE = _p[min(3, len(_p) - 1)] / "XITE-Million-Sessions-Dataset" / "xite_msd.parquet"


def download_and_extract(dest_dir: Path) -> Path:
    zip_path = dest_dir / "xite_msd.zip"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Download
    print(f"[ingest] Downloading {XITE_URL} ...")
    start = time.perf_counter()

    def progress(count, block_size, total_size):
        downloaded = count * block_size
        elapsed = time.perf_counter() - start
        pct = downloaded / total_size * 100 if total_size > 0 else 0
        speed = downloaded / elapsed / 1024 / 1024 if elapsed > 0 else 0
        print(f"\r  {pct:.1f}%  {downloaded/1024/1024:.1f}/{total_size/1024/1024:.1f} MB  "
              f"{speed:.1f} MB/s", end="", flush=True)

    urllib.request.urlretrieve(XITE_URL, zip_path, reporthook=progress)
    print(f"\n[ingest] Download done in {time.perf_counter()-start:.1f}s")

    # Extract
    print(f"[ingest] Extracting {zip_path} ...")
    t = time.perf_counter()
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Find the parquet file inside the zip
        parquet_members = [m for m in zf.namelist() if m.endswith(".parquet")]
        if not parquet_members:
            raise FileNotFoundError("No .parquet file found inside zip")
        zf.extract(parquet_members[0], dest_dir)
        extracted = dest_dir / parquet_members[0]
    print(f"[ingest] Extracted in {time.perf_counter()-t:.1f}s")

    # Clean up zip
    zip_path.unlink()
    print(f"[ingest] Deleted {zip_path}")

    return extracted


def copy_parquet(source: Path, raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / "xite_msd.parquet"

    if dest.exists():
        print(f"[ingest] {dest} already exists, skipping copy")
        return dest

    print(f"[ingest] Copying {source} → {dest} ...")
    shutil.copy2(source, dest)
    print(f"[ingest] Copy complete ({dest.stat().st_size / 1024 / 1024:.1f} MB)")
    return dest


def write_metadata(raw_dir: Path, row_count: int):
    metadata = {
        "source_url": XITE_URL,
        "source_note": "Data ingested from local copy of XITE Million Sessions Dataset",
        "ingestion_timestamp": datetime.now(timezone.utc).isoformat(),
        "row_count": row_count,
        "parquet_file": "xite_msd.parquet",
    }
    meta_path = raw_dir / "metadata.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"[ingest] Metadata written: {meta_path}")
    return metadata


def main():
    parser = argparse.ArgumentParser()
    default_output = os.getenv("OUTPUT_DIR", "/app/data")
    parser.add_argument("--output-dir", default=default_output, help="Base output directory")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Path to local xite_msd.parquet")
    args = parser.parse_args()

    raw_dir = Path(args.output_dir) / "raw"
    total_start = time.perf_counter()

    source = Path(args.source)
    if source.exists():
        print(f"[ingest] Local file found: {source}")
        dest = copy_parquet(source, raw_dir)
    else:
        print(f"[ingest] Local file not found, downloading from {XITE_URL} ...")
        tmp_dir = Path(args.output_dir) / "tmp"
        extracted = download_and_extract(tmp_dir)
        dest = copy_parquet(extracted, raw_dir)
        # Clean up tmp dir
        shutil.rmtree(tmp_dir)
        print(f"[ingest] Cleaned up {tmp_dir}")

    # Count rows
    t = time.perf_counter()
    print("[ingest] Reading row count...")
    pf = pq.ParquetFile(dest)
    row_count = pf.metadata.num_rows
    print(f"[ingest] Row count: {row_count:,}  ({time.perf_counter()-t:.1f}s)")

    # Write metadata
    write_metadata(raw_dir, row_count)

    print(f"\n[ingest] Done. Total: {time.perf_counter()-total_start:.1f}s  Raw data at: {raw_dir}")
    return dest


if __name__ == "__main__":
    main()

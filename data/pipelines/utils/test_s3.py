"""
Unit tests for s3.list_objects pagination fix.

Validates that list_objects collects all pages instead of stopping at 1000 objects.
No real S3 connection needed — uses unittest.mock.
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Allow import without .env credentials present
import os
os.environ.setdefault("S3_ACCESS_KEY", "dummy")
os.environ.setdefault("S3_SECRET_KEY", "dummy")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import s3 as s3_module


def _make_pages(total: int, page_size: int = 1000) -> list[dict]:
    """Build a list of simulated S3 page dicts for `total` objects."""
    pages = []
    for start in range(0, total, page_size):
        batch = [{"Key": f"feedback/20260423/file_{i}.jsonl", "Size": 100}
                 for i in range(start, min(start + page_size, total))]
        pages.append({"Contents": batch})
    if not pages:
        pages.append({})  # empty prefix returns a page with no Contents key
    return pages


def _mock_paginator(pages: list[dict]):
    paginator = MagicMock()
    paginator.paginate.return_value = iter(pages)
    client = MagicMock()
    client.get_paginator.return_value = paginator
    return client, paginator


def test_single_page_under_1000():
    pages = _make_pages(500)
    client, paginator = _mock_paginator(pages)

    with patch.object(s3_module, "get_client", return_value=client):
        result = s3_module.list_objects(prefix="feedback/20260423/")

    assert len(result) == 500
    paginator.paginate.assert_called_once_with(
        Bucket=s3_module.BUCKET, Prefix="feedback/20260423/"
    )


def test_exactly_1000_objects():
    """Old code would silently truncate at 1000 — new code should return all 1000."""
    pages = _make_pages(1000)
    client, paginator = _mock_paginator(pages)

    with patch.object(s3_module, "get_client", return_value=client):
        result = s3_module.list_objects(prefix="feedback/20260423/")

    assert len(result) == 1000


def test_pagination_across_multiple_pages():
    """Core regression: 20,210 objects must all be returned, not just the first 1000."""
    pages = _make_pages(20_210)
    client, paginator = _mock_paginator(pages)

    with patch.object(s3_module, "get_client", return_value=client):
        result = s3_module.list_objects(prefix="feedback/20260423/")

    assert len(result) == 20_210
    # Paginator must have iterated all 21 pages (20 full + 1 partial)
    assert len(pages) == 21


def test_empty_prefix_returns_empty_list():
    pages = [{}]  # no Contents key
    client, paginator = _mock_paginator(pages)

    with patch.object(s3_module, "get_client", return_value=client):
        result = s3_module.list_objects(prefix="feedback/99991231/")

    assert result == []


def test_keys_are_preserved():
    """Returned dicts must retain the Key field used by retrain.py."""
    pages = _make_pages(5, page_size=5)
    client, _ = _mock_paginator(pages)

    with patch.object(s3_module, "get_client", return_value=client):
        result = s3_module.list_objects(prefix="feedback/20260423/")

    assert all("Key" in obj for obj in result)
    assert result[0]["Key"] == "feedback/20260423/file_0.jsonl"


if __name__ == "__main__":
    tests = [
        test_single_page_under_1000,
        test_exactly_1000_objects,
        test_pagination_across_multiple_pages,
        test_empty_prefix_returns_empty_list,
        test_keys_are_preserved,
    ]
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {t.__name__}: {e}")

#!/usr/bin/env python3
"""
Write ID3 tags to the local MP3 library from song_catalog_metadata.json.

This script is intended for the Navidrome music library, where files are
organized as:

  <music_root>/<folder>/<filename>

using metadata entries shaped like:

  {
    "track_name": "Olympus",
    "artist_name": "Grégoire Lourme",
    "release_year": 2013,
    "filename": "1063183.mp3",
    "folder": "Classical"
  }

Examples:
  python data/scripts/write_id3_tags.py --dry-run --limit 5
  python data/scripts/write_id3_tags.py
"""

import argparse
import json
from pathlib import Path

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, ID3NoHeaderError, TDRC


DEFAULT_METADATA = "data/song_catalog_metadata.json"
DEFAULT_MUSIC_ROOT = "/mnt/smartqueue-data/navidrome/music"


def load_metadata(metadata_path: Path) -> list[dict]:
    with metadata_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON array in {metadata_path}")
    return payload


def resolve_mp3_path(music_root: Path, entry: dict) -> Path:
    folder = str(entry.get("folder", "")).strip()
    filename = str(entry.get("filename", "")).strip()
    if not folder or not filename:
        raise ValueError("Missing required metadata fields: folder or filename")
    return music_root / folder / filename


def ensure_id3_header(mp3_path: Path) -> None:
    try:
        ID3(mp3_path)
    except ID3NoHeaderError:
        tags = ID3()
        tags.save(mp3_path)


def build_updates(entry: dict, set_album_from_folder: bool) -> tuple[dict, str | None]:
    updates = {
        "title": [str(entry.get("track_name", "")).strip()],
        "artist": [str(entry.get("artist_name", "")).strip()],
    }

    if set_album_from_folder:
        folder = str(entry.get("folder", "")).strip()
        if folder:
            updates["album"] = [folder]

    year = entry.get("release_year")
    year_value = None
    if year not in (None, ""):
        year_value = str(year).strip()

    return updates, year_value


def apply_updates(mp3_path: Path, updates: dict, year_value: str | None) -> tuple[bool, list[str]]:
    ensure_id3_header(mp3_path)
    audio = EasyID3(mp3_path)

    changed_fields = []
    for key, value in updates.items():
        if not value or not value[0]:
            continue
        current = audio.get(key, [])
        if current != value:
            audio[key] = value
            changed_fields.append(key)
    audio.save()

    if year_value:
        id3 = ID3(mp3_path)
        current_year = [str(frame.text[0]) for frame in id3.getall("TDRC")] if id3.getall("TDRC") else []
        if current_year != [year_value]:
            id3.delall("TDRC")
            id3.add(TDRC(encoding=3, text=[year_value]))
            id3.save(v2_version=3)
            changed_fields.append("date")

    return bool(changed_fields), changed_fields


def main() -> int:
    parser = argparse.ArgumentParser(description="Write ID3 tags to Navidrome MP3 files")
    parser.add_argument("--metadata", default=DEFAULT_METADATA, help="Path to metadata JSON array")
    parser.add_argument("--music-root", default=DEFAULT_MUSIC_ROOT, help="Music library root")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N entries")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing tags")
    parser.add_argument(
        "--skip-album",
        action="store_true",
        help="Do not set album from the metadata folder name",
    )
    args = parser.parse_args()

    metadata_path = Path(args.metadata)
    music_root = Path(args.music_root)

    entries = load_metadata(metadata_path)
    if args.limit > 0:
        entries = entries[: args.limit]

    processed = 0
    changed = 0
    missing = 0
    invalid = 0

    for entry in entries:
        try:
            mp3_path = resolve_mp3_path(music_root, entry)
        except ValueError as exc:
            print(f"[invalid] {exc}: {entry}")
            invalid += 1
            continue

        if not mp3_path.exists():
            print(f"[missing] {mp3_path}")
            missing += 1
            continue

        updates, year_value = build_updates(entry, set_album_from_folder=not args.skip_album)
        summary = ", ".join(
            filter(
                None,
                [
                    f"title={updates.get('title', [''])[0]!r}" if updates.get("title", [""])[0] else "",
                    f"artist={updates.get('artist', [''])[0]!r}" if updates.get("artist", [""])[0] else "",
                    f"album={updates.get('album', [''])[0]!r}" if updates.get("album", [""])[0] else "",
                    f"date={year_value!r}" if year_value else "",
                ],
            )
        )

        if args.dry_run:
            print(f"[dry-run] {mp3_path} <- {summary}")
            processed += 1
            continue

        was_changed, changed_fields = apply_updates(mp3_path, updates, year_value)
        status = "updated" if was_changed else "unchanged"
        suffix = f" ({', '.join(changed_fields)})" if changed_fields else ""
        print(f"[{status}] {mp3_path}{suffix}")
        processed += 1
        if was_changed:
            changed += 1

    print(
        f"\n[done] processed={processed} changed={changed} missing={missing} invalid={invalid} "
        f"dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

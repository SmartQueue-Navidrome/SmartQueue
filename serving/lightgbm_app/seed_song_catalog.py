#!/usr/bin/env python3
"""
Seed song_catalog table in Postgres from Navidrome song library.

Steps:
  1. Read metadata JSON (contains genre_encoded, subgenre_encoded, etc.)
  2. Fetch all songs from Navidrome API (contains navidrome_id, title, artist)
  3. Match by title + artist
  4. INSERT INTO song_catalog

Usage:
  python seed_song_catalog.py --metadata data/song_catalog_metadata.json

Environment variables:
  NAVIDROME_URL       Navidrome base URL (default: http://localhost:4533)
  NAVIDROME_USER      Admin username
  NAVIDROME_PASSWORD  Admin password
  POSTGRES_HOST       (default: localhost)
  POSTGRES_PORT       (default: 5432)
  POSTGRES_USER
  POSTGRES_PASSWORD
  POSTGRES_DB
"""

import argparse
import json
import os
import sys
import unicodedata

import psycopg2
import requests


def get_navidrome_token(base_url, username, password):
    resp = requests.post(
        f"{base_url}/auth/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def get_all_navidrome_songs(base_url, token):
    songs = []
    start = 0
    batch_size = 200
    while True:
        resp = requests.get(
            f"{base_url}/api/song",
            params={"_start": start, "_end": start + batch_size},
            headers={"X-ND-Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        songs.extend(batch)
        if len(batch) < batch_size:
            break
        start += batch_size
    return songs


def normalize(s):
    if not s:
        return ""
    normalized = unicodedata.normalize("NFKD", s)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_text.lower().strip().split())


def filename_stem(filename):
    return normalize(str(filename).removesuffix(".mp3"))


def track_artist_key(track_name, artist_name):
    return f"{normalize(track_name)}|||{normalize(artist_name)}"


def seed(metadata_path):
    base_url = os.environ.get("NAVIDROME_URL", "http://localhost:4533").rstrip("/")
    nd_user = os.environ.get("NAVIDROME_USER", "")
    nd_pass = os.environ.get("NAVIDROME_PASSWORD", "")

    pg_host = os.environ.get("POSTGRES_HOST", "localhost")
    pg_port = int(os.environ.get("POSTGRES_PORT", "5432"))
    pg_user = os.environ.get("POSTGRES_USER", "")
    pg_pass = os.environ.get("POSTGRES_PASSWORD", "")
    pg_db = os.environ.get("POSTGRES_DB", "")

    if not nd_user or not nd_pass:
        print("[error] NAVIDROME_USER and NAVIDROME_PASSWORD must be set", file=sys.stderr)
        sys.exit(1)

    # Load metadata
    with open(metadata_path) as f:
        metadata = json.load(f)
    print(f"[metadata] Loaded {len(metadata)} entries from {metadata_path}")

    # Build metadata lookups for both the new ID3-based world and the older
    # filename-based library state.
    meta_by_track_artist = {}
    meta_by_filename = {}
    for entry in metadata:
        title_artist = track_artist_key(entry.get("track_name", ""), entry.get("artist_name", ""))
        if title_artist != "|||":
            meta_by_track_artist[title_artist] = entry

        stem = filename_stem(entry.get("filename", ""))
        if stem:
            meta_by_filename[stem] = entry

    # Login to Navidrome
    print(f"[navidrome] Logging in as '{nd_user}' at {base_url} ...")
    try:
        token = get_navidrome_token(base_url, nd_user, nd_pass)
    except Exception as e:
        print(f"[error] Navidrome login failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Fetch all songs
    print("[navidrome] Fetching all songs...")
    try:
        nd_songs = get_all_navidrome_songs(base_url, token)
    except Exception as e:
        print(f"[error] Failed to fetch songs from Navidrome: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[navidrome] Found {len(nd_songs)} songs")

    # Connect to Postgres
    conn = psycopg2.connect(
        host=pg_host, port=pg_port,
        user=pg_user, password=pg_pass, dbname=pg_db,
    )

    matched = 0
    skipped = 0
    matched_by_title_artist = 0
    matched_by_filename = 0

    with conn.cursor() as cur:
        for song in nd_songs:
            match_source = None
            meta = meta_by_track_artist.get(
                track_artist_key(song.get("title", ""), song.get("artist", ""))
            )
            if meta:
                match_source = "title_artist"
            else:
                meta = meta_by_filename.get(normalize(song.get("title", "")))
                if meta:
                    match_source = "filename_fallback"

            if not meta:
                print(f"  [skip] No metadata match: '{song.get('title')}' / '{song.get('artist')}'")
                skipped += 1
                continue

            cur.execute(
                """
                INSERT INTO song_catalog
                    (navidrome_id, genre_encoded, subgenre_encoded, release_year, context_segment)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (navidrome_id) DO UPDATE SET
                    genre_encoded    = EXCLUDED.genre_encoded,
                    subgenre_encoded = EXCLUDED.subgenre_encoded,
                    release_year     = EXCLUDED.release_year,
                    context_segment  = EXCLUDED.context_segment
                """,
                (
                    song["id"],
                    meta["genre_encoded"],
                    meta.get("subgenre_encoded", 0),
                    meta.get("release_year", 2000),
                    meta.get("context_segment", 0),
                ),
            )
            matched += 1
            if match_source == "title_artist":
                matched_by_title_artist += 1
            elif match_source == "filename_fallback":
                matched_by_filename += 1

    conn.commit()
    conn.close()

    print(
        "\n[done] "
        f"Inserted/updated: {matched} | "
        f"Skipped (no match): {skipped} | "
        f"Matched by title+artist: {matched_by_title_artist} | "
        f"Matched by filename fallback: {matched_by_filename}"
    )
    if skipped > 0:
        print(
            "  → Skipped songs have no metadata entry.\n"
            "    Check whether Navidrome title/artist values differ from\n"
            "    track_name/artist_name in the metadata JSON, or whether the\n"
            "    filename fallback needs to cover an additional case."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed song_catalog from Navidrome library")
    parser.add_argument(
        "--metadata",
        default="data/song_catalog_metadata.json",
        help="Path to song metadata JSON array file",
    )
    args = parser.parse_args()
    seed(args.metadata)

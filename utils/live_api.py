"""Fetch live cricket data from RapidAPI Cricbuzz."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY", "")
API_HOST = os.getenv("API_HOST", "cricbuzz-cricket.p.rapidapi.com")
CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _headers() -> dict:
    return {
        "X-RapidAPI-Key": API_KEY,
        "X-RapidAPI-Host": API_HOST,
    }


def fetch_json(endpoint: str, cache_name: str | None = None, ttl_seconds: int = 120, force: bool = False) -> dict:
    """GET an API endpoint with short-lived file cache to protect quota."""
    cache_path = CACHE_DIR / cache_name if cache_name else None
    if cache_path and cache_path.exists() and not force:
        age = time.time() - cache_path.stat().st_mtime
        if age < ttl_seconds:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)

    if not API_KEY:
        raise RuntimeError("API_KEY missing in .env")

    url = f"https://{API_HOST}/{endpoint.lstrip('/')}"
    resp = requests.get(url, headers=_headers(), timeout=20)

    if resp.status_code == 429:
        # Prefer stale cache over failing hard when quota is hit
        if cache_path and cache_path.exists():
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        raise RuntimeError("RapidAPI quota exceeded (429). Try again later.")

    resp.raise_for_status()
    data = resp.json()

    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return data


def fetch_match_lists(force: bool = False) -> dict[str, dict]:
    out: dict[str, dict] = {}
    endpoints = {
        "live": ("matches/v1/live", "matches_live.json", 60),
        "recent": ("matches/v1/recent", "matches_recent.json", 300),
        "upcoming": ("matches/v1/upcoming", "matches_upcoming.json", 300),
    }
    for kind, (endpoint, cache_name, ttl) in endpoints.items():
        try:
            out[kind] = fetch_json(endpoint, cache_name, ttl_seconds=ttl, force=force)
        except Exception as exc:
            # Fall back to any cached payload so one 429 doesn't kill the whole sync
            cache_path = CACHE_DIR / cache_name
            if cache_path.exists():
                with open(cache_path, "r", encoding="utf-8") as f:
                    out[kind] = json.load(f)
            else:
                out[kind] = {}
            print(f"[live_api] {kind} failed ({exc}); using cache/empty")
    return out


def fetch_match_info(match_id: str | int, force: bool = False) -> dict:
    mid = str(match_id)
    return fetch_json(f"mcenter/v1/{mid}", f"match_{mid}_info.json", ttl_seconds=300, force=force)


def fetch_scorecard(match_id: str | int, force: bool = False) -> dict:
    mid = str(match_id)
    return fetch_json(f"mcenter/v1/{mid}/scard", f"match_{mid}_scorecard.json", ttl_seconds=120, force=force)


def iter_matches_from_list(payload: dict):
    """Yield matchInfo (+ matchScore when present) from live/recent/upcoming payloads."""
    for block in payload.get("typeMatches", []) or []:
        for series in block.get("seriesMatches", []) or []:
            wrapper = series.get("seriesAdWrapper") or {}
            series_name = wrapper.get("seriesName") or block.get("matchType") or "Unknown Series"
            for match in wrapper.get("matches", []) or []:
                info = match.get("matchInfo") or {}
                if not info.get("matchId"):
                    continue
                yield {
                    "series": series_name,
                    "matchInfo": info,
                    "matchScore": match.get("matchScore") or {},
                }

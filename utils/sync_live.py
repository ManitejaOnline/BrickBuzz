"""Sync RapidAPI live/recent/upcoming matches into cricket.db."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from utils.db_connection import DB_PATH
from utils.live_api import (
    fetch_match_info,
    fetch_match_lists,
    fetch_scorecard,
    iter_matches_from_list,
)


def _safe_int(val: Any, default: int | None = None) -> int | None:
    try:
        if val is None or val == "":
            return default
        return int(val)
    except (TypeError, ValueError):
        return default


def _safe_float(val: Any, default: float | None = None) -> float | None:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _epoch_to_date(ms: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _team_score_from_list(match_score: dict, team_key: str) -> tuple[int, int, float]:
    """Parse teamScore from live list payload into runs/wickets/overs."""
    block = (match_score or {}).get(team_key) or {}
    innings = block.get("inngs1") or block.get("inngs2") or {}
    if not innings and isinstance(block.get("inngs"), list) and block["inngs"]:
        innings = block["inngs"][0]
    runs = _safe_int(innings.get("runs"), 0) or 0
    wickets = _safe_int(innings.get("wickets"), 0) or 0
    overs = _safe_float(innings.get("overs"), 0.0) or 0.0
    return runs, wickets, overs


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS matches (
            match_id TEXT PRIMARY KEY,
            series TEXT,
            format TEXT,
            venue TEXT,
            date TEXT,
            status TEXT
        );
        CREATE TABLE IF NOT EXISTS players (
            player_id TEXT PRIMARY KEY,
            match_id TEXT,
            team_name TEXT,
            player_name TEXT,
            role TEXT,
            FOREIGN KEY(match_id) REFERENCES matches(match_id)
        );
        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            team_name TEXT,
            runs INTEGER,
            wickets INTEGER,
            overs REAL,
            FOREIGN KEY(match_id) REFERENCES matches(match_id)
        );
        CREATE TABLE IF NOT EXISTS player_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            player_id TEXT,
            runs INTEGER,
            wickets INTEGER,
            FOREIGN KEY(match_id) REFERENCES matches(match_id),
            FOREIGN KEY(player_id) REFERENCES players(player_id)
        );
        """
    )
    conn.commit()


def _upsert_match(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO matches (match_id, series, format, venue, date, status)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(match_id) DO UPDATE SET
            series=excluded.series,
            format=excluded.format,
            venue=excluded.venue,
            date=excluded.date,
            status=excluded.status
        """,
        (
            row["match_id"],
            row["series"],
            row["format"],
            row["venue"],
            row["date"],
            row["status"],
        ),
    )


def _replace_scores(conn: sqlite3.Connection, match_id: str, scores: list[tuple]) -> None:
    conn.execute("DELETE FROM scores WHERE match_id = ?", (match_id,))
    conn.executemany(
        "INSERT INTO scores (match_id, team_name, runs, wickets, overs) VALUES (?, ?, ?, ?, ?)",
        scores,
    )


def _upsert_player(conn: sqlite3.Connection, player: tuple) -> None:
    # player_id is global primary key in this schema; use match-scoped id to avoid collisions
    conn.execute(
        """
        INSERT INTO players (player_id, match_id, team_name, player_name, role)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
            match_id=excluded.match_id,
            team_name=excluded.team_name,
            player_name=excluded.player_name,
            role=excluded.role
        """,
        player,
    )


def _replace_player_stats(conn: sqlite3.Connection, match_id: str, stats: list[tuple]) -> None:
    conn.execute("DELETE FROM player_stats WHERE match_id = ?", (match_id,))
    conn.executemany(
        "INSERT INTO player_stats (match_id, player_id, runs, wickets) VALUES (?, ?, ?, ?)",
        stats,
    )


def _enrich_from_details(conn: sqlite3.Connection, match_id: str, team1: str, team2: str) -> None:
    """Pull scorecard + squad details when quota allows."""
    try:
        info = fetch_match_info(match_id)
        scard = fetch_scorecard(match_id)
    except Exception:
        return

    mi = info.get("matchInfo") or {}
    players = []
    for team_key, team_name in (("team1", team1), ("team2", team2)):
        team = mi.get(team_key) or {}
        name = team.get("name") or team_name
        for p in team.get("playerDetails", []) or []:
            pid = p.get("id")
            if not pid:
                continue
            players.append(
                (
                    f"{match_id}_{pid}",
                    match_id,
                    name,
                    p.get("name") or p.get("fullName") or str(pid),
                    p.get("role") or "Player",
                )
            )
    for player in players:
        _upsert_player(conn, player)

    # Aggregate batting/bowling into player_stats
    bat_map: dict[str, int] = {}
    bowl_map: dict[str, int] = {}
    team_totals: dict[str, dict] = {}

    for inng in scard.get("scorecard", []) or []:
        bat_team = inng.get("batteamname") or inng.get("batTeamName") or ""
        runs = _safe_int(inng.get("score"), 0) or 0
        wickets = _safe_int(inng.get("wickets"), 0) or 0
        overs = _safe_float(inng.get("overs"), 0.0) or 0.0
        if bat_team:
            prev = team_totals.get(bat_team, {"runs": 0, "wickets": 0, "overs": 0.0})
            team_totals[bat_team] = {
                "runs": prev["runs"] + runs,
                "wickets": max(prev["wickets"], wickets),
                "overs": prev["overs"] + overs,
            }

        for b in inng.get("batsman", []) or []:
            pid = b.get("id")
            if not pid:
                continue
            key = f"{match_id}_{pid}"
            bat_map[key] = bat_map.get(key, 0) + (_safe_int(b.get("runs"), 0) or 0)
            if key not in {p[0] for p in players}:
                _upsert_player(
                    conn,
                    (
                        key,
                        match_id,
                        bat_team or "Unknown",
                        b.get("name") or str(pid),
                        "Batsman",
                    ),
                )

        for bowl in inng.get("bowler", []) or []:
            pid = bowl.get("id")
            if not pid:
                continue
            key = f"{match_id}_{pid}"
            bowl_map[key] = bowl_map.get(key, 0) + (_safe_int(bowl.get("wickets"), 0) or 0)
            if key not in {p[0] for p in players}:
                _upsert_player(
                    conn,
                    (
                        key,
                        match_id,
                        "Unknown",
                        bowl.get("name") or str(pid),
                        "Bowler",
                    ),
                )

    if team_totals:
        score_rows = [
            (match_id, team, vals["runs"], vals["wickets"], vals["overs"])
            for team, vals in team_totals.items()
        ]
        _replace_scores(conn, match_id, score_rows)

    all_ids = set(bat_map) | set(bowl_map)
    if all_ids:
        stats = [(match_id, pid, bat_map.get(pid, 0), bowl_map.get(pid, 0)) for pid in all_ids]
        _replace_player_stats(conn, match_id, stats)


def sync_live_data(force: bool = False, detail_limit: int = 8) -> dict:
    """
    Pull live/upcoming/recent match lists into SQLite.
    Fetches detailed scorecards for up to `detail_limit` matches (live first).
    """
    lists = fetch_match_lists(force=force)
    collected = []
    seen = set()

    for kind in ("live", "recent", "upcoming"):
        for item in iter_matches_from_list(lists[kind]):
            mid = str(item["matchInfo"]["matchId"])
            if mid in seen:
                continue
            seen.add(mid)
            collected.append((kind, item))

    conn = sqlite3.connect(DB_PATH)
    _ensure_schema(conn)

    # Keep sample rows unless we successfully sync at least one live API match
    synced = 0
    detailed = 0
    errors = []

    for kind, item in collected:
        info = item["matchInfo"]
        mid = str(info["matchId"])
        venue_info = info.get("venueInfo") or {}
        venue = ", ".join(
            [p for p in [venue_info.get("ground"), venue_info.get("city")] if p]
        ) or venue_info.get("ground") or "TBD"
        team1 = (info.get("team1") or {}).get("teamName") or (info.get("team1") or {}).get("teamSName") or "Team 1"
        team2 = (info.get("team2") or {}).get("teamName") or (info.get("team2") or {}).get("teamSName") or "Team 2"
        status = info.get("status") or info.get("state") or kind.title()
        if kind == "live" and info.get("state") == "In Progress":
            status = f"LIVE — {status}"

        _upsert_match(
            conn,
            {
                "match_id": mid,
                "series": item["series"],
                "format": info.get("matchFormat") or info.get("matchType") or "",
                "venue": venue,
                "date": _epoch_to_date(info.get("startDate")),
                "status": status,
            },
        )

        # Baseline team score from list payload
        r1, w1, o1 = _team_score_from_list(item["matchScore"], "team1Score")
        r2, w2, o2 = _team_score_from_list(item["matchScore"], "team2Score")
        if r1 or r2 or kind == "live":
            _replace_scores(
                conn,
                mid,
                [
                    (mid, team1, r1, w1, o1),
                    (mid, team2, r2, w2, o2),
                ],
            )

        # Placeholder players from team names so Players page isn't empty
        _upsert_player(conn, (f"{mid}_t1", mid, team1, f"{team1} squad", "Team"))
        _upsert_player(conn, (f"{mid}_t2", mid, team2, f"{team2} squad", "Team"))

        synced += 1

        if detailed < detail_limit and kind in ("live", "recent"):
            try:
                _enrich_from_details(conn, mid, team1, team2)
                detailed += 1
            except Exception as exc:
                errors.append(f"{mid}: {exc}")

    conn.commit()
    conn.close()

    return {
        "synced_matches": synced,
        "detailed_matches": detailed,
        "live_count": sum(1 for k, _ in collected if k == "live"),
        "upcoming_count": sum(1 for k, _ in collected if k == "upcoming"),
        "recent_count": sum(1 for k, _ in collected if k == "recent"),
        "errors": errors,
    }


if __name__ == "__main__":
    # Keep detail fetches small to avoid RapidAPI free-tier 429s
    result = sync_live_data(force=False, detail_limit=3)
    print(result)

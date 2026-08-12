from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.8, "low": 0.5}
SUPPORTED_MODES = {"near", "bridge", "adventure"}


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS artists (
    artist_id INTEGER PRIMARY KEY,
    mbid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    sort_name TEXT,
    artist_type TEXT,
    area_code TEXT,
    begin_year INTEGER,
    end_year INTEGER,
    data_version TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS artists_by_name ON artists (name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS artist_aliases (
    artist_id INTEGER NOT NULL REFERENCES artists(artist_id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    locale TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (artist_id, alias, locale)
);

CREATE TABLE IF NOT EXISTS model_versions (
    model_version TEXT PRIMARY KEY,
    window_days INTEGER NOT NULL CHECK (window_days > 0),
    generated_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1))
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_model
ON model_versions (is_active) WHERE is_active = 1;

CREATE TABLE IF NOT EXISTS artist_edges (
    source_artist_id INTEGER NOT NULL REFERENCES artists(artist_id) ON DELETE CASCADE,
    target_artist_id INTEGER NOT NULL REFERENCES artists(artist_id) ON DELETE CASCADE,
    rank INTEGER NOT NULL CHECK (rank > 0),
    total_score REAL NOT NULL,
    behavior_score REAL NOT NULL,
    metadata_score REAL NOT NULL DEFAULT 0,
    relation_score REAL NOT NULL DEFAULT 0,
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    common_listener_count INTEGER NOT NULL DEFAULT 0,
    recommendation_source TEXT NOT NULL,
    window_days INTEGER NOT NULL CHECK (window_days > 0),
    model_version TEXT NOT NULL REFERENCES model_versions(model_version) ON DELETE CASCADE,
    generated_at TEXT NOT NULL,
    PRIMARY KEY (source_artist_id, target_artist_id, model_version),
    UNIQUE (source_artist_id, rank, model_version)
);

CREATE INDEX IF NOT EXISTS artist_edges_by_source
ON artist_edges (source_artist_id, model_version, rank);

CREATE INDEX IF NOT EXISTS artist_edges_by_target
ON artist_edges (target_artist_id, model_version);

CREATE TABLE IF NOT EXISTS edge_evidence (
    source_artist_id INTEGER NOT NULL,
    target_artist_id INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    evidence_value TEXT NOT NULL,
    evidence_score REAL NOT NULL DEFAULT 0,
    source_name TEXT NOT NULL,
    PRIMARY KEY (
        source_artist_id, target_artist_id, model_version, evidence_type, source_name
    ),
    FOREIGN KEY (source_artist_id, target_artist_id, model_version)
        REFERENCES artist_edges (source_artist_id, target_artist_id, model_version)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS edge_evidence_by_edge
ON edge_evidence (source_artist_id, target_artist_id, model_version);

CREATE TABLE IF NOT EXISTS source_registry (
    source_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    model_version TEXT NOT NULL REFERENCES model_versions(model_version) ON DELETE CASCADE,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (source_path, model_version)
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database: Path) -> None:
    with connect(database) as connection:
        connection.executescript(SCHEMA)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upsert_artist(
    connection: sqlite3.Connection,
    mbid: str,
    name: str,
    *,
    artist_type: str = "",
    area_code: str = "",
    data_version: str = "",
    updated_at: str,
) -> int:
    normalized_mbid = mbid.strip().lower()
    normalized_name = name.strip()
    if not normalized_mbid or not normalized_name:
        raise ValueError("Artist MBID and name are required")
    connection.execute(
        """
        INSERT INTO artists (
            mbid, name, sort_name, artist_type, area_code, data_version, updated_at
        ) VALUES (?, ?, ?, NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), ?)
        ON CONFLICT (mbid) DO UPDATE SET
            name = CASE WHEN excluded.name <> '' THEN excluded.name ELSE artists.name END,
            artist_type = COALESCE(excluded.artist_type, artists.artist_type),
            area_code = COALESCE(excluded.area_code, artists.area_code),
            data_version = COALESCE(excluded.data_version, artists.data_version),
            updated_at = excluded.updated_at
        """,
        (
            normalized_mbid,
            normalized_name,
            normalized_name,
            artist_type.strip(),
            area_code.strip(),
            data_version.strip(),
            updated_at,
        ),
    )
    row = connection.execute(
        "SELECT artist_id FROM artists WHERE mbid = ?", (normalized_mbid,)
    ).fetchone()
    assert row is not None
    return int(row["artist_id"])


def _coverage_artists(path: Path) -> list[dict[str, str]]:
    result = []
    for row in read_csv(path):
        mbid = (row.get("resolved_mbid") or row.get("manual_mbid") or "").strip().lower()
        name = (row.get("resolved_name") or row.get("artist_name") or "").strip()
        if not mbid or not name:
            continue
        result.append(
            {
                "mbid": mbid,
                "name": name,
                "artist_type": (row.get("resolved_type") or row.get("expected_type") or "").strip(),
                "area_code": (row.get("resolved_country") or row.get("expected_country") or "").strip(),
            }
        )
    return result


def _group_similarity_rows(paths: Iterable[Path]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for path in paths:
        current: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in read_csv(path):
            seed_mbid = row.get("seed_artist_mbid", "").strip().lower()
            candidate_mbid = row.get("candidate_artist_mbid", "").strip().lower()
            if not seed_mbid or not candidate_mbid:
                raise ValueError(f"Missing MBID in {path}")
            current[seed_mbid].append(row)
        # If several reports contain the same seed, the last complete report wins.
        grouped.update(current)
    return grouped


def build_database(
    database: Path,
    coverage_csv: Path,
    similarity_csvs: list[Path],
    *,
    model_version: str,
    window_days: int,
    recommendation_source: str = "behavior",
    activate: bool = True,
) -> dict[str, Any]:
    if not model_version.strip():
        raise ValueError("model_version is required")
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    initialize_database(database)
    imported_at = utc_now()
    grouped = _group_similarity_rows(similarity_csvs)

    with connect(database) as connection:
        if activate:
            connection.execute("UPDATE model_versions SET is_active = 0")
        connection.execute(
            """
            INSERT INTO model_versions (model_version, window_days, generated_at, is_active)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (model_version) DO UPDATE SET
                window_days = excluded.window_days,
                generated_at = excluded.generated_at,
                is_active = excluded.is_active
            """,
            (model_version, window_days, imported_at, int(activate)),
        )

        for artist in _coverage_artists(coverage_csv):
            upsert_artist(
                connection,
                artist["mbid"],
                artist["name"],
                artist_type=artist["artist_type"],
                area_code=artist["area_code"],
                data_version=model_version,
                updated_at=imported_at,
            )

        edge_count = 0
        for seed_mbid, rows in grouped.items():
            first = rows[0]
            source_id = upsert_artist(
                connection,
                seed_mbid,
                first["seed_artist_name"],
                data_version=model_version,
                updated_at=imported_at,
            )
            connection.execute(
                "DELETE FROM artist_edges WHERE source_artist_id = ? AND model_version = ?",
                (source_id, model_version),
            )
            for row in sorted(rows, key=lambda item: int(item["rank"])):
                target_id = upsert_artist(
                    connection,
                    row["candidate_artist_mbid"],
                    row["candidate_artist_name"],
                    data_version=model_version,
                    updated_at=imported_at,
                )
                source = row.get("recommendation_source") or recommendation_source
                row_window = int(row.get("window_days") or window_days)
                connection.execute(
                    """
                    INSERT INTO artist_edges (
                        source_artist_id, target_artist_id, rank, total_score,
                        behavior_score, metadata_score, relation_score, confidence,
                        common_listener_count, recommendation_source, window_days,
                        model_version, generated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        target_id,
                        int(row["rank"]),
                        float(row["similarity_score"]),
                        float(row.get("cosine_similarity") or row["similarity_score"]),
                        float(row.get("metadata_score") or 0),
                        float(row.get("relation_score") or 0),
                        row.get("confidence") or "low",
                        int(row.get("common_listener_count") or 0),
                        source,
                        row_window,
                        model_version,
                        imported_at,
                    ),
                )
                common_listeners = int(row.get("common_listener_count") or 0)
                if common_listeners > 0:
                    connection.execute(
                        """
                        INSERT INTO edge_evidence (
                            source_artist_id, target_artist_id, model_version,
                            evidence_type, evidence_value, evidence_score, source_name
                        ) VALUES (?, ?, ?, 'common_listener_count', ?, ?, 'ListenBrainz')
                        ON CONFLICT DO UPDATE SET
                            evidence_value = excluded.evidence_value,
                            evidence_score = excluded.evidence_score
                        """,
                        (
                            source_id,
                            target_id,
                            model_version,
                            str(common_listeners),
                            float(row.get("cosine_similarity") or row["similarity_score"]),
                        ),
                    )
                metadata_evidence = (row.get("metadata_evidence") or "").strip()
                if metadata_evidence and metadata_evidence != "{}":
                    # Validate before storing so the API never receives malformed evidence.
                    json.loads(metadata_evidence)
                    connection.execute(
                        """
                        INSERT INTO edge_evidence (
                            source_artist_id, target_artist_id, model_version,
                            evidence_type, evidence_value, evidence_score, source_name
                        ) VALUES (?, ?, ?, 'metadata', ?, ?, 'metadata_fallback')
                        ON CONFLICT DO UPDATE SET
                            evidence_value = excluded.evidence_value,
                            evidence_score = excluded.evidence_score
                        """,
                        (
                            source_id,
                            target_id,
                            model_version,
                            metadata_evidence,
                            float(row.get("metadata_score") or 0),
                        ),
                    )
                edge_count += 1

        for source_path in [coverage_csv, *similarity_csvs]:
            connection.execute(
                """
                INSERT INTO source_registry (
                    source_name, source_path, checksum_sha256, model_version, imported_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (source_path, model_version) DO UPDATE SET
                    checksum_sha256 = excluded.checksum_sha256,
                    imported_at = excluded.imported_at
                """,
                (
                    source_path.name,
                    str(source_path),
                    file_checksum(source_path),
                    model_version,
                    imported_at,
                ),
            )

        artist_count = int(connection.execute("SELECT COUNT(*) FROM artists").fetchone()[0])
        stored_edge_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM artist_edges WHERE model_version = ?", (model_version,)
            ).fetchone()[0]
        )
        stored_evidence_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM edge_evidence WHERE model_version = ?", (model_version,)
            ).fetchone()[0]
        )
    return {
        "database": str(database),
        "model_version": model_version,
        "window_days": window_days,
        "active": activate,
        "artist_count": artist_count,
        "source_artist_count": len(grouped),
        "imported_edge_count": edge_count,
        "stored_edge_count": stored_edge_count,
        "stored_evidence_count": stored_evidence_count,
    }


def active_model(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT model_version FROM model_versions WHERE is_active = 1"
    ).fetchone()
    if row is None:
        raise ValueError("No active model version")
    return str(row["model_version"])


def activate_model(database: Path, model_version: str) -> dict[str, Any]:
    initialize_database(database)
    with connect(database) as connection:
        model = connection.execute(
            "SELECT model_version FROM model_versions WHERE model_version = ?",
            (model_version,),
        ).fetchone()
        if model is None:
            raise ValueError(f"Unknown model version: {model_version}")
        edge_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM artist_edges WHERE model_version = ?",
                (model_version,),
            ).fetchone()[0]
        )
        if edge_count == 0:
            raise ValueError(f"Model has no recommendation edges: {model_version}")
        connection.execute("UPDATE model_versions SET is_active = 0")
        connection.execute(
            "UPDATE model_versions SET is_active = 1 WHERE model_version = ?",
            (model_version,),
        )
    return {
        "database": str(database),
        "active_model": model_version,
        "active_model_edge_count": edge_count,
    }


def search_artists(database: Path, query: str, limit: int = 10) -> list[dict[str, Any]]:
    pattern = f"%{query.strip()}%"
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT artist_id, mbid, name, artist_type, area_code
            FROM artists
            WHERE name LIKE ? COLLATE NOCASE
            ORDER BY CASE WHEN name = ? COLLATE NOCASE THEN 0 ELSE 1 END, name COLLATE NOCASE
            LIMIT ?
            """,
            (pattern, query.strip(), limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_neighbors(
    database: Path,
    source_mbid: str,
    *,
    limit: int = 10,
    model_version: str | None = None,
) -> list[dict[str, Any]]:
    with connect(database) as connection:
        selected_model = model_version or active_model(connection)
        rows = connection.execute(
            """
            SELECT
                target.mbid AS artist_mbid,
                target.name AS artist_name,
                edge.rank,
                edge.total_score,
                edge.behavior_score,
                edge.metadata_score,
                edge.relation_score,
                edge.confidence,
                edge.common_listener_count,
                edge.recommendation_source,
                edge.window_days,
                edge.model_version
            FROM artist_edges AS edge
            JOIN artists AS source ON source.artist_id = edge.source_artist_id
            JOIN artists AS target ON target.artist_id = edge.target_artist_id
            WHERE source.mbid = ? AND edge.model_version = ?
            ORDER BY edge.rank
            LIMIT ?
            """,
            (source_mbid.strip().lower(), selected_model, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def recommend(
    database: Path,
    seed_mbids: list[str],
    *,
    mode: str = "near",
    limit: int = 10,
    model_version: str | None = None,
) -> dict[str, Any]:
    normalized_mode = "near" if mode == "close" else mode
    if normalized_mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported mode: {mode}")
    seeds = list(dict.fromkeys(mbid.strip().lower() for mbid in seed_mbids if mbid.strip()))
    if not seeds:
        raise ValueError("At least one seed MBID is required")

    with connect(database) as connection:
        selected_model = model_version or active_model(connection)
        placeholders = ",".join("?" for _ in seeds)
        seed_rows = connection.execute(
            f"SELECT artist_id, mbid, name FROM artists WHERE mbid IN ({placeholders})",
            tuple(seeds),
        ).fetchall()
        seed_by_id = {int(row["artist_id"]): dict(row) for row in seed_rows}
        found_mbids = {str(row["mbid"]) for row in seed_rows}
        missing_mbids = [mbid for mbid in seeds if mbid not in found_mbids]
        if not seed_by_id:
            return {
                "mode": normalized_mode,
                "model_version": selected_model,
                "seed_count": len(seeds),
                "missing_seed_mbids": missing_mbids,
                "recommendations": [],
            }

        seed_ids = list(seed_by_id)
        id_placeholders = ",".join("?" for _ in seed_ids)
        rows = connection.execute(
            f"""
            SELECT
                edge.source_artist_id,
                target.artist_id AS target_artist_id,
                target.mbid AS target_mbid,
                target.name AS target_name,
                edge.total_score,
                edge.confidence,
                edge.common_listener_count,
                edge.recommendation_source,
                edge.window_days
            FROM artist_edges AS edge
            JOIN artists AS target ON target.artist_id = edge.target_artist_id
            WHERE edge.source_artist_id IN ({id_placeholders})
              AND edge.model_version = ?
            """,
            (*seed_ids, selected_model),
        ).fetchall()

    seed_id_set = set(seed_ids)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        row = dict(raw)
        if int(row["target_artist_id"]) in seed_id_set:
            continue
        grouped[int(row["target_artist_id"])].append(row)

    recommendations = []
    denominator = len(seeds)
    confidence_order = {"low": 0, "medium": 1, "high": 2}
    for candidate_rows in grouped.values():
        weighted = [
            float(row["total_score"]) * CONFIDENCE_WEIGHT[str(row["confidence"])]
            for row in candidate_rows
        ]
        matched = len({int(row["source_artist_id"]) for row in candidate_rows})
        coverage = matched / denominator
        maximum = max(weighted)
        average_all_seeds = sum(weighted) / denominator
        if normalized_mode == "near":
            final_score = maximum + (1 - maximum) * 0.08 * max(matched - 1, 0)
        elif normalized_mode == "bridge":
            final_score = average_all_seeds + 0.20 * coverage
        else:
            novelty = 1 - min(max(float(row["total_score"]) for row in candidate_rows), 1)
            final_score = 0.45 * maximum + 0.35 * coverage + 0.20 * novelty

        best = max(candidate_rows, key=lambda row: float(row["total_score"]))
        seed_scores = [
            {
                "seed_artist_mbid": seed_by_id[int(row["source_artist_id"])]["mbid"],
                "seed_artist_name": seed_by_id[int(row["source_artist_id"])]["name"],
                "similarity_score": float(row["total_score"]),
            }
            for row in sorted(
                candidate_rows,
                key=lambda item: float(item["total_score"]),
                reverse=True,
            )
        ]
        confidence = max(
            (str(row["confidence"]) for row in candidate_rows),
            key=lambda value: confidence_order[value],
        )
        recommendations.append(
            {
                "artist_mbid": best["target_mbid"],
                "artist_name": best["target_name"],
                "score": round(final_score, 6),
                "confidence": confidence,
                "matched_seed_count": matched,
                "seed_scores": seed_scores,
                "recommendation_source": best["recommendation_source"],
                "window_days": best["window_days"],
                "reason": f"入力{matched}組との類似関係を確認",
            }
        )

    recommendations.sort(
        key=lambda row: (row["score"], row["matched_seed_count"], row["artist_mbid"]),
        reverse=True,
    )
    return {
        "mode": normalized_mode,
        "model_version": selected_model,
        "seed_count": len(seeds),
        "missing_seed_mbids": missing_mbids,
        "recommendations": recommendations[:limit],
    }


def database_stats(database: Path) -> dict[str, Any]:
    with connect(database) as connection:
        model = active_model(connection)
        return {
            "database": str(database),
            "active_model": model,
            "artist_count": int(connection.execute("SELECT COUNT(*) FROM artists").fetchone()[0]),
            "edge_count": int(connection.execute("SELECT COUNT(*) FROM artist_edges").fetchone()[0]),
            "evidence_count": int(
                connection.execute("SELECT COUNT(*) FROM edge_evidence").fetchone()[0]
            ),
            "active_model_edge_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM artist_edges WHERE model_version = ?", (model,)
                ).fetchone()[0]
            ),
            "source_artist_count": int(
                connection.execute(
                    "SELECT COUNT(DISTINCT source_artist_id) FROM artist_edges WHERE model_version = ?",
                    (model,),
                ).fetchone()[0]
            ),
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and query the artist recommendation DB")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--database", type=Path, required=True)
    build.add_argument("--coverage", type=Path, required=True)
    build.add_argument("--similarity", type=Path, nargs="+", required=True)
    build.add_argument("--model-version", required=True)
    build.add_argument("--window-days", type=int, required=True)
    build.add_argument("--recommendation-source", default="behavior")
    build.add_argument("--inactive", action="store_true")

    search = subparsers.add_parser("search")
    search.add_argument("--database", type=Path, required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=10)

    neighbors = subparsers.add_parser("neighbors")
    neighbors.add_argument("--database", type=Path, required=True)
    neighbors.add_argument("--mbid", required=True)
    neighbors.add_argument("--limit", type=int, default=10)

    recommendations = subparsers.add_parser("recommend")
    recommendations.add_argument("--database", type=Path, required=True)
    recommendations.add_argument("--seed-mbid", action="append", required=True)
    recommendations.add_argument("--mode", choices=sorted(SUPPORTED_MODES), default="near")
    recommendations.add_argument("--limit", type=int, default=10)

    stats = subparsers.add_parser("stats")
    stats.add_argument("--database", type=Path, required=True)

    activate = subparsers.add_parser("activate")
    activate.add_argument("--database", type=Path, required=True)
    activate.add_argument("--model-version", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "build":
        result = build_database(
            args.database,
            args.coverage,
            args.similarity,
            model_version=args.model_version,
            window_days=args.window_days,
            recommendation_source=args.recommendation_source,
            activate=not args.inactive,
        )
    elif args.command == "search":
        result = search_artists(args.database, args.query, args.limit)
    elif args.command == "neighbors":
        result = get_neighbors(args.database, args.mbid, limit=args.limit)
    elif args.command == "recommend":
        result = recommend(
            args.database,
            args.seed_mbid,
            mode=args.mode,
            limit=args.limit,
        )
    elif args.command == "activate":
        result = activate_model(args.database, args.model_version)
    else:
        result = database_stats(args.database)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

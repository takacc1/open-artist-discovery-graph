from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import psycopg


TABLES = (
    "artists",
    "artist_aliases",
    "model_versions",
    "artist_edges",
    "edge_evidence",
    "source_registry",
)

COLUMNS = {
    "artists": (
        "artist_id", "mbid", "name", "sort_name", "artist_type", "area_code",
        "begin_year", "end_year", "data_version", "updated_at",
    ),
    "artist_aliases": ("artist_id", "alias", "locale"),
    "model_versions": ("model_version", "window_days", "generated_at", "is_active"),
    "artist_edges": (
        "source_artist_id", "target_artist_id", "rank", "total_score",
        "behavior_score", "metadata_score", "relation_score", "confidence",
        "common_listener_count", "recommendation_source", "window_days",
        "model_version", "generated_at",
    ),
    "edge_evidence": (
        "source_artist_id", "target_artist_id", "model_version", "evidence_type",
        "evidence_value", "evidence_score", "source_name",
    ),
    "source_registry": (
        "source_name", "source_path", "checksum_sha256", "model_version", "imported_at",
    ),
}


def rows(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> Iterable[tuple[Any, ...]]:
    selected = ", ".join(columns)
    for row in connection.execute(f"SELECT {selected} FROM {table}"):
        values = list(row)
        if table == "model_versions":
            values[3] = bool(values[3])
        yield tuple(values)


def migrate(sqlite_path: Path, database_url: str) -> dict[str, int]:
    if not sqlite_path.is_file():
        raise FileNotFoundError(sqlite_path)
    sqlite_connection = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    counts: dict[str, int] = {}
    try:
        with psycopg.connect(database_url) as postgres:
            with postgres.cursor() as cursor:
                cursor.execute(
                    "TRUNCATE source_registry, edge_evidence, artist_edges, "
                    "artist_aliases, model_versions, artists CASCADE"
                )
                for table in TABLES:
                    columns = COLUMNS[table]
                    column_list = ", ".join(columns)
                    with cursor.copy(
                        f"COPY {table} ({column_list}) FROM STDIN"
                    ) as copy:
                        count = 0
                        for row in rows(sqlite_connection, table, columns):
                            copy.write_row(row)
                            count += 1
                    counts[table] = count
    finally:
        sqlite_connection.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy the local recommendation SQLite database to PostgreSQL"
    )
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=Path("reports/serving/artist_discovery.sqlite3"),
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    counts = migrate(args.sqlite, args.database_url)
    for table, count in counts.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()

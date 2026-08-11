from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

try:
    import pyarrow.dataset as arrow_dataset
except ModuleNotFoundError:
    arrow_dataset = None  # type: ignore[assignment]

from src.feasibility import normalize_name, write_csv


DEFAULT_COVERAGE = Path("reports/artist_coverage.csv")
DEFAULT_REPORT_DIR = Path("reports")
DEFAULT_WORK_DB = Path("/private/tmp/listenbrainz_artist_similarity.sqlite3")


@dataclass(frozen=True)
class SimilarityConfig:
    listen_cap: int = 100
    shrinkage: float = 10.0
    min_common_listeners: int = 2
    limit: int = 10


def artist_records(listen: dict[str, Any]) -> list[tuple[str, str]]:
    """Return MBID/name pairs without reading or returning a ListenBrainz username."""
    metadata = listen.get("track_metadata") or {}
    additional = metadata.get("additional_info") or {}
    mbids = additional.get("artist_mbids") or []
    names = additional.get("artist_names") or []
    if isinstance(mbids, str):
        mbids = [mbids]
    if isinstance(names, str):
        names = [names]
    if not isinstance(mbids, list):
        return []

    fallback_name = str(metadata.get("artist_name") or "")
    result: list[tuple[str, str]] = []
    for index, raw_mbid in enumerate(mbids):
        mbid = str(raw_mbid or "").strip().lower()
        if not mbid:
            continue
        name = str(names[index]) if isinstance(names, list) and index < len(names) else fallback_name
        result.append((mbid, name.strip()))
    return result


def artist_credit_records(artist_name: str, mbids: Any) -> list[tuple[str, str]]:
    """Normalize the MusicBrainz artist-credit identifiers from a Spark row."""
    if not isinstance(mbids, list):
        return []
    normalized_mbids: list[str] = []
    seen: set[str] = set()
    for raw_mbid in mbids:
        mbid = str(raw_mbid or "").strip().lower()
        if not mbid or mbid in seen:
            continue
        seen.add(mbid)
        normalized_mbids.append(mbid)
    # A combined credit such as "K/DA with ..." does not identify the
    # individual names belonging to each MBID. Leave those names empty until
    # the same MBID appears in an unambiguous single-artist credit.
    name = str(artist_name or "").strip() if len(normalized_mbids) == 1 else ""
    return [(mbid, name) for mbid in normalized_mbids]


def pseudonymize_user_id(user_id: int, key: bytes) -> bytes:
    """Create a per-run, non-reversible grouping key for a ListenBrainz user ID."""
    return hashlib.blake2b(str(user_id).encode("ascii"), key=key, digest_size=16).digest()


def find_listens_member(archive: Path) -> str:
    completed = subprocess.run(
        ["tar", "--use-compress-program=unzstd", "-tf", str(archive)],
        check=True,
        capture_output=True,
        text=True,
    )
    members = [line for line in completed.stdout.splitlines() if line.endswith(".listens")]
    if len(members) != 1:
        raise ValueError(f"Expected exactly one .listens member in {archive}, found {len(members)}")
    return members[0]


def iter_dump_listens(archive: Path, member: str | None = None) -> Iterator[dict[str, Any]]:
    selected_member = member or find_listens_member(archive)
    process = subprocess.Popen(
        ["tar", "--use-compress-program=unzstd", "-xOf", str(archive), selected_member],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1024 * 1024,
    )
    assert process.stdout is not None
    try:
        for line in process.stdout:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value
    finally:
        process.stdout.close()
    stderr = process.stderr.read() if process.stderr is not None else ""
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"Could not stream ListenBrainz dump: {stderr.strip()}")


INSERT_USER_ARTIST_SQL = """
    INSERT INTO user_artist (user_key, artist_mbid, artist_name, listen_count)
    VALUES (?, ?, ?, ?)
    ON CONFLICT (user_key, artist_mbid) DO UPDATE SET
        listen_count = listen_count + excluded.listen_count,
        artist_name = CASE
            WHEN user_artist.artist_name = '' THEN excluded.artist_name
            ELSE user_artist.artist_name
        END
"""


def initialize_aggregate_db(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    database.unlink(missing_ok=True)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute(
        """
        CREATE TABLE user_artist (
            user_key BLOB NOT NULL,
            artist_mbid TEXT NOT NULL,
            artist_name TEXT NOT NULL,
            listen_count INTEGER NOT NULL,
            PRIMARY KEY (user_key, artist_mbid)
        ) WITHOUT ROWID
        """
    )
    return connection


def finalize_aggregate_db(
    connection: sqlite3.Connection,
    rows_seen: int,
    rows_with_mbid: int,
    artist_credits_seen: int,
) -> dict[str, int]:
    connection.execute("CREATE INDEX user_artist_by_artist ON user_artist (artist_mbid, user_key)")
    connection.commit()
    aggregate_rows = connection.execute("SELECT COUNT(*) FROM user_artist").fetchone()[0]
    users = connection.execute("SELECT COUNT(DISTINCT user_key) FROM user_artist").fetchone()[0]
    artists = connection.execute("SELECT COUNT(DISTINCT artist_mbid) FROM user_artist").fetchone()[0]
    connection.close()
    return {
        "dump_rows": rows_seen,
        "rows_with_artist_mbid": rows_with_mbid,
        "artist_credits": artist_credits_seen,
        "aggregated_user_artist_rows": aggregate_rows,
        "users": users,
        "artists": artists,
    }


def create_aggregate_db(archive: Path, database: Path, batch_size: int = 25_000) -> dict[str, int]:
    """Aggregate listens to user×artist counts; never store usernames or raw listens."""
    connection = initialize_aggregate_db(database)
    batch: list[tuple[bytes, str, str, int]] = []
    pseudonym_key = os.urandom(32)
    user_keys: dict[int, bytes] = {}
    rows_seen = 0
    rows_with_mbid = 0
    artist_credits_seen = 0
    for listen in iter_dump_listens(archive):
        rows_seen += 1
        user_id = listen.get("user_id")
        if not isinstance(user_id, int):
            continue
        user_key = user_keys.get(user_id)
        if user_key is None:
            user_key = pseudonymize_user_id(user_id, pseudonym_key)
            user_keys[user_id] = user_key
        artists = artist_records(listen)
        if not artists:
            continue
        rows_with_mbid += 1
        for mbid, name in artists:
            batch.append((user_key, mbid, name, 1))
            artist_credits_seen += 1
        if len(batch) >= batch_size:
            connection.executemany(INSERT_USER_ARTIST_SQL, batch)
            connection.commit()
            batch.clear()
    if batch:
        connection.executemany(INSERT_USER_ARTIST_SQL, batch)
        connection.commit()
    stats = finalize_aggregate_db(connection, rows_seen, rows_with_mbid, artist_credits_seen)
    stats["source_count"] = 1
    return stats


@contextmanager
def spark_parquet_paths(sources: Path | list[Path]) -> Iterator[list[Path]]:
    source_list = [sources] if isinstance(sources, Path) else sources
    if not source_list:
        raise ValueError("At least one Spark source is required")

    direct_paths: list[Path] = []
    archives: list[tuple[Path, list[str]]] = []
    for source in source_list:
        if source.is_dir():
            paths = sorted(source.rglob("*.parquet"))
            if not paths:
                raise ValueError(f"No Parquet files found under {source}")
            direct_paths.extend(paths)
            continue

        completed = subprocess.run(
            ["tar", "-tf", str(source)],
            check=True,
            capture_output=True,
            text=True,
        )
        members = [line for line in completed.stdout.splitlines() if line.endswith(".parquet")]
        if not members:
            raise ValueError(f"No Parquet members found in {source}")
        for member in members:
            member_path = Path(member)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"Unsafe archive member: {member}")
        archives.append((source, members))

    if not archives:
        yield direct_paths
        return

    with tempfile.TemporaryDirectory(prefix="listenbrainz-spark-", dir="/private/tmp") as temp_dir:
        extracted_paths: list[Path] = []
        for source, members in archives:
            subprocess.run(
                ["tar", "-xf", str(source), "-C", temp_dir, *members],
                check=True,
            )
            extracted_paths.extend(Path(temp_dir) / member for member in members)
        yield direct_paths + extracted_paths


def create_spark_aggregate_db(
    spark_sources: Path | list[Path],
    database: Path,
    batch_size: int = 100_000,
) -> dict[str, int]:
    """Aggregate ListenBrainz Spark rows using its mapped artist-credit MBIDs."""
    if arrow_dataset is None:
        raise RuntimeError("Install Spark dump support with: python -m pip install -r requirements.txt")

    source_count = 1 if isinstance(spark_sources, Path) else len(spark_sources)
    connection = initialize_aggregate_db(database)
    pseudonym_key = os.urandom(32)
    user_keys: dict[int, bytes] = {}
    pending: list[tuple[bytes, str, str, int]] = []
    rows_seen = 0
    rows_with_mbid = 0
    artist_credits_seen = 0

    with spark_parquet_paths(spark_sources) as paths:
        dataset = arrow_dataset.dataset([str(path) for path in paths], format="parquet")
        columns = ["user_id", "artist_name", "artist_credit_mbids"]
        for record_batch in dataset.to_batches(columns=columns, batch_size=65_536):
            values = record_batch.to_pydict()
            rows_seen += record_batch.num_rows
            for user_id, artist_name, mbids in zip(
                values["user_id"],
                values["artist_name"],
                values["artist_credit_mbids"],
                strict=True,
            ):
                if not isinstance(user_id, int):
                    continue
                artists = artist_credit_records(artist_name, mbids)
                if not artists:
                    continue
                rows_with_mbid += 1
                user_key = user_keys.get(user_id)
                if user_key is None:
                    user_key = pseudonymize_user_id(user_id, pseudonym_key)
                    user_keys[user_id] = user_key
                for mbid, name in artists:
                    pending.append((user_key, mbid, name, 1))
                    artist_credits_seen += 1
                if len(pending) >= batch_size:
                    connection.executemany(INSERT_USER_ARTIST_SQL, pending)
                    connection.commit()
                    pending.clear()

    if pending:
        connection.executemany(INSERT_USER_ARTIST_SQL, pending)
        connection.commit()
    stats = finalize_aggregate_db(connection, rows_seen, rows_with_mbid, artist_credits_seen)
    stats["source_count"] = source_count
    return stats


def load_targets(coverage_path: Path) -> dict[str, dict[str, str]]:
    with coverage_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    targets: dict[str, dict[str, str]] = {}
    for row in rows:
        mbid = row.get("resolved_mbid", "").strip().lower()
        if mbid:
            targets[mbid] = row
    if not targets:
        raise ValueError(f"No resolved_mbid values found in {coverage_path}")
    return targets


def weight(listen_count: int, cap: int) -> float:
    return math.log1p(min(max(listen_count, 0), cap))


def confidence_label(common_listeners: int) -> str:
    if common_listeners >= 30:
        return "high"
    if common_listeners >= 10:
        return "medium"
    return "low"


def compute_similarities(
    database: Path,
    targets: dict[str, dict[str, str]],
    config: SimilarityConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    connection = sqlite3.connect(database)
    target_mbids = set(targets)
    placeholders = ",".join("?" for _ in target_mbids)
    connection.execute("DROP TABLE IF EXISTS temp.target_users")
    connection.execute(
        f"CREATE TEMP TABLE target_users AS "
        f"SELECT DISTINCT user_key FROM user_artist WHERE artist_mbid IN ({placeholders})",
        tuple(target_mbids),
    )
    connection.execute("CREATE UNIQUE INDEX target_users_key ON target_users (user_key)")

    norms_squared: dict[str, float] = defaultdict(float)
    listener_counts: dict[str, int] = defaultdict(int)
    for mbid, listen_count in connection.execute("SELECT artist_mbid, listen_count FROM user_artist"):
        value = weight(listen_count, config.listen_cap)
        norms_squared[mbid] += value * value
        listener_counts[mbid] += 1

    dots: dict[tuple[str, str], float] = defaultdict(float)
    common: dict[tuple[str, str], int] = defaultdict(int)
    names: dict[str, str] = {}
    name_query = """
        SELECT artist_mbid, artist_name, SUM(listen_count) AS total_listens
        FROM user_artist
        WHERE artist_name <> ''
        GROUP BY artist_mbid, artist_name
        ORDER BY artist_mbid, total_listens DESC, artist_name COLLATE NOCASE
    """
    for mbid, name, _ in connection.execute(name_query):
        names.setdefault(mbid, name)
    current_user: bytes | None = None
    current_artists: list[tuple[str, float]] = []

    def consume_user(items: list[tuple[str, float]]) -> None:
        seeds = [(mbid, value) for mbid, value in items if mbid in target_mbids]
        for seed_mbid, seed_value in seeds:
            for candidate_mbid, candidate_value in items:
                if candidate_mbid == seed_mbid:
                    continue
                pair = (seed_mbid, candidate_mbid)
                dots[pair] += seed_value * candidate_value
                common[pair] += 1

    query = """
        SELECT ua.user_key, ua.artist_mbid, ua.artist_name, ua.listen_count
        FROM user_artist AS ua
        JOIN target_users AS tu ON tu.user_key = ua.user_key
        ORDER BY ua.user_key
    """
    for user_key, mbid, _name, listen_count in connection.execute(query):
        if current_user is not None and user_key != current_user:
            consume_user(current_artists)
            current_artists = []
        current_user = user_key
        current_artists.append((mbid, weight(listen_count, config.listen_cap)))
    if current_artists:
        consume_user(current_artists)

    rows: list[dict[str, Any]] = []
    per_seed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (seed_mbid, candidate_mbid), dot in dots.items():
        common_count = common[(seed_mbid, candidate_mbid)]
        if common_count < config.min_common_listeners:
            continue
        denominator = math.sqrt(norms_squared[seed_mbid] * norms_squared[candidate_mbid])
        if denominator <= 0:
            continue
        cosine = dot / denominator
        shrinkage_factor = common_count / (common_count + config.shrinkage)
        score = cosine * shrinkage_factor
        target = targets[seed_mbid]
        candidate_name = (
            targets.get(candidate_mbid, {}).get("artist_name") or names.get(candidate_mbid, "")
        )
        if not candidate_name:
            # A multi-artist credit can supply several MBIDs without the
            # individual names. Do not show an unidentifiable recommendation.
            continue
        row = {
            "seed_artist_name": target.get("artist_name", ""),
            "seed_artist_mbid": seed_mbid,
            "candidate_artist_name": candidate_name,
            "candidate_artist_mbid": candidate_mbid,
            "similarity_score": round(score, 6),
            "cosine_similarity": round(cosine, 6),
            "shrinkage_factor": round(shrinkage_factor, 6),
            "common_listener_count": common_count,
            "candidate_listener_count": listener_counts[candidate_mbid],
            "confidence": confidence_label(common_count),
            "reason": f"共通リスナー{common_count}人の聴取傾向が近い",
        }
        per_seed[seed_mbid].append(row)

    for seed_mbid, candidates in per_seed.items():
        candidates.sort(
            key=lambda row: (
                row["similarity_score"],
                row["common_listener_count"],
                row["candidate_artist_mbid"],
            ),
            reverse=True,
        )
        for rank, row in enumerate(candidates[: config.limit], start=1):
            row["rank"] = rank
            rows.append(row)
    rows.sort(key=lambda row: (row["seed_artist_name"].casefold(), row["rank"]))

    expected_hits_all = 0
    evaluated_all = 0
    expected_hits_available = 0
    evaluated_available = 0
    seed_summaries: list[dict[str, Any]] = []
    for seed_mbid, target in targets.items():
        expected = {
            normalize_name(name)
            for name in target.get("expected_similar", "").split("|")
            if name.strip()
        }
        candidate_rows = [row for row in rows if row["seed_artist_mbid"] == seed_mbid]
        returned_names = {normalize_name(row["candidate_artist_name"]) for row in candidate_rows}
        hit_names = sorted(expected & returned_names)
        if expected:
            evaluated_all += 1
            expected_hits_all += bool(hit_names)
            if candidate_rows:
                evaluated_available += 1
                expected_hits_available += bool(hit_names)
        seed_summaries.append(
            {
                "artist_name": target.get("artist_name", ""),
                "mbid": seed_mbid,
                "listener_count_in_window": listener_counts.get(seed_mbid, 0),
                "recommendation_count": len(candidate_rows),
                "expected_hit": bool(hit_names),
                "expected_hit_names": hit_names,
            }
        )

    selected_users = connection.execute("SELECT COUNT(*) FROM target_users").fetchone()[0]
    connection.close()
    summary = {
        "target_artist_count": len(targets),
        "target_artists_with_listeners": sum(
            listener_counts.get(mbid, 0) > 0 for mbid in target_mbids
        ),
        "target_listener_union": selected_users,
        "target_artists_with_recommendations": sum(
            item["recommendation_count"] > 0 for item in seed_summaries
        ),
        "expected_top10_hit_rate_all_targets": (
            round(expected_hits_all / evaluated_all, 4) if evaluated_all else None
        ),
        "expected_top10_hit_rate_with_recommendations": (
            round(expected_hits_available / evaluated_available, 4) if evaluated_available else None
        ),
        "expected_hit_count": expected_hits_all,
        "expected_evaluated_all_count": evaluated_all,
        "expected_evaluated_available_count": evaluated_available,
        "config": {
            "listen_cap": config.listen_cap,
            "shrinkage": config.shrinkage,
            "min_common_listeners": config.min_common_listeners,
            "limit": config.limit,
        },
        "artists": seed_summaries,
    }
    return rows, summary


def write_summary(path: Path, dump_stats: dict[str, int], summary: dict[str, Any]) -> None:
    top_k = summary["config"]["limit"]
    mbid_rate = dump_stats["rows_with_artist_mbid"] / dump_stats["dump_rows"]
    source_count = dump_stats.get("source_count", 1)
    lines = [
        "# Phase 0 similarity report",
        "",
        f"ListenBrainzの増分ダンプ{source_count}個をユーザー×アーティストに集計し、log変換した再生数のcosine類似度を共通リスナー数で補正した試作結果です。ユーザー名・ユーザーID・個人別履歴は出力していません。",
        "",
        f"- ダンプ内listen行数: {dump_stats['dump_rows']:,}",
        f"- 入力ダンプ数: {source_count}",
        f"- Artist MBID付きlisten行数: {dump_stats['rows_with_artist_mbid']:,}",
        f"- Artist MBID利用率: {mbid_rate:.1%}",
        f"- 集計対象ユーザー数: {dump_stats['users']:,}",
        f"- 集計対象アーティスト数: {dump_stats['artists']:,}",
        f"- 50組のうち期間内リスナーあり: {summary['target_artists_with_listeners']}/{summary['target_artist_count']}",
        f"- 50組のうち推薦を出せた数: {summary['target_artists_with_recommendations']}/{summary['target_artist_count']}",
        f"- 手入力した期待候補のTop {top_k} hit率（50組全体）: {summary['expected_top10_hit_rate_all_targets']:.1%}",
        f"- 手入力した期待候補のTop {top_k} hit率（推薦を出せた組のみ）: {summary['expected_top10_hit_rate_with_recommendations']:.1%}",
        "",
        "このhit率は正解率そのものではなく、手入力した少数の参考候補が上位に入った割合です。期間やデータ量が少ないアーティストは低信頼になりやすい点に注意してください。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run(
    archive: Path | None,
    coverage: Path,
    report_dir: Path,
    work_db: Path,
    config: SimilarityConfig,
    spark_archive: list[Path] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if (archive is None) == (spark_archive is None):
        raise ValueError("Provide exactly one of archive or spark_archive")
    dump_stats = (
        create_spark_aggregate_db(spark_archive, work_db)
        if spark_archive is not None
        else create_aggregate_db(archive, work_db)  # type: ignore[arg-type]
    )
    targets = load_targets(coverage)
    rows, summary = compute_similarities(work_db, targets, config)
    report_dir.mkdir(parents=True, exist_ok=True)
    write_csv(report_dir / "similarity_top10.csv", rows)
    result = {"dump": dump_stats, "similarity": summary}
    (report_dir / "similarity_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_summary(report_dir / "similarity_summary.md", dump_stats, summary)
    return rows, result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calculate artist similarity from a ListenBrainz dump")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--spark-archive",
        type=Path,
        nargs="+",
        help="One or more mapped ListenBrainz Spark .tar files or directories",
    )
    source.add_argument("--archive", type=Path, help="Raw ListenBrainz listens .tar.zst (lower coverage)")
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--work-db", type=Path, default=DEFAULT_WORK_DB)
    parser.add_argument("--listen-cap", type=int, default=100)
    parser.add_argument("--shrinkage", type=float, default=10.0)
    parser.add_argument("--min-common-listeners", type=int, default=2)
    parser.add_argument("--limit", type=int, default=10)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = SimilarityConfig(
        listen_cap=args.listen_cap,
        shrinkage=args.shrinkage,
        min_common_listeners=args.min_common_listeners,
        limit=args.limit,
    )
    _, result = run(
        args.archive,
        args.coverage,
        args.report_dir,
        args.work_db,
        config,
        spark_archive=args.spark_archive,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sqlite3
from array import array
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

try:
    from implicit.als import AlternatingLeastSquares
except ModuleNotFoundError:
    AlternatingLeastSquares = None  # type: ignore[assignment]

try:
    import pyarrow.dataset as arrow_dataset
except ModuleNotFoundError:
    arrow_dataset = None  # type: ignore[assignment]

from src.algorithm_comparison import ALSConfig, SessionConfig, build_als_matrix
from src.evaluation import DEFAULT_OUTPUT as DEFAULT_EVALUATION
from src.evaluation import DEFAULT_SIMILARITY, read_csv
from src.evaluation_metrics import parse_rating
from src.feasibility import write_csv
from src.similarity import DEFAULT_COVERAGE, DEFAULT_WORK_DB, spark_parquet_paths


DEFAULT_REPORT_DIR = Path("reports")
DEFAULT_CATEGORY = "kpop_girl_group"
METHODS = ("cosine_shrinkage", "session_cooccurrence", "implicit_als")


def load_seeds(coverage: Path, category: str) -> dict[str, str]:
    with coverage.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result = {
        row.get("resolved_mbid", "").strip().lower(): row.get("artist_name", "").strip()
        for row in rows
        if row.get("category", "").strip() == category
        and row.get("resolved_mbid", "").strip()
    }
    if not result:
        raise ValueError(f"No seeds found for category {category!r}")
    return result


def load_artist_names(database: Path, seeds: dict[str, str]) -> dict[str, str]:
    names = dict(seeds)
    connection = sqlite3.connect(database)
    query = """
        SELECT artist_mbid, artist_name, SUM(listen_count) AS total_listens
        FROM user_artist
        WHERE artist_name <> ''
        GROUP BY artist_mbid, artist_name
        ORDER BY artist_mbid, total_listens DESC, artist_name COLLATE NOCASE
    """
    for mbid, name, _total in connection.execute(query):
        names.setdefault(str(mbid), str(name))
    connection.close()
    return names


def load_cosine_retrievals(
    similarity: Path, seeds: dict[str, str], limit: int
) -> list[dict[str, Any]]:
    seed_mbids = set(seeds)
    rows: list[dict[str, Any]] = []
    for row in read_csv(similarity):
        seed_mbid = row.get("seed_artist_mbid", "").strip().lower()
        if seed_mbid not in seed_mbids or int(row.get("rank") or 0) > limit:
            continue
        rows.append(
            {
                "method": "cosine_shrinkage",
                "seed_artist_name": seeds[seed_mbid],
                "seed_artist_mbid": seed_mbid,
                "rank": int(row["rank"]),
                "candidate_artist_name": row.get("candidate_artist_name", ""),
                "candidate_artist_mbid": row.get("candidate_artist_mbid", "").lower(),
                "score": float(row.get("similarity_score") or 0.0),
                "evidence_count": int(row.get("common_listener_count") or 0),
            }
        )
    return sorted(rows, key=lambda row: (row["seed_artist_name"].casefold(), row["rank"]))


def compute_session_retrievals(
    spark_sources: list[Path],
    seeds: dict[str, str],
    names: dict[str, str],
    config: SessionConfig,
    limit: int,
    min_common_sessions: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if arrow_dataset is None:
        raise RuntimeError("Install Spark dump support with: python -m pip install -r requirements.txt")

    artist_to_code: dict[str, int] = {}
    code_to_artist: list[str] = []
    events_by_user: dict[int, tuple[array[int], array[int]]] = {}
    rows_seen = 0
    stored_events = 0

    def artist_code(mbid: str) -> int:
        existing = artist_to_code.get(mbid)
        if existing is not None:
            return existing
        code = len(code_to_artist)
        artist_to_code[mbid] = code
        code_to_artist.append(mbid)
        return code

    with spark_parquet_paths(spark_sources) as paths:
        dataset = arrow_dataset.dataset([str(path) for path in paths], format="parquet")
        for batch_number, batch in enumerate(
            dataset.to_batches(
                columns=["listened_at", "user_id", "artist_credit_mbids"],
                batch_size=131_072,
            ),
            1,
        ):
            values = batch.to_pydict()
            rows_seen += batch.num_rows
            for listened_at, user_id, raw_mbids in zip(
                values["listened_at"],
                values["user_id"],
                values["artist_credit_mbids"],
                strict=True,
            ):
                if not isinstance(user_id, int):
                    continue
                event_arrays = events_by_user.get(user_id)
                if event_arrays is None:
                    event_arrays = (array("q"), array("i"))
                    events_by_user[user_id] = event_arrays
                timestamps, codes = event_arrays
                timestamp = int(listened_at.timestamp())
                mbids = {
                    normalized
                    for raw_mbid in (raw_mbids if isinstance(raw_mbids, list) else [])
                    if (normalized := str(raw_mbid or "").strip().lower())
                }
                if not mbids:
                    timestamps.append(timestamp)
                    codes.append(-1)
                    stored_events += 1
                    continue
                for mbid in mbids:
                    timestamps.append(timestamp)
                    codes.append(artist_code(mbid))
                    stored_events += 1
            if batch_number % 25 == 0:
                print(
                    f"Session input: {rows_seen:,} listens, {len(events_by_user):,} users",
                    flush=True,
                )

    seed_codes = {
        artist_to_code[mbid]: mbid for mbid in seeds if mbid in artist_to_code
    }
    artist_sessions: dict[int, int] = defaultdict(int)
    common_by_seed: dict[int, dict[int, int]] = {
        seed_code: defaultdict(int) for seed_code in seed_codes
    }
    sessions_seen = 0

    def consume_session(session_codes: set[int]) -> None:
        nonlocal sessions_seen
        if not session_codes:
            return
        sessions_seen += 1
        for code in session_codes:
            artist_sessions[code] += 1
        for seed_code in seed_codes.keys() & session_codes:
            counts = common_by_seed[seed_code]
            for candidate_code in session_codes:
                if candidate_code != seed_code:
                    counts[candidate_code] += 1

    users_processed = 0
    while events_by_user:
        _user_id, (timestamps, codes) = events_by_user.popitem()
        timestamp_values = np.frombuffer(timestamps, dtype=np.int64)
        code_values = np.frombuffer(codes, dtype=np.int32)
        order = np.argsort(timestamp_values, kind="stable")
        last_timestamp: int | None = None
        session_codes: set[int] = set()
        for position in order:
            timestamp = int(timestamp_values[position])
            if last_timestamp is not None and timestamp - last_timestamp > config.gap_seconds:
                consume_session(session_codes)
                session_codes = set()
            code = int(code_values[position])
            if code >= 0:
                session_codes.add(code)
            last_timestamp = timestamp
        consume_session(session_codes)
        users_processed += 1
        if users_processed % 5_000 == 0:
            print(
                f"Sessionize: {users_processed:,} users, {sessions_seen:,} sessions",
                flush=True,
            )

    rows: list[dict[str, Any]] = []
    for seed_code, seed_mbid in seed_codes.items():
        candidates: list[dict[str, Any]] = []
        for candidate_code, common in common_by_seed[seed_code].items():
            if common < min_common_sessions:
                continue
            candidate_mbid = code_to_artist[candidate_code]
            candidate_name = names.get(candidate_mbid, "")
            if not candidate_name:
                continue
            denominator = math.sqrt(
                artist_sessions[seed_code] * artist_sessions[candidate_code]
            )
            cosine = common / denominator if denominator else 0.0
            shrinkage = common / (common + config.shrinkage)
            candidates.append(
                {
                    "method": "session_cooccurrence",
                    "seed_artist_name": seeds[seed_mbid],
                    "seed_artist_mbid": seed_mbid,
                    "candidate_artist_name": candidate_name,
                    "candidate_artist_mbid": candidate_mbid,
                    "score": cosine * shrinkage,
                    "evidence_count": common,
                }
            )
        candidates.sort(
            key=lambda row: (row["score"], row["evidence_count"], row["candidate_artist_mbid"]),
            reverse=True,
        )
        for rank, row in enumerate(candidates[:limit], 1):
            row["rank"] = rank
            row["score"] = round(float(row["score"]), 8)
            rows.append(row)
    rows.sort(key=lambda row: (row["seed_artist_name"].casefold(), row["rank"]))
    return rows, {
        "dump_rows": rows_seen,
        "users": users_processed,
        "stored_timestamp_artist_events": stored_events,
        "artists": len(code_to_artist),
        "sessions": sessions_seen,
    }


def compute_als_retrievals(
    database: Path,
    seeds: dict[str, str],
    names: dict[str, str],
    config: ALSConfig,
    listen_cap: int,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if AlternatingLeastSquares is None:
        raise RuntimeError("Install ALS support with: python -m pip install -r requirements.txt")
    matrix, item_to_index, stats = build_als_matrix(database, listen_cap)
    index_to_item = [""] * len(item_to_index)
    for mbid, index in item_to_index.items():
        index_to_item[index] = mbid
    model = AlternatingLeastSquares(
        factors=config.factors,
        regularization=config.regularization,
        iterations=config.iterations,
        alpha=config.alpha,
        random_state=config.random_state,
    )
    model.fit(matrix, show_progress=False)
    rows: list[dict[str, Any]] = []
    missing_seeds: list[str] = []
    for seed_mbid, seed_name in seeds.items():
        seed_index = item_to_index.get(seed_mbid)
        if seed_index is None:
            missing_seeds.append(seed_mbid)
            continue
        candidate_ids, scores = model.similar_items(seed_index, N=500)
        candidates: list[dict[str, Any]] = []
        for candidate_index, score in zip(candidate_ids, scores, strict=True):
            candidate_mbid = index_to_item[int(candidate_index)]
            if candidate_mbid == seed_mbid:
                continue
            candidate_name = names.get(candidate_mbid, "")
            if not candidate_name:
                continue
            candidates.append(
                {
                    "method": "implicit_als",
                    "seed_artist_name": seed_name,
                    "seed_artist_mbid": seed_mbid,
                    "candidate_artist_name": candidate_name,
                    "candidate_artist_mbid": candidate_mbid,
                    "score": round(float(score), 8),
                    "evidence_count": "",
                }
            )
            if len(candidates) >= limit:
                break
        for rank, row in enumerate(candidates, 1):
            row["rank"] = rank
            rows.append(row)
    rows.sort(key=lambda row: (row["seed_artist_name"].casefold(), row["rank"]))
    return rows, {
        **stats,
        "missing_seed_mbids": missing_seeds,
        "factors": config.factors,
        "regularization": config.regularization,
        "iterations": config.iterations,
        "alpha": config.alpha,
        "random_state": config.random_state,
        "listen_cap": listen_cap,
    }


def build_blind_evaluation(
    retrieval_rows: list[dict[str, Any]],
    previous_evaluation_rows: list[dict[str, str]],
    random_state: int = 42,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    previous = {
        (
            row.get("seed_artist_name", ""),
            row.get("candidate_artist_mbid", "").strip().lower(),
        ): row
        for row in previous_evaluation_rows
    }
    key_rows = sorted(
        retrieval_rows,
        key=lambda row: (
            str(row["seed_artist_name"]).casefold(),
            str(row["method"]),
            int(row["rank"]),
        ),
    )
    union: dict[tuple[str, str], dict[str, Any]] = {}
    for row in retrieval_rows:
        key = (str(row["seed_artist_name"]), str(row["candidate_artist_mbid"]))
        union.setdefault(
            key,
            {
                "seed_artist_name": row["seed_artist_name"],
                "candidate_artist_name": row["candidate_artist_name"],
                "candidate_artist_mbid": row["candidate_artist_mbid"],
            },
        )

    rng = random.Random(random_state)
    by_seed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in union.values():
        by_seed[str(row["seed_artist_name"])].append(row)
    blind_rows: list[dict[str, Any]] = []
    blind_number = 1
    for seed_name in sorted(by_seed, key=str.casefold):
        candidates = by_seed[seed_name]
        candidates.sort(key=lambda row: str(row["candidate_artist_mbid"]))
        rng.shuffle(candidates)
        for row in candidates:
            prior = previous.get((seed_name, str(row["candidate_artist_mbid"])))
            prior_rating = parse_rating(prior.get("human_rating_0_1_2")) if prior else None
            blind_rows.append(
                {
                    "blind_candidate_id": f"KPOP-{blind_number:03d}",
                    **row,
                    "needs_rating": "no" if prior_rating is not None else "yes",
                    "human_rating_0_1_2": "" if prior_rating is None else prior_rating,
                    "human_notes": prior.get("human_notes", "") if prior else "",
                }
            )
            blind_number += 1
    return blind_rows, key_rows


def summarize_retrievals(
    retrieval_rows: list[dict[str, Any]], blind_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    sets = {
        method: {
            (str(row["seed_artist_mbid"]), str(row["candidate_artist_mbid"]))
            for row in retrieval_rows
            if row["method"] == method
        }
        for method in METHODS
    }
    pairwise: dict[str, Any] = {}
    for left, right in (
        ("cosine_shrinkage", "session_cooccurrence"),
        ("cosine_shrinkage", "implicit_als"),
        ("session_cooccurrence", "implicit_als"),
    ):
        intersection = len(sets[left] & sets[right])
        union = len(sets[left] | sets[right])
        pairwise[f"{left}__{right}"] = {
            "intersection": intersection,
            "jaccard": intersection / union if union else None,
        }
    per_seed = []
    for seed in sorted({row["seed_artist_name"] for row in blind_rows}, key=str.casefold):
        rows = [row for row in blind_rows if row["seed_artist_name"] == seed]
        per_seed.append(
            {
                "seed_artist_name": seed,
                "union_candidate_count": len(rows),
                "already_rated_count": sum(row["needs_rating"] == "no" for row in rows),
                "new_rating_count": sum(row["needs_rating"] == "yes" for row in rows),
            }
        )
    return {
        "retrieved_count_by_method": {method: len(sets[method]) for method in METHODS},
        "union_candidate_count": len(blind_rows),
        "already_rated_count": sum(row["needs_rating"] == "no" for row in blind_rows),
        "new_rating_count": sum(row["needs_rating"] == "yes" for row in blind_rows),
        "pairwise_overlap": pairwise,
        "per_seed": per_seed,
    }


def run(
    similarity: Path,
    evaluation: Path,
    coverage: Path,
    work_db: Path,
    spark_sources: list[Path],
    report_dir: Path,
    category: str,
    limit: int,
    session_config: SessionConfig,
    als_config: ALSConfig,
    listen_cap: int,
) -> dict[str, Any]:
    seeds = load_seeds(coverage, category)
    names = load_artist_names(work_db, seeds)
    cosine_rows = load_cosine_retrievals(similarity, seeds, limit)
    print(f"Cosine retrievals: {len(cosine_rows)}", flush=True)
    session_rows, session_stats = compute_session_retrievals(
        spark_sources, seeds, names, session_config, limit
    )
    print(f"Session retrievals: {len(session_rows)}", flush=True)
    als_rows, als_stats = compute_als_retrievals(
        work_db, seeds, names, als_config, listen_cap, limit
    )
    print(f"ALS retrievals: {len(als_rows)}", flush=True)
    retrieval_rows = cosine_rows + session_rows + als_rows
    blind_rows, key_rows = build_blind_evaluation(retrieval_rows, read_csv(evaluation))
    summary = summarize_retrievals(retrieval_rows, blind_rows)
    result = {
        **summary,
        "session": {
            **session_stats,
            "gap_seconds": session_config.gap_seconds,
            "shrinkage": session_config.shrinkage,
        },
        "als": als_stats,
        "blind_random_state": 42,
        "rating_instruction": "2=かなり納得, 1=意外だがあり, 0=違う",
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    write_csv(report_dir / "kpop_full_retrieval_evaluation.csv", blind_rows)
    write_csv(report_dir / "kpop_full_retrieval_key.csv", key_rows)
    (report_dir / "kpop_full_retrieval_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Retrieve independent Top 10s and build blind ratings")
    parser.add_argument("--similarity", type=Path, default=DEFAULT_SIMILARITY)
    parser.add_argument("--evaluation", type=Path, default=DEFAULT_EVALUATION)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--work-db", type=Path, default=DEFAULT_WORK_DB)
    parser.add_argument("--spark-archive", type=Path, nargs="+", required=True)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--category", default=DEFAULT_CATEGORY)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--session-gap-minutes", type=int, default=30)
    parser.add_argument("--session-shrinkage", type=float, default=10.0)
    parser.add_argument("--als-factors", type=int, default=64)
    parser.add_argument("--als-regularization", type=float, default=0.1)
    parser.add_argument("--als-iterations", type=int, default=15)
    parser.add_argument("--als-alpha", type=float, default=20.0)
    parser.add_argument("--listen-cap", type=int, default=100)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run(
        args.similarity,
        args.evaluation,
        args.coverage,
        args.work_db,
        args.spark_archive,
        args.report_dir,
        args.category,
        args.limit,
        SessionConfig(
            gap_seconds=args.session_gap_minutes * 60,
            shrinkage=args.session_shrinkage,
        ),
        ALSConfig(
            factors=args.als_factors,
            regularization=args.als_regularization,
            iterations=args.als_iterations,
            alpha=args.als_alpha,
        ),
        args.listen_cap,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

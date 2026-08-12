from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from array import array
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from scipy.sparse import coo_matrix

try:
    from implicit.als import AlternatingLeastSquares
except ModuleNotFoundError:
    AlternatingLeastSquares = None  # type: ignore[assignment]

try:
    import pyarrow.dataset as arrow_dataset
except ModuleNotFoundError:
    arrow_dataset = None  # type: ignore[assignment]

from src.evaluation import DEFAULT_OUTPUT as DEFAULT_EVALUATION
from src.evaluation import read_csv
from src.evaluation_metrics import parse_rating, summarize_ranked_rows
from src.feasibility import write_csv
from src.similarity import DEFAULT_COVERAGE, DEFAULT_WORK_DB, spark_parquet_paths, weight


DEFAULT_REPORT_DIR = Path("reports")


@dataclass(frozen=True)
class ALSConfig:
    factors: int = 64
    regularization: float = 0.1
    iterations: int = 15
    alpha: float = 20.0
    random_state: int = 42


@dataclass(frozen=True)
class SessionConfig:
    gap_seconds: int = 30 * 60
    shrinkage: float = 10.0


def load_pairs(evaluation: Path, coverage: Path) -> list[dict[str, Any]]:
    with coverage.open(encoding="utf-8", newline="") as handle:
        coverage_rows = list(csv.DictReader(handle))
    seed_mbids = {
        row.get("artist_name", ""): row.get("resolved_mbid", "").strip().lower()
        for row in coverage_rows
    }
    pairs: list[dict[str, Any]] = []
    for row in read_csv(evaluation):
        rating = parse_rating(row.get("human_rating_0_1_2"))
        if rating is None:
            raise ValueError("All comparison rows must have a human rating")
        seed_name = row.get("seed_artist_name", "")
        seed_mbid = seed_mbids.get(seed_name, "")
        candidate_mbid = row.get("candidate_artist_mbid", "").strip().lower()
        if not seed_mbid or not candidate_mbid:
            raise ValueError(f"Missing MBID for {seed_name} / {row.get('candidate_artist_name', '')}")
        pairs.append(
            {
                **row,
                "seed_artist_mbid": seed_mbid,
                "candidate_artist_mbid": candidate_mbid,
                "human_rating_0_1_2": rating,
                "original_rank": int(row.get("rank") or 0),
                "cosine_shrinkage_score": float(row.get("similarity_score") or 0.0),
            }
        )
    return pairs


def pair_keys(pairs: Iterable[dict[str, Any]]) -> set[tuple[str, str]]:
    return {
        (str(row["seed_artist_mbid"]), str(row["candidate_artist_mbid"]))
        for row in pairs
    }


def score_session_event_groups(
    events_by_user: dict[int, list[tuple[int, tuple[str, ...]]]],
    pairs: set[tuple[str, str]],
    config: SessionConfig,
) -> dict[tuple[str, str], dict[str, float | int]]:
    relevant_mbids = {mbid for pair in pairs for mbid in pair}
    artist_sessions: dict[str, int] = defaultdict(int)
    common_sessions: dict[tuple[str, str], int] = defaultdict(int)

    def consume_session(artists: set[str]) -> None:
        if not artists:
            return
        for mbid in artists:
            artist_sessions[mbid] += 1
        seeds_present = {seed for seed, _candidate in pairs if seed in artists}
        for seed in seeds_present:
            for candidate in artists:
                key = (seed, candidate)
                if key in pairs:
                    common_sessions[key] += 1

    for events in events_by_user.values():
        events.sort(key=lambda item: item[0])
        last_timestamp: int | None = None
        session_artists: set[str] = set()
        for timestamp, mbids in events:
            if last_timestamp is not None and timestamp - last_timestamp > config.gap_seconds:
                consume_session(session_artists)
                session_artists = set()
            session_artists.update(mbid for mbid in mbids if mbid in relevant_mbids)
            last_timestamp = timestamp
        consume_session(session_artists)

    result: dict[tuple[str, str], dict[str, float | int]] = {}
    for seed, candidate in pairs:
        common = common_sessions[(seed, candidate)]
        denominator = math.sqrt(artist_sessions[seed] * artist_sessions[candidate])
        cosine = common / denominator if denominator else 0.0
        shrinkage_factor = common / (common + config.shrinkage) if common else 0.0
        result[(seed, candidate)] = {
            "score": cosine * shrinkage_factor,
            "cosine": cosine,
            "common_sessions": common,
            "seed_sessions": artist_sessions[seed],
            "candidate_sessions": artist_sessions[candidate],
        }
    return result


def compute_session_scores(
    spark_sources: list[Path],
    pairs: set[tuple[str, str]],
    config: SessionConfig,
) -> tuple[dict[tuple[str, str], dict[str, float | int]], dict[str, int]]:
    if arrow_dataset is None:
        raise RuntimeError("Install Spark dump support with: python -m pip install -r requirements.txt")
    seed_mbids = {seed for seed, _candidate in pairs}
    relevant_mbids = {mbid for pair in pairs for mbid in pair}
    selected_users: set[int] = set()
    rows_seen_first_pass = 0
    selected_rows = 0
    events_by_user: dict[int, list[tuple[int, tuple[str, ...]]]] = defaultdict(list)

    with spark_parquet_paths(spark_sources) as paths:
        dataset = arrow_dataset.dataset([str(path) for path in paths], format="parquet")
        for batch in dataset.to_batches(
            columns=["user_id", "artist_credit_mbids"], batch_size=131_072
        ):
            values = batch.to_pydict()
            rows_seen_first_pass += batch.num_rows
            for user_id, raw_mbids in zip(
                values["user_id"], values["artist_credit_mbids"], strict=True
            ):
                if not isinstance(user_id, int) or not isinstance(raw_mbids, list):
                    continue
                if any(str(mbid or "").lower() in seed_mbids for mbid in raw_mbids):
                    selected_users.add(user_id)

        for batch in dataset.to_batches(
            columns=["listened_at", "user_id", "artist_credit_mbids"],
            batch_size=131_072,
        ):
            values = batch.to_pydict()
            for listened_at, user_id, raw_mbids in zip(
                values["listened_at"],
                values["user_id"],
                values["artist_credit_mbids"],
                strict=True,
            ):
                if user_id not in selected_users:
                    continue
                selected_rows += 1
                mbids = tuple(
                    normalized
                    for raw_mbid in (raw_mbids if isinstance(raw_mbids, list) else [])
                    if (normalized := str(raw_mbid or "").strip().lower()) in relevant_mbids
                )
                events_by_user[user_id].append((int(listened_at.timestamp()), mbids))

    scores = score_session_event_groups(events_by_user, pairs, config)
    stats = {
        "dump_rows_scanned": rows_seen_first_pass,
        "seed_listener_users": len(selected_users),
        "selected_user_listens_sessionized": selected_rows,
    }
    return scores, stats


def build_als_matrix(
    database: Path, listen_cap: int
) -> tuple[Any, dict[str, int], dict[str, int]]:
    connection = sqlite3.connect(database)
    user_indices = array("i")
    item_indices = array("i")
    values = array("f")
    item_to_index: dict[str, int] = {}
    current_user: bytes | None = None
    user_index = -1
    query = "SELECT user_key, artist_mbid, listen_count FROM user_artist ORDER BY user_key, artist_mbid"
    for user_key, artist_mbid, listen_count in connection.execute(query):
        if current_user != user_key:
            current_user = user_key
            user_index += 1
        item_index = item_to_index.setdefault(artist_mbid, len(item_to_index))
        user_indices.append(user_index)
        item_indices.append(item_index)
        values.append(weight(int(listen_count), listen_cap))
    connection.close()

    matrix = coo_matrix(
        (
            np.frombuffer(values, dtype=np.float32),
            (
                np.frombuffer(user_indices, dtype=np.int32),
                np.frombuffer(item_indices, dtype=np.int32),
            ),
        ),
        shape=(user_index + 1, len(item_to_index)),
        dtype=np.float32,
    ).tocsr()
    return matrix, item_to_index, {
        "users": matrix.shape[0],
        "artists": matrix.shape[1],
        "interactions": matrix.nnz,
    }


def cosine_factor_score(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denominator) if denominator else 0.0


def compute_als_scores(
    database: Path,
    pairs: set[tuple[str, str]],
    config: ALSConfig,
    listen_cap: int,
) -> tuple[dict[tuple[str, str], float], dict[str, Any]]:
    if AlternatingLeastSquares is None:
        raise RuntimeError("Install ALS support with: python -m pip install -r requirements.txt")
    matrix, item_to_index, stats = build_als_matrix(database, listen_cap)
    model = AlternatingLeastSquares(
        factors=config.factors,
        regularization=config.regularization,
        iterations=config.iterations,
        alpha=config.alpha,
        random_state=config.random_state,
    )
    model.fit(matrix, show_progress=False)
    factors = model.item_factors
    scores: dict[tuple[str, str], float] = {}
    missing: set[str] = set()
    for seed, candidate in pairs:
        seed_index = item_to_index.get(seed)
        candidate_index = item_to_index.get(candidate)
        if seed_index is None or candidate_index is None:
            missing.update(
                mbid
                for mbid, index in ((seed, seed_index), (candidate, candidate_index))
                if index is None
            )
            scores[(seed, candidate)] = 0.0
            continue
        scores[(seed, candidate)] = cosine_factor_score(
            factors[seed_index], factors[candidate_index]
        )
    return scores, {**stats, "missing_pair_mbids": sorted(missing)}


def add_method_ranks(rows: list[dict[str, Any]], score_field: str, rank_field: str) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["seed_artist_mbid"])].append(row)
    for items in grouped.values():
        items.sort(
            key=lambda row: (float(row[score_field]), str(row["candidate_artist_mbid"])),
            reverse=True,
        )
        for rank, row in enumerate(items, 1):
            row[rank_field] = rank


def method_metrics(rows: list[dict[str, Any]], rank_field: str) -> dict[str, Any]:
    summary = summarize_ranked_rows(rows, rank_field=rank_field)
    return {key: value for key, value in summary.items() if key != "by_artist"} | {
        "by_artist": summary["by_artist"]
    }


def bootstrap_ndcg_deltas(
    methods: dict[str, dict[str, Any]],
    baseline: str = "cosine_shrinkage",
    samples: int = 20_000,
    random_state: int = 42,
) -> dict[str, Any]:
    baseline_by_seed = {
        row["seed_artist_name"]: float(row["ndcg_at_10"])
        for row in methods[baseline]["by_artist"]
    }
    seeds = sorted(baseline_by_seed, key=str.casefold)
    rng = np.random.default_rng(random_state)
    result: dict[str, Any] = {
        "baseline": baseline,
        "unit": "seed_artist",
        "seed_count": len(seeds),
        "samples": samples,
        "comparisons": {},
    }
    for method, metrics in methods.items():
        if method == baseline:
            continue
        method_by_seed = {
            row["seed_artist_name"]: float(row["ndcg_at_10"])
            for row in metrics["by_artist"]
        }
        deltas = np.array(
            [method_by_seed[seed] - baseline_by_seed[seed] for seed in seeds],
            dtype=np.float64,
        )
        draws = rng.integers(0, len(deltas), size=(samples, len(deltas)))
        bootstrap_means = deltas[draws].mean(axis=1)
        result["comparisons"][method] = {
            "mean_ndcg_delta": float(deltas.mean()),
            "ci_95_low": float(np.quantile(bootstrap_means, 0.025)),
            "ci_95_high": float(np.quantile(bootstrap_means, 0.975)),
            "bootstrap_probability_better": float(np.mean(bootstrap_means > 0)),
        }
    return result


def write_comparison_markdown(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# K-POP推薦アルゴリズム比較",
        "",
        "採点済みの同じ100候補を3方式で並べ替え、順位品質を比較しました。",
        "",
        "| 方式 | 厳しいP@5 | 厳しいP@10 | NDCG@10 |",
        "|---|---:|---:|---:|",
    ]
    labels = {
        "cosine_shrinkage": "cosine＋shrinkage",
        "session_cooccurrence": "30分セッション共起",
        "implicit_als": "implicit ALS",
    }
    for method, label in labels.items():
        metrics = result["methods"][method]
        lines.append(
            f"| {label} | {metrics['macro_strict_precision_at_5']:.1%} | "
            f"{metrics['macro_strict_precision_at_10']:.1%} | "
            f"{metrics['macro_ndcg_at_10']:.1%} |"
        )
    lines.extend(["", "## 現在方式との差（アーティスト単位bootstrap）", ""])
    for method in ("session_cooccurrence", "implicit_als"):
        comparison = result["paired_bootstrap"]["comparisons"][method]
        lines.append(
            f"- {labels[method]}: NDCG差 {comparison['mean_ndcg_delta']:+.2%} "
            f"（95% CI {comparison['ci_95_low']:+.2%}〜{comparison['ci_95_high']:+.2%}）"
        )
    lines.extend(
        [
            "",
            "## 解釈上の制限",
            "",
            "今回はcosine方式が出した100候補だけを再順位付けしています。P@10は候補集合が同じなので方式間で変わりません。NDCG@10とP@5が順位の差を表します。完全な推薦器比較には、3方式のTop 10候補の和集合を追加採点する必要があります。",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(
    evaluation: Path,
    coverage: Path,
    work_db: Path,
    spark_sources: list[Path],
    report_dir: Path,
    als_config: ALSConfig,
    session_config: SessionConfig,
    listen_cap: int,
) -> dict[str, Any]:
    rows = load_pairs(evaluation, coverage)
    keys = pair_keys(rows)
    session_scores, session_stats = compute_session_scores(spark_sources, keys, session_config)
    als_scores, als_stats = compute_als_scores(work_db, keys, als_config, listen_cap)

    for row in rows:
        key = (str(row["seed_artist_mbid"]), str(row["candidate_artist_mbid"]))
        session = session_scores[key]
        row["session_cooccurrence_score"] = round(float(session["score"]), 8)
        row["session_common_count"] = int(session["common_sessions"])
        row["als_score"] = round(als_scores[key], 8)
    add_method_ranks(rows, "cosine_shrinkage_score", "cosine_shrinkage_rank")
    add_method_ranks(rows, "session_cooccurrence_score", "session_cooccurrence_rank")
    add_method_ranks(rows, "als_score", "als_rank")
    rows.sort(key=lambda row: (str(row["seed_artist_name"]).casefold(), row["original_rank"]))

    methods = {
        "cosine_shrinkage": method_metrics(rows, "cosine_shrinkage_rank"),
        "session_cooccurrence": method_metrics(rows, "session_cooccurrence_rank"),
        "implicit_als": method_metrics(rows, "als_rank"),
    }
    result = {
        "comparison_scope": "rerank_same_100_human_rated_candidates",
        "candidate_count": len(rows),
        "methods": methods,
        "paired_bootstrap": bootstrap_ndcg_deltas(methods),
        "session": {
            **session_stats,
            "gap_seconds": session_config.gap_seconds,
            "shrinkage": session_config.shrinkage,
        },
        "als": {
            **als_stats,
            "factors": als_config.factors,
            "regularization": als_config.regularization,
            "iterations": als_config.iterations,
            "alpha": als_config.alpha,
            "random_state": als_config.random_state,
            "listen_cap": listen_cap,
        },
        "limitation": (
            "All methods rerank the same cosine-retrieved 100 candidates. "
            "Evaluate the union of each method's retrieved Top 10 for a full retrieval comparison."
        ),
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    write_csv(report_dir / "kpop_algorithm_comparison.csv", rows)
    (report_dir / "kpop_algorithm_comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_comparison_markdown(report_dir / "kpop_algorithm_comparison.md", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare three rankings on human-rated candidates")
    parser.add_argument("--evaluation", type=Path, default=DEFAULT_EVALUATION)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--work-db", type=Path, default=DEFAULT_WORK_DB)
    parser.add_argument("--spark-archive", type=Path, nargs="+", required=True)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
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
        args.evaluation,
        args.coverage,
        args.work_db,
        args.spark_archive,
        args.report_dir,
        ALSConfig(
            factors=args.als_factors,
            regularization=args.als_regularization,
            iterations=args.als_iterations,
            alpha=args.als_alpha,
        ),
        SessionConfig(
            gap_seconds=args.session_gap_minutes * 60,
            shrinkage=args.session_shrinkage,
        ),
        args.listen_cap,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

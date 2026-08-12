from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src.evaluation import read_csv
from src.feasibility import write_csv


DEFAULT_EVALUATION = Path("reports/cross_genre/recommendation_evaluation.csv")
DEFAULT_VALIDATION = Path("data/validation_artists.csv")
DEFAULT_SIMILARITY_SUMMARY = Path("reports/cross_genre/similarity_summary.json")
DEFAULT_REPORT_DIR = Path("reports/cross_genre")
DEFAULT_CATEGORY = "cross_genre_validation"


def build_blind_rows(
    evaluation_rows: list[dict[str, Any]], random_state: int = 42, id_prefix: str = "CROSS"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evaluation_rows:
        grouped[str(row.get("seed_artist_name", ""))].append(dict(row))

    rng = random.Random(random_state)
    blind_rows: list[dict[str, Any]] = []
    key_rows: list[dict[str, Any]] = []
    counter = 1
    for seed_name, rows in sorted(grouped.items(), key=lambda item: item[0].casefold()):
        rows.sort(key=lambda row: int(row.get("rank") or 0))
        rng.shuffle(rows)
        for row in rows:
            blind_id = f"{id_prefix}-{counter:03d}"
            blind_rows.append(
                {
                    "blind_candidate_id": blind_id,
                    "seed_artist_name": seed_name,
                    "primary_genre": row.get("primary_genre", ""),
                    "candidate_artist_name": row.get("candidate_artist_name", ""),
                    "candidate_artist_mbid": row.get("candidate_artist_mbid", ""),
                    "human_rating_0_1_2": "",
                    "human_notes": "",
                }
            )
            key_rows.append(
                {
                    "blind_candidate_id": blind_id,
                    "seed_artist_name": seed_name,
                    "primary_genre": row.get("primary_genre", ""),
                    "rank": int(row.get("rank") or 0),
                    "candidate_artist_name": row.get("candidate_artist_name", ""),
                    "candidate_artist_mbid": row.get("candidate_artist_mbid", ""),
                    "similarity_score": float(row.get("similarity_score") or 0.0),
                    "common_listener_count": int(row.get("common_listener_count") or 0),
                    "confidence": row.get("confidence", ""),
                }
            )
            counter += 1
    return blind_rows, key_rows


def build_summary(
    blind_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    similarity_summary: dict[str, Any],
    category: str,
    random_state: int,
) -> dict[str, Any]:
    selected = [row for row in validation_rows if row.get("category") == category]
    genre_counts = Counter(str(row.get("primary_genre", "")) for row in selected)
    similarity = similarity_summary["similarity"]
    by_name = {row["artist_name"]: row for row in similarity["artists"]}
    per_artist = []
    for row in sorted(selected, key=lambda item: str(item["artist_name"]).casefold()):
        stats = by_name.get(str(row["artist_name"]), {})
        per_artist.append(
            {
                "artist_name": row["artist_name"],
                "primary_genre": row.get("primary_genre", ""),
                "listener_count_in_window": stats.get("listener_count_in_window", 0),
                "recommendation_count": stats.get("recommendation_count", 0),
                "expected_hit": stats.get("expected_hit", False),
            }
        )
    return {
        "scope": category,
        "artist_count": len(selected),
        "candidate_count": len(blind_rows),
        "genre_counts": dict(sorted(genre_counts.items())),
        "artists_with_listeners": similarity["target_artists_with_listeners"],
        "artists_with_recommendations": similarity["target_artists_with_recommendations"],
        "target_listener_union": similarity["target_listener_union"],
        "expected_top10_hit_rate": similarity["expected_top10_hit_rate_all_targets"],
        "blind_random_state": random_state,
        "rating_instruction": "2=かなり納得, 1=意外だがあり, 0=違う",
        "per_artist": per_artist,
    }


def run(
    evaluation: Path,
    validation: Path,
    similarity_summary_path: Path,
    report_dir: Path,
    category: str = DEFAULT_CATEGORY,
    random_state: int = 42,
) -> dict[str, Any]:
    blind_rows, key_rows = build_blind_rows(read_csv(evaluation), random_state)
    summary = build_summary(
        blind_rows,
        read_csv(validation),
        json.loads(similarity_summary_path.read_text(encoding="utf-8")),
        category,
        random_state,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    write_csv(report_dir / "blind_evaluation.csv", blind_rows)
    write_csv(report_dir / "evaluation_key.csv", key_rows)
    (report_dir / "evaluation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a blind cross-genre rating sheet")
    parser.add_argument("--evaluation", type=Path, default=DEFAULT_EVALUATION)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument(
        "--similarity-summary", type=Path, default=DEFAULT_SIMILARITY_SUMMARY
    )
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--category", default=DEFAULT_CATEGORY)
    parser.add_argument("--random-state", type=int, default=42)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = run(
        args.evaluation,
        args.validation,
        args.similarity_summary,
        args.report_dir,
        args.category,
        args.random_state,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

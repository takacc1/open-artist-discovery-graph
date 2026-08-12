from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.cross_genre_evaluation import build_blind_rows
from src.evaluation import read_csv
from src.feasibility import write_csv


DEFAULT_EVALUATION = Path("reports/youth_popular/recommendation_evaluation.csv")
DEFAULT_VALIDATION = Path("data/validation_artists.csv")
DEFAULT_SIMILARITY_SUMMARY = Path("reports/youth_popular/similarity_summary.json")
DEFAULT_REPORT_DIR = Path("reports/youth_popular")
DEFAULT_CATEGORY = "youth_popular_validation"


def listener_tier(listener_count: int) -> str:
    if listener_count < 30:
        return "low_under_30"
    if listener_count < 100:
        return "limited_30_to_99"
    return "sufficient_100_plus"


def build_summary(
    blind_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    similarity_summary: dict[str, Any],
    category: str,
    random_state: int,
) -> dict[str, Any]:
    selected = [row for row in validation_rows if row.get("category") == category]
    similarity = similarity_summary["similarity"]
    by_name = {row["artist_name"]: row for row in similarity["artists"]}
    per_artist = []
    for row in selected:
        stats = by_name.get(str(row["artist_name"]), {})
        listener_count = int(stats.get("listener_count_in_window", 0))
        per_artist.append(
            {
                "artist_name": row["artist_name"],
                "primary_genre": row.get("primary_genre", ""),
                "listener_count_in_window": listener_count,
                "data_tier": listener_tier(listener_count),
                "recommendation_count": int(stats.get("recommendation_count", 0)),
                "expected_hit": bool(stats.get("expected_hit", False)),
            }
        )
    tier_counts = Counter(row["data_tier"] for row in per_artist)
    return {
        "scope": category,
        "artist_count": len(selected),
        "candidate_count": len(blind_rows),
        "genre_counts": dict(sorted(Counter(row.get("primary_genre", "") for row in selected).items())),
        "data_tier_counts": dict(sorted(tier_counts.items())),
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
    blind_rows, key_rows = build_blind_rows(
        read_csv(evaluation), random_state, id_prefix="YOUTH"
    )
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
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a blind youth-popular rating sheet")
    parser.add_argument("--evaluation", type=Path, default=DEFAULT_EVALUATION)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--similarity-summary", type=Path, default=DEFAULT_SIMILARITY_SUMMARY)
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

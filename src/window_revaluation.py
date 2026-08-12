from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

from src.evaluation import read_csv
from src.feasibility import write_csv


DEFAULT_CATEGORY = "youth_popular_validation"


def build_revaluation(
    similarity_rows: list[dict[str, str]],
    validation_rows: list[dict[str, str]],
    previous_ratings: list[dict[str, str]],
    similarity_summary: dict[str, Any],
    *,
    category: str = DEFAULT_CATEGORY,
    limit: int = 10,
    random_state: int = 42,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    selected = {
        row["artist_name"]: row
        for row in validation_rows
        if row.get("category", "").strip() == category
    }
    previous = {
        (row.get("seed_artist_name", ""), row.get("candidate_artist_mbid", "").lower()): row
        for row in previous_ratings
        if row.get("human_rating_0_1_2", "").strip() in {"0", "1", "2"}
    }
    current = [
        row
        for row in similarity_rows
        if row.get("seed_artist_name", "") in selected
        and int(row.get("rank") or 0) <= limit
    ]
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in current:
        grouped.setdefault(row["seed_artist_name"], []).append(row)
    missing = [name for name in selected if len(grouped.get(name, [])) != limit]
    if missing:
        raise ValueError(f"Expected {limit} candidates for every artist; invalid={missing}")

    rng = random.Random(random_state)
    visible: list[dict[str, Any]] = []
    key_rows: list[dict[str, Any]] = []
    per_artist = []
    counter = 1
    for seed_name in sorted(selected, key=str.casefold):
        rows = sorted(grouped[seed_name], key=lambda row: int(row["rank"]))
        carried_count = sum(
            (seed_name, row["candidate_artist_mbid"].lower()) in previous for row in rows
        )
        per_artist.append(
            {
                "artist_name": seed_name,
                "primary_genre": selected[seed_name].get("primary_genre", ""),
                "carried_rating_count": carried_count,
                "new_rating_count": limit - carried_count,
            }
        )
        shuffled = list(rows)
        rng.shuffle(shuffled)
        for row in shuffled:
            blind_id = f"Y30-{counter:03d}"
            prior = previous.get((seed_name, row["candidate_artist_mbid"].lower()))
            visible.append(
                {
                    "blind_candidate_id": blind_id,
                    "seed_artist_name": seed_name,
                    "primary_genre": selected[seed_name].get("primary_genre", ""),
                    "candidate_artist_name": row["candidate_artist_name"],
                    "candidate_artist_mbid": row["candidate_artist_mbid"],
                    "human_rating_0_1_2": prior.get("human_rating_0_1_2", "") if prior else "",
                    "human_notes": prior.get("human_notes", "") if prior else "",
                    "needs_rating": "no" if prior else "yes",
                }
            )
            key_rows.append(
                {
                    "blind_candidate_id": blind_id,
                    "seed_artist_name": seed_name,
                    "primary_genre": selected[seed_name].get("primary_genre", ""),
                    "rank": int(row["rank"]),
                    "candidate_artist_name": row["candidate_artist_name"],
                    "candidate_artist_mbid": row["candidate_artist_mbid"],
                    "similarity_score": float(row["similarity_score"]),
                    "common_listener_count": int(row["common_listener_count"]),
                    "confidence": row["confidence"],
                }
            )
            counter += 1

    stats_by_name = {
        row["artist_name"]: row for row in similarity_summary["similarity"]["artists"]
    }
    for row in per_artist:
        stats = stats_by_name[row["artist_name"]]
        row["listener_count_30d"] = int(stats["listener_count_in_window"])
        row["data_tier_30d"] = (
            "low_under_30"
            if row["listener_count_30d"] < 30
            else "limited_30_to_99"
            if row["listener_count_30d"] < 100
            else "sufficient_100_plus"
        )

    carried = sum(row["carried_rating_count"] for row in per_artist)
    summary = {
        "scope": "youth_popular_30d_top10_revaluation",
        "artist_count": len(selected),
        "candidate_count": len(visible),
        "carried_rating_count": carried,
        "new_rating_count": len(visible) - carried,
        "carried_rating_rate": carried / len(visible) if visible else 0.0,
        "artists_with_30d_listeners": sum(
            int(stats_by_name[name]["listener_count_in_window"]) > 0 for name in selected
        ),
        "artists_with_30d_recommendations": sum(
            int(stats_by_name[name]["recommendation_count"]) > 0 for name in selected
        ),
        "low_under_30_count": sum(
            row["data_tier_30d"] == "low_under_30" for row in per_artist
        ),
        "random_state": random_state,
        "rating_instruction": "needs_rating=yesのみ、2=かなり納得、1=意外だがあり、0=違う",
        "per_artist": per_artist,
    }
    return visible, key_rows, summary


def run(
    similarity: Path,
    validation: Path,
    previous_ratings: Path,
    similarity_summary: Path,
    report_dir: Path,
    *,
    category: str = DEFAULT_CATEGORY,
    limit: int = 10,
    random_state: int = 42,
) -> dict[str, Any]:
    visible, key_rows, summary = build_revaluation(
        read_csv(similarity),
        read_csv(validation),
        read_csv(previous_ratings),
        json.loads(similarity_summary.read_text(encoding="utf-8")),
        category=category,
        limit=limit,
        random_state=random_state,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    write_csv(report_dir / "blind_revaluation.csv", visible)
    write_csv(report_dir / "revaluation_key.csv", key_rows)
    (report_dir / "revaluation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Carry prior ratings into a blind evaluation of a new window"
    )
    parser.add_argument("--similarity", type=Path, required=True)
    parser.add_argument("--validation", type=Path, default=Path("data/validation_artists.csv"))
    parser.add_argument("--previous-ratings", type=Path, required=True)
    parser.add_argument("--similarity-summary", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--category", default=DEFAULT_CATEGORY)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--random-state", type=int, default=42)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = run(
        args.similarity,
        args.validation,
        args.previous_ratings,
        args.similarity_summary,
        args.report_dir,
        category=args.category,
        limit=args.limit,
        random_state=args.random_state,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from src.feasibility import write_csv


DEFAULT_SIMILARITY = Path("reports/similarity_top10.csv")
DEFAULT_VALIDATION = Path("data/validation_artists.csv")
DEFAULT_OUTPUT = Path("reports/kpop_recommendation_evaluation.csv")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_evaluation_rows(
    similarity_rows: list[dict[str, str]],
    validation_rows: list[dict[str, str]],
    category: str,
) -> list[dict[str, Any]]:
    targets = {
        row["artist_name"]: row
        for row in validation_rows
        if row.get("category", "").strip() == category
    }
    result: list[dict[str, Any]] = []
    for row in similarity_rows:
        seed_name = row.get("seed_artist_name", "")
        if seed_name not in targets:
            continue
        result.append(
            {
                "seed_artist_name": seed_name,
                "primary_genre": targets[seed_name].get("primary_genre", "") or "",
                "rank": row.get("rank", ""),
                "candidate_artist_name": row.get("candidate_artist_name", ""),
                "candidate_artist_mbid": row.get("candidate_artist_mbid", ""),
                "similarity_score": row.get("similarity_score", ""),
                "common_listener_count": row.get("common_listener_count", ""),
                "confidence": row.get("confidence", ""),
                "human_rating_0_1_2": "",
                "human_notes": "",
            }
        )
    result.sort(key=lambda row: (str(row["seed_artist_name"]).casefold(), int(row["rank"])))
    return result


def run(similarity: Path, validation: Path, output: Path, category: str) -> list[dict[str, Any]]:
    rows = build_evaluation_rows(read_csv(similarity), read_csv(validation), category)
    write_csv(output, rows)
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a human-rating sheet from recommendations")
    parser.add_argument("--similarity", type=Path, default=DEFAULT_SIMILARITY)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--category", default="kpop_girl_group")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    rows = run(args.similarity, args.validation, args.output, args.category)
    print(f"Wrote {len(rows)} evaluation rows to {args.output}")


if __name__ == "__main__":
    main()

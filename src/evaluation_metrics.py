from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from src.evaluation import DEFAULT_OUTPUT as DEFAULT_EVALUATION
from src.evaluation import read_csv
from src.feasibility import write_csv


DEFAULT_SUMMARY_JSON = Path("reports/kpop_evaluation_summary.json")
DEFAULT_SUMMARY_MD = Path("reports/kpop_evaluation_summary.md")
DEFAULT_BY_ARTIST = Path("reports/kpop_evaluation_by_artist.csv")
VALID_RATINGS = {0, 1, 2}


def parse_rating(value: Any) -> int | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    try:
        rating = int(text)
    except ValueError as error:
        raise ValueError(f"Human rating must be 0, 1, or 2; got {value!r}") from error
    if rating not in VALID_RATINGS:
        raise ValueError(f"Human rating must be 0, 1, or 2; got {value!r}")
    return rating


def dcg(ratings: Iterable[int]) -> float:
    return sum((2**rating - 1) / math.log2(rank + 1) for rank, rating in enumerate(ratings, 1))


def ndcg(ratings: list[int]) -> float | None:
    ideal = dcg(sorted(ratings, reverse=True))
    return dcg(ratings) / ideal if ideal else None


def summarize_ranked_rows(
    rows: list[dict[str, Any]],
    *,
    rank_field: str = "rank",
    seed_field: str = "seed_artist_name",
    rating_field: str = "human_rating_0_1_2",
    top_k: int = 10,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    blank_count = 0
    ratings_all: list[int] = []
    for row in rows:
        rating = parse_rating(row.get(rating_field))
        if rating is None:
            blank_count += 1
            continue
        ranked = dict(row)
        ranked["_rating"] = rating
        grouped[str(row.get(seed_field, ""))].append(ranked)
        ratings_all.append(rating)

    by_artist: list[dict[str, Any]] = []
    for seed, items in sorted(grouped.items(), key=lambda item: item[0].casefold()):
        items.sort(key=lambda row: int(row.get(rank_field) or 0))
        selected = items[:top_k]
        ratings = [int(row["_rating"]) for row in selected]
        distribution = Counter(ratings)
        top_five = ratings[: min(5, len(ratings))]
        by_artist.append(
            {
                "seed_artist_name": seed,
                "rated_count": len(ratings),
                "rating_2_count": distribution[2],
                "rating_1_count": distribution[1],
                "rating_0_count": distribution[0],
                "relaxed_precision_at_10": (
                    sum(rating >= 1 for rating in ratings) / top_k if ratings else None
                ),
                "strict_precision_at_10": (
                    sum(rating == 2 for rating in ratings) / top_k if ratings else None
                ),
                "strict_precision_at_5": (
                    sum(rating == 2 for rating in top_five) / len(top_five)
                    if top_five
                    else None
                ),
                "mean_rating": sum(ratings) / len(ratings) if ratings else None,
                "ndcg_at_10": ndcg(ratings),
            }
        )

    def macro_average(field: str) -> float | None:
        values = [float(row[field]) for row in by_artist if row[field] is not None]
        return sum(values) / len(values) if values else None

    distribution = Counter(ratings_all)
    return {
        "row_count": len(rows),
        "rated_count": len(ratings_all),
        "blank_count": blank_count,
        "artist_count": len(by_artist),
        "rating_distribution": {
            "2": distribution[2],
            "1": distribution[1],
            "0": distribution[0],
        },
        "mean_rating": sum(ratings_all) / len(ratings_all) if ratings_all else None,
        "macro_relaxed_precision_at_10": macro_average("relaxed_precision_at_10"),
        "macro_strict_precision_at_10": macro_average("strict_precision_at_10"),
        "macro_strict_precision_at_5": macro_average("strict_precision_at_5"),
        "macro_ndcg_at_10": macro_average("ndcg_at_10"),
        "by_artist": by_artist,
    }


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    distribution = summary["rating_distribution"]
    lines = [
        "# K-POP推薦の人手評価",
        "",
        f"- 採点済み: {summary['rated_count']}/{summary['row_count']}",
        f"- 2（かなり納得）: {distribution['2']}",
        f"- 1（意外だがあり）: {distribution['1']}",
        f"- 0（違う）: {distribution['0']}",
        f"- 平均評価: {summary['mean_rating']:.3f}/2",
        f"- 緩いPrecision@10（1以上）: {summary['macro_relaxed_precision_at_10']:.1%}",
        f"- 厳しいPrecision@10（2のみ）: {summary['macro_strict_precision_at_10']:.1%}",
        f"- 厳しいPrecision@5（2のみ）: {summary['macro_strict_precision_at_5']:.1%}",
        f"- NDCG@10: {summary['macro_ndcg_at_10']:.1%}",
        "",
        "NDCGは評価2を評価1より強く扱い、高評価候補が上位にあるほど高くなります。",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(
    evaluation: Path,
    summary_json: Path,
    summary_md: Path,
    by_artist_csv: Path,
) -> dict[str, Any]:
    summary = summarize_ranked_rows(read_csv(evaluation))
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_markdown(summary_md, summary)
    write_csv(by_artist_csv, summary["by_artist"])
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize 0/1/2 human recommendation ratings")
    parser.add_argument("--evaluation", type=Path, default=DEFAULT_EVALUATION)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--summary-md", type=Path, default=DEFAULT_SUMMARY_MD)
    parser.add_argument("--by-artist-csv", type=Path, default=DEFAULT_BY_ARTIST)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = run(args.evaluation, args.summary_json, args.summary_md, args.by_artist_csv)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

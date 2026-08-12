from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src.evaluation import read_csv
from src.evaluation_metrics import parse_rating, summarize_ranked_rows
from src.feasibility import write_csv


DEFAULT_RATINGS = Path("data/metadata_fallback_expanded_human_ratings.csv")
DEFAULT_REPORT_DIR = Path("reports/metadata_fallback_expanded")
EXPECTED_ARTIST_COUNT = 8
EXPECTED_RANKS = list(range(1, 11))


def validate_rows(rows: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        seed = str(row.get("seed_artist_name", "")).strip()
        candidate = str(row.get("candidate_artist_name", "")).strip()
        rating = parse_rating(row.get("human_rating_0_1_2"))
        notes = str(row.get("human_notes", "")).strip()
        if not seed or not candidate or rating is None or not notes:
            raise ValueError(f"Incomplete metadata fallback rating row: {row!r}")
        grouped[seed].append(int(row["rank"]))

    invalid = {
        seed: sorted(ranks)
        for seed, ranks in grouped.items()
        if sorted(ranks) != EXPECTED_RANKS
    }
    expected_rows = EXPECTED_ARTIST_COUNT * len(EXPECTED_RANKS)
    if len(rows) != expected_rows or len(grouped) != EXPECTED_ARTIST_COUNT or invalid:
        raise ValueError(
            f"Expected {EXPECTED_ARTIST_COUNT} artists with ranks 1-10 "
            f"({expected_rows} rows); got {len(grouped)} artists, {len(rows)} rows, "
            f"invalid={invalid}"
        )


def summarize_sources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        rating = parse_rating(row.get("human_rating_0_1_2"))
        assert rating is not None
        grouped[str(row.get("recommendation_source", "")).strip()].append(rating)

    result = []
    for source, ratings in sorted(grouped.items(), key=lambda item: item[0].casefold()):
        distribution = Counter(ratings)
        count = len(ratings)
        result.append(
            {
                "recommendation_source": source,
                "rated_count": count,
                "rating_2_count": distribution[2],
                "rating_1_count": distribution[1],
                "rating_0_count": distribution[0],
                "relaxed_precision": sum(rating >= 1 for rating in ratings) / count,
                "strict_precision": distribution[2] / count,
                "rating_0_rate": distribution[0] / count,
                "mean_rating": sum(ratings) / count,
            }
        )
    return result


def build_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    validate_rows(rows)
    summary = summarize_ranked_rows(rows)
    distribution = summary["rating_distribution"]
    rating_0_rate = distribution["0"] / summary["rated_count"]
    recommendation_coverage = summary["artist_count"] / EXPECTED_ARTIST_COUNT
    criteria = [
        {
            "criterion": "recommendation_coverage",
            "target": 0.70,
            "actual": recommendation_coverage,
            "passed": recommendation_coverage >= 0.70,
        },
        {
            "criterion": "relaxed_precision_at_10",
            "target": 0.80,
            "actual": summary["macro_relaxed_precision_at_10"],
            "passed": summary["macro_relaxed_precision_at_10"] >= 0.80,
        },
        {
            "criterion": "strict_precision_at_5",
            "target": 0.40,
            "actual": summary["macro_strict_precision_at_5"],
            "passed": summary["macro_strict_precision_at_5"] >= 0.40,
        },
        {
            "criterion": "rating_0_rate_below",
            "target": 0.10,
            "actual": rating_0_rate,
            "passed": rating_0_rate < 0.10,
        },
    ]
    by_artist = [
        {
            **row,
            "rating_0_rate": row["rating_0_count"] / row["rated_count"],
        }
        for row in summary.pop("by_artist")
    ]
    return {
        "scope": "expanded_metadata_fallback_top10",
        "decision": "PASS" if all(row["passed"] for row in criteria) else "REVIEW",
        "criteria": criteria,
        "overall": {**summary, "rating_0_rate": rating_0_rate},
        "by_artist": by_artist,
        "by_source": summarize_sources(rows),
        "limitations": [
            "One human rater evaluated eight low-data seed artists.",
            "Known artists were excluded to test discovery quality, so these scores are not directly comparable with the earlier unfiltered evaluation.",
            "The 30-day sparse listener estimates remain unstable for artists below 30 listeners.",
        ],
    }


def write_markdown(path: Path, result: dict[str, Any]) -> None:
    overall = result["overall"]
    distribution = overall["rating_distribution"]
    lines = [
        "# 拡張メタデータ補助の人手評価",
        "",
        f"判定: **{result['decision']}**",
        "",
        f"- 採点済み: {overall['rated_count']}/{overall['row_count']}",
        f"- 評価2: {distribution['2']}、評価1: {distribution['1']}、評価0: {distribution['0']}",
        f"- 緩いP@10: {overall['macro_relaxed_precision_at_10']:.1%}",
        f"- 厳しいP@10: {overall['macro_strict_precision_at_10']:.1%}",
        f"- 厳しいP@5: {overall['macro_strict_precision_at_5']:.1%}",
        f"- NDCG@10: {overall['macro_ndcg_at_10']:.1%}",
        f"- 評価0率: {overall['rating_0_rate']:.1%}",
        f"- 平均評価: {overall['mean_rating']:.3f}/2",
        "",
        "緩いP@10と厳しいP@5は合格しましたが、評価0率が10%未満の基準を超えたためREVIEWです。",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(ratings_path: Path, report_dir: Path) -> dict[str, Any]:
    rows = read_csv(ratings_path)
    result = build_result(rows)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(report_dir / "metrics.md", result)
    write_csv(report_dir / "by_artist.csv", result["by_artist"])
    write_csv(report_dir / "by_source.csv", result["by_source"])
    write_csv(
        report_dir / "scored_top10.csv",
        sorted(rows, key=lambda row: (str(row["seed_artist_name"]).casefold(), int(row["rank"]))),
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score expanded sparse metadata fallback ratings")
    parser.add_argument("--ratings", type=Path, default=DEFAULT_RATINGS)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    print(json.dumps(run(args.ratings, args.report_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

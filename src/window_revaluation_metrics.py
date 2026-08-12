from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.cross_genre_metrics import aggregate_groups, data_tier, join_ratings, load_completed_ratings
from src.evaluation import read_csv
from src.evaluation_metrics import summarize_ranked_rows
from src.feasibility import write_csv


DEFAULT_COMPLETED_RATINGS = Path("data/youth_popular_30d_human_ratings.csv")
DEFAULT_KEY = Path("reports/30d/youth_popular/revaluation_key.csv")
DEFAULT_REVALUATION_SUMMARY = Path("reports/30d/youth_popular/revaluation_summary.json")
DEFAULT_REPORT_DIR = Path("reports/30d/youth_popular")
DEFAULT_PREVIOUS_METRICS = Path("reports/youth_popular/metrics.json")


def validate_ranked_rows(rows: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[int]] = {}
    for row in rows:
        grouped.setdefault(str(row["seed_artist_name"]), []).append(int(row["rank"]))
    invalid = {
        seed: sorted(ranks)
        for seed, ranks in grouped.items()
        if sorted(ranks) != list(range(1, 11))
    }
    if len(rows) != 260 or len(grouped) != 26 or invalid:
        raise ValueError(
            f"Expected 26 artists with ranks 1-10 (260 rows); got {len(grouped)} artists, "
            f"{len(rows)} rows, invalid={invalid}"
        )


def market_segment(primary_genre: str) -> str:
    return "kpop" if primary_genre == "kpop" else "japan"


def build_result(
    key_rows: list[dict[str, Any]],
    completed_rows: list[dict[str, Any]],
    revaluation_summary: dict[str, Any],
    previous_metrics: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    scored_rows = join_ratings(key_rows, completed_rows)
    validate_ranked_rows(scored_rows)
    summary = summarize_ranked_rows(scored_rows)

    metadata = {
        str(row["artist_name"]): row for row in revaluation_summary["per_artist"]
    }
    by_artist = []
    for row in summary.pop("by_artist"):
        source = metadata[str(row["seed_artist_name"])]
        listeners = int(source["listener_count_30d"])
        genre = str(source["primary_genre"])
        by_artist.append(
            {
                **row,
                "primary_genre": genre,
                "market_segment": market_segment(genre),
                "listener_count_in_window": listeners,
                "data_tier": data_tier(listeners),
                "rating_0_rate": row["rating_0_count"] / row["rated_count"],
            }
        )
    by_artist.sort(key=lambda row: str(row["seed_artist_name"]).casefold())

    distribution = summary["rating_distribution"]
    zero_rate = distribution["0"] / summary["rated_count"]
    recommendation_coverage = (
        revaluation_summary["artists_with_30d_recommendations"]
        / revaluation_summary["artist_count"]
    )
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
            "actual": zero_rate,
            "passed": zero_rate < 0.10,
        },
    ]
    result: dict[str, Any] = {
        "scope": "youth_popular_cosine_shrinkage_30d_top10",
        "decision": "PASS" if all(row["passed"] for row in criteria) else "REVIEW",
        "criteria": criteria,
        "coverage": {
            "requested_artist_count": revaluation_summary["artist_count"],
            "artists_with_30day_listeners": revaluation_summary[
                "artists_with_30d_listeners"
            ],
            "artists_with_recommendations": revaluation_summary[
                "artists_with_30d_recommendations"
            ],
            "recommendation_coverage": recommendation_coverage,
            "low_under_30_count": revaluation_summary["low_under_30_count"],
        },
        "rating_provenance": {
            "carried_rating_count": revaluation_summary["carried_rating_count"],
            "new_rating_count": revaluation_summary["new_rating_count"],
        },
        "overall": {**summary, "rating_0_rate": zero_rate},
        "by_artist": by_artist,
        "by_market": aggregate_groups(by_artist, "market_segment"),
        "by_genre": aggregate_groups(by_artist, "primary_genre"),
        "by_data_tier": aggregate_groups(by_artist, "data_tier"),
        "limitations": [
            "One human rater evaluated 26 seed artists using a 30-day ListenBrainz window.",
            "Eight seed artists still had fewer than 30 listeners and require low-confidence handling.",
            "The 113 carried ratings reduce re-rating effort but do not provide an independent second judgment.",
        ],
    }
    if previous_metrics is not None:
        previous = previous_metrics["overall"]
        result["comparison_to_7day"] = {
            "recommendation_coverage_delta": recommendation_coverage
            - previous_metrics["coverage"]["recommendation_coverage"],
            "relaxed_precision_at_10_delta": summary["macro_relaxed_precision_at_10"]
            - previous["macro_relaxed_precision_at_10"],
            "strict_precision_at_10_delta": summary["macro_strict_precision_at_10"]
            - previous["macro_strict_precision_at_10"],
            "strict_precision_at_5_delta": summary["macro_strict_precision_at_5"]
            - previous["macro_strict_precision_at_5"],
            "ndcg_at_10_delta": summary["macro_ndcg_at_10"]
            - previous["macro_ndcg_at_10"],
            "rating_0_rate_delta": zero_rate - previous["rating_0_rate"],
        }
    scored_rows.sort(
        key=lambda row: (str(row["seed_artist_name"]).casefold(), int(row["rank"]))
    )
    return result, scored_rows


def write_markdown(path: Path, result: dict[str, Any]) -> None:
    overall = result["overall"]
    distribution = overall["rating_distribution"]
    coverage = result["coverage"]
    lines = [
        "# 若者向け人気アーティスト推薦の30日版人手評価",
        "",
        f"判定: **{result['decision']}**",
        "",
        f"- 推薦生成: {coverage['artists_with_recommendations']}/{coverage['requested_artist_count']}（{coverage['recommendation_coverage']:.1%}）",
        f"- 採点済み: {overall['rated_count']}/{overall['row_count']}",
        f"- 評価2: {distribution['2']}、評価1: {distribution['1']}、評価0: {distribution['0']}",
        f"- 緩いP@10: {overall['macro_relaxed_precision_at_10']:.1%}",
        f"- 厳しいP@10: {overall['macro_strict_precision_at_10']:.1%}",
        f"- 厳しいP@5: {overall['macro_strict_precision_at_5']:.1%}",
        f"- NDCG@10: {overall['macro_ndcg_at_10']:.1%}",
        f"- 評価0率: {overall['rating_0_rate']:.1%}",
        "",
        "30日でもリスナー30人未満の8組は、実サービスで低信頼表示とメタデータ補完を行います。",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(
    completed_ratings: Path,
    key_csv: Path,
    revaluation_summary_json: Path,
    report_dir: Path,
    *,
    previous_metrics_json: Path | None = DEFAULT_PREVIOUS_METRICS,
) -> dict[str, Any]:
    previous_metrics = None
    if previous_metrics_json is not None and previous_metrics_json.exists():
        previous_metrics = json.loads(previous_metrics_json.read_text(encoding="utf-8"))
    result, scored_rows = build_result(
        read_csv(key_csv),
        load_completed_ratings(completed_ratings),
        json.loads(revaluation_summary_json.read_text(encoding="utf-8")),
        previous_metrics,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(report_dir / "metrics.md", result)
    write_csv(report_dir / "scored_top10.csv", scored_rows)
    write_csv(report_dir / "by_artist.csv", result["by_artist"])
    write_csv(report_dir / "by_market.csv", result["by_market"])
    write_csv(report_dir / "by_genre.csv", result["by_genre"])
    write_csv(report_dir / "by_data_tier.csv", result["by_data_tier"])
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score the 30-day youth-popular revaluation")
    parser.add_argument("--completed-ratings", type=Path, default=DEFAULT_COMPLETED_RATINGS)
    parser.add_argument("--key-csv", type=Path, default=DEFAULT_KEY)
    parser.add_argument(
        "--revaluation-summary-json", type=Path, default=DEFAULT_REVALUATION_SUMMARY
    )
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--previous-metrics-json", type=Path, default=DEFAULT_PREVIOUS_METRICS)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run(
        args.completed_ratings,
        args.key_csv,
        args.revaluation_summary_json,
        args.report_dir,
        previous_metrics_json=args.previous_metrics_json,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

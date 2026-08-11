from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src.evaluation import read_csv
from src.evaluation_metrics import parse_rating, summarize_ranked_rows
from src.feasibility import write_csv


DEFAULT_COMPLETED_RATINGS = Path("data/cross_genre_human_ratings.csv")
DEFAULT_KEY = Path("reports/cross_genre/evaluation_key.csv")
DEFAULT_EVALUATION_SUMMARY = Path("reports/cross_genre/evaluation_summary.json")
DEFAULT_KPOP_METRICS = Path("reports/kpop_full_retrieval_metrics.json")
DEFAULT_REPORT_DIR = Path("reports/cross_genre")


def load_completed_ratings(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        return read_csv(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("records")
    if not isinstance(rows, list):
        raise ValueError("Completed ratings JSON must contain a records list")
    return rows


def join_ratings(
    key_rows: list[dict[str, Any]], completed_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    ratings: dict[str, dict[str, Any]] = {}
    for row in completed_rows:
        blind_id = str(row.get("blind_candidate_id", "")).strip()
        rating = parse_rating(row.get("human_rating_0_1_2"))
        if not blind_id or rating is None:
            raise ValueError(f"Missing blind candidate ID or rating: {row!r}")
        if blind_id in ratings:
            raise ValueError(f"Duplicate completed rating for {blind_id}")
        ratings[blind_id] = {**row, "human_rating_0_1_2": rating}

    joined: list[dict[str, Any]] = []
    missing: list[str] = []
    for key_row in key_rows:
        blind_id = str(key_row.get("blind_candidate_id", "")).strip()
        rating_row = ratings.get(blind_id)
        if rating_row is None:
            missing.append(blind_id)
            continue
        for field in ("seed_artist_name", "candidate_artist_mbid"):
            expected = str(key_row.get(field, "")).strip().casefold()
            actual = str(rating_row.get(field, "")).strip().casefold()
            if expected != actual:
                raise ValueError(
                    f"Identity mismatch for {blind_id}: {field} is {actual!r}, expected {expected!r}"
                )
        joined.append(
            {
                **key_row,
                "rank": int(key_row.get("rank") or 0),
                "similarity_score": float(key_row.get("similarity_score") or 0.0),
                "common_listener_count": int(key_row.get("common_listener_count") or 0),
                "human_rating_0_1_2": rating_row["human_rating_0_1_2"],
                "human_notes": rating_row.get("human_notes", ""),
            }
        )
    if missing:
        raise ValueError(f"Missing completed ratings for {len(missing)} candidates: {missing[:3]}")
    if len(ratings) != len(joined):
        extra = sorted(set(ratings) - {str(row["blind_candidate_id"]) for row in joined})
        raise ValueError(f"Completed ratings contain unknown candidates: {extra[:3]}")
    return joined


def validate_ranked_rows(rows: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        grouped[str(row["seed_artist_name"])].append(int(row["rank"]))
    invalid = {
        seed: sorted(ranks)
        for seed, ranks in grouped.items()
        if sorted(ranks) != list(range(1, 11))
    }
    if len(rows) != 150 or len(grouped) != 15 or invalid:
        raise ValueError(
            f"Expected 15 artists with ranks 1-10 (150 rows); got {len(grouped)} artists, "
            f"{len(rows)} rows, invalid={invalid}"
        )


def data_tier(listener_count: int) -> str:
    if listener_count < 30:
        return "low_under_30"
    if listener_count < 100:
        return "limited_30_to_99"
    return "sufficient_100_plus"


def aggregate_groups(
    by_artist: list[dict[str, Any]], group_field: str
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in by_artist:
        grouped[str(row[group_field])].append(row)

    output = []
    metric_fields = (
        "relaxed_precision_at_10",
        "strict_precision_at_10",
        "strict_precision_at_5",
        "mean_rating",
        "ndcg_at_10",
    )
    for group, rows in sorted(grouped.items()):
        combined = Counter()
        for row in rows:
            combined[2] += int(row["rating_2_count"])
            combined[1] += int(row["rating_1_count"])
            combined[0] += int(row["rating_0_count"])
        aggregate: dict[str, Any] = {
            group_field: group,
            "artist_count": len(rows),
            "candidate_count": sum(int(row["rated_count"]) for row in rows),
            "rating_2_count": combined[2],
            "rating_1_count": combined[1],
            "rating_0_count": combined[0],
            "rating_0_rate": combined[0] / sum(combined.values()),
        }
        for field in metric_fields:
            values = [float(row[field]) for row in rows if row[field] is not None]
            aggregate[field] = sum(values) / len(values) if values else None
        output.append(aggregate)
    return output


def build_result(
    key_rows: list[dict[str, Any]],
    completed_rows: list[dict[str, Any]],
    evaluation_summary: dict[str, Any],
    kpop_metrics: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    scored_rows = join_ratings(key_rows, completed_rows)
    validate_ranked_rows(scored_rows)
    summary = summarize_ranked_rows(scored_rows)

    artist_metadata = {
        str(row["artist_name"]): row for row in evaluation_summary["per_artist"]
    }
    by_artist = []
    for row in summary.pop("by_artist"):
        metadata = artist_metadata[str(row["seed_artist_name"])]
        listener_count = int(metadata["listener_count_in_window"])
        by_artist.append(
            {
                **row,
                "primary_genre": metadata["primary_genre"],
                "listener_count_in_window": listener_count,
                "data_tier": data_tier(listener_count),
                "rating_0_rate": row["rating_0_count"] / row["rated_count"],
            }
        )
    by_artist.sort(key=lambda row: str(row["seed_artist_name"]).casefold())
    by_genre = aggregate_groups(by_artist, "primary_genre")
    by_data_tier = aggregate_groups(by_artist, "data_tier")

    distribution = summary["rating_distribution"]
    zero_rate = distribution["0"] / summary["rated_count"]
    criteria = [
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
        "scope": "cross_genre_cosine_shrinkage_top10",
        "decision": "PASS" if all(row["passed"] for row in criteria) else "REVIEW",
        "criteria": criteria,
        "overall": {**summary, "rating_0_rate": zero_rate},
        "by_artist": by_artist,
        "by_genre": by_genre,
        "by_data_tier": by_data_tier,
        "data_tier_definition": {
            "low_under_30": "fewer than 30 listeners in the 7-day window",
            "limited_30_to_99": "30-99 listeners in the 7-day window",
            "sufficient_100_plus": "100 or more listeners in the 7-day window",
        },
        "limitations": [
            "One human rater evaluated 15 seed artists using a 7-day ListenBrainz window.",
            "Genre groups contain only 1-4 seed artists and should not be generalized broadly.",
            "The low-data group contains only Michel Camilo and STUTS.",
        ],
    }
    if kpop_metrics is not None:
        cosine = kpop_metrics["methods"]["cosine_shrinkage"]
        result["kpop_cosine_descriptive_comparison"] = {
            field: cosine[field]
            for field in (
                "mean_rating",
                "macro_relaxed_precision_at_10",
                "macro_strict_precision_at_10",
                "macro_strict_precision_at_5",
                "macro_ndcg_at_10",
            )
        }
    scored_rows.sort(key=lambda row: (str(row["seed_artist_name"]).casefold(), int(row["rank"])))
    return result, scored_rows, by_genre, by_data_tier


def write_markdown(path: Path, result: dict[str, Any]) -> None:
    overall = result["overall"]
    distribution = overall["rating_distribution"]
    lines = [
        "# クロスジャンル推薦の人手評価",
        "",
        f"判定: **{result['decision']}**",
        "",
        f"- 採点済み: {overall['rated_count']}/{overall['row_count']}",
        f"- 2（かなり納得）: {distribution['2']}",
        f"- 1（意外だがあり）: {distribution['1']}",
        f"- 0（違う）: {distribution['0']}（{overall['rating_0_rate']:.1%}）",
        f"- 平均評価: {overall['mean_rating']:.3f}/2",
        f"- 緩いP@10: {overall['macro_relaxed_precision_at_10']:.1%}",
        f"- 厳しいP@10: {overall['macro_strict_precision_at_10']:.1%}",
        f"- 厳しいP@5: {overall['macro_strict_precision_at_5']:.1%}",
        f"- NDCG@10: {overall['macro_ndcg_at_10']:.1%}",
        "",
        "## 注意",
        "",
        "ジャンル別は各1〜4組、低データ群は2組、採点者は1人です。今回の数値はMVPの成立確認であり、一般的な音楽推薦精度の保証ではありません。",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(
    completed_ratings: Path,
    key_csv: Path,
    evaluation_summary_json: Path,
    report_dir: Path,
    *,
    ratings_output: Path | None = None,
    kpop_metrics_json: Path | None = DEFAULT_KPOP_METRICS,
) -> dict[str, Any]:
    completed_rows = load_completed_ratings(completed_ratings)
    if ratings_output is not None:
        write_csv(ratings_output, completed_rows)
    kpop_metrics = None
    if kpop_metrics_json is not None and kpop_metrics_json.exists():
        kpop_metrics = json.loads(kpop_metrics_json.read_text(encoding="utf-8"))
    result, scored_rows, by_genre, by_data_tier = build_result(
        read_csv(key_csv),
        completed_rows,
        json.loads(evaluation_summary_json.read_text(encoding="utf-8")),
        kpop_metrics,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(report_dir / "metrics.md", result)
    write_csv(report_dir / "scored_top10.csv", scored_rows)
    write_csv(report_dir / "by_artist.csv", result["by_artist"])
    write_csv(report_dir / "by_genre.csv", by_genre)
    write_csv(report_dir / "by_data_tier.csv", by_data_tier)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score the blind cross-genre Top 10 evaluation")
    parser.add_argument("--completed-ratings", type=Path, default=DEFAULT_COMPLETED_RATINGS)
    parser.add_argument("--key-csv", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--evaluation-summary-json", type=Path, default=DEFAULT_EVALUATION_SUMMARY)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--ratings-output", type=Path)
    parser.add_argument("--kpop-metrics-json", type=Path, default=DEFAULT_KPOP_METRICS)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run(
        args.completed_ratings,
        args.key_csv,
        args.evaluation_summary_json,
        args.report_dir,
        ratings_output=args.ratings_output,
        kpop_metrics_json=args.kpop_metrics_json,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

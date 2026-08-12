from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src.evaluation import read_csv
from src.evaluation_metrics import parse_rating, summarize_ranked_rows
from src.feasibility import write_csv
from src.metadata_fallback_metrics import summarize_sources


DEFAULT_RATINGS = Path("data/metadata_evidence_v2_human_ratings.csv")
DEFAULT_REPORT_DIR = Path("reports/metadata_evidence_v2")
V3_EXPECTED_CANDIDATE_COUNTS = {
    "3House": 10,
    "NCT": 10,
    "おいしくるメロンパン": 10,
    "カネヨリマサル": 10,
    "クリープハイプ": 10,
    "マルシィ": 10,
    "ヤングスキニー": 6,
    "平井 大": 10,
}
V4_EXPECTED_CANDIDATE_COUNTS = {
    **V3_EXPECTED_CANDIDATE_COUNTS,
    "ヤングスキニー": 4,
}
EXPECTED_CANDIDATE_COUNTS = V3_EXPECTED_CANDIDATE_COUNTS
PROFILES = {
    "v3": {
        "ratings": DEFAULT_RATINGS,
        "report_dir": DEFAULT_REPORT_DIR,
        "expected_candidate_counts": V3_EXPECTED_CANDIDATE_COUNTS,
        "scope": "generic_evidence_rules_sparse_top10",
        "title": "証拠ベース共通ルールv3の人手評価",
        "previous_candidate_count": 80,
        "previous_rating_0_count": 14,
        "previous_rating_0_rate": 0.175,
        "safety_stop_note": "One seed returns six candidates rather than padding its Top 10 with weak evidence.",
    },
    "v4": {
        "ratings": Path("data/metadata_evidence_v4_human_ratings.csv"),
        "report_dir": Path("reports/metadata_evidence_v4"),
        "expected_candidate_counts": V4_EXPECTED_CANDIDATE_COUNTS,
        "scope": "specific_metadata_evidence_sparse_top10",
        "title": "具体的証拠を必須にしたv4の人手評価",
        "previous_candidate_count": 76,
        "previous_rating_0_count": 8,
        "previous_rating_0_rate": 8 / 76,
        "safety_stop_note": "One seed returns four candidates rather than padding its Top 10 with weak evidence.",
    },
}


def validate_rows(
    rows: list[dict[str, Any]],
    expected_candidate_counts: dict[str, int] | None = None,
) -> None:
    expected_candidate_counts = expected_candidate_counts or EXPECTED_CANDIDATE_COUNTS
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        seed = str(row.get("seed_artist_name", "")).strip()
        candidate = str(row.get("candidate_artist_name", "")).strip()
        rating = parse_rating(row.get("human_rating_0_1_2"))
        if not seed or not candidate or rating is None:
            raise ValueError(f"Incomplete evidence-v2 rating row: {row!r}")
        grouped[seed].append(int(row["rank"]))

    expected_seeds = set(expected_candidate_counts)
    if set(grouped) != expected_seeds:
        raise ValueError(
            f"Expected seeds {sorted(expected_seeds)!r}; got {sorted(grouped)!r}"
        )
    invalid = {
        seed: sorted(grouped[seed])
        for seed, count in expected_candidate_counts.items()
        if sorted(grouped[seed]) != list(range(1, count + 1))
    }
    expected_rows = sum(expected_candidate_counts.values())
    if len(rows) != expected_rows or invalid:
        raise ValueError(
            f"Expected {expected_rows} rows with configured rank ranges; "
            f"got {len(rows)}, invalid={invalid}"
        )


def build_result(rows: list[dict[str, Any]], *, profile: str = "v3") -> dict[str, Any]:
    config = PROFILES[profile]
    expected_candidate_counts = config["expected_candidate_counts"]
    validate_rows(rows, expected_candidate_counts)
    summary = summarize_ranked_rows(rows)
    distribution = summary["rating_distribution"]
    rated_count = summary["rated_count"]
    rating_0_rate = distribution["0"] / rated_count
    returned_relaxed_precision = (
        distribution["2"] + distribution["1"]
    ) / rated_count
    expected_slot_count = len(expected_candidate_counts) * 10
    recommendation_coverage = summary["artist_count"] / len(expected_candidate_counts)
    candidate_fill_rate = summary["row_count"] / expected_slot_count
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
    new_ratings = [
        parse_rating(row.get("human_rating_0_1_2"))
        for row in rows
        if str(row.get("rating_status", "")).strip() == "要入力"
    ]
    new_distribution = Counter(
        rating for rating in new_ratings if rating is not None
    )
    return {
        "profile": profile,
        "scope": config["scope"],
        "title": config["title"],
        "decision": "PASS" if all(row["passed"] for row in criteria) else "REVIEW",
        "criteria": criteria,
        "coverage": {
            "recommendation_coverage": recommendation_coverage,
            "candidate_fill_rate": candidate_fill_rate,
            "returned_candidate_count": summary["row_count"],
            "possible_top10_slot_count": expected_slot_count,
        },
        "overall": {
            **summary,
            "returned_relaxed_precision": returned_relaxed_precision,
            "rating_0_rate": rating_0_rate,
        },
        "new_rating_distribution": {
            "rated_count": len(new_ratings),
            "2": new_distribution[2],
            "1": new_distribution[1],
            "0": new_distribution[0],
        },
        "comparison_to_previous": {
            "previous_candidate_count": config["previous_candidate_count"],
            "previous_rating_0_count": config["previous_rating_0_count"],
            "previous_rating_0_rate": config["previous_rating_0_rate"],
            "current_candidate_count": rated_count,
            "current_rating_0_count": distribution["0"],
            "current_rating_0_rate": rating_0_rate,
        },
        "by_artist": by_artist,
        "by_source": summarize_sources(rows),
        "limitations": [
            "One human rater evaluated eight low-data seed artists.",
            config["safety_stop_note"],
            "The 30-day sparse listener estimates remain unstable for artists below 30 listeners.",
        ],
    }


def write_markdown(path: Path, result: dict[str, Any]) -> None:
    overall = result["overall"]
    distribution = overall["rating_distribution"]
    coverage = result["coverage"]
    comparison = result["comparison_to_previous"]
    lines = [
        f"# {result['title']}",
        "",
        f"判定: **{result['decision']}**",
        "",
        f"- 推薦生成率: {coverage['recommendation_coverage']:.1%}",
        f"- 候補充足率: {coverage['candidate_fill_rate']:.1%} "
        f"({coverage['returned_candidate_count']}/{coverage['possible_top10_slot_count']})",
        f"- 評価2: {distribution['2']}、評価1: {distribution['1']}、評価0: {distribution['0']}",
        f"- 固定枠の緩いP@10: {overall['macro_relaxed_precision_at_10']:.1%}",
        f"- 返した候補内の緩い精度: {overall['returned_relaxed_precision']:.1%}",
        f"- 厳しいP@5: {overall['macro_strict_precision_at_5']:.1%}",
        f"- NDCG@10: {overall['macro_ndcg_at_10']:.1%}",
        f"- 評価0率: {overall['rating_0_rate']:.1%}",
        "",
        f"評価0は前モデルの{comparison['previous_rating_0_count']}件から"
        f"{comparison['current_rating_0_count']}件へ減りました。"
        + (
            "4基準をすべて満たしたため、有効モデルへの切替候補です。"
            if result["decision"] == "PASS"
            else "10%未満の基準には届いていないためモデルは有効化しません。"
        ),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(ratings_path: Path, report_dir: Path, *, profile: str = "v3") -> dict[str, Any]:
    rows = read_csv(ratings_path)
    result = build_result(rows, profile=profile)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(report_dir / "metrics.md", result)
    write_csv(report_dir / "by_artist.csv", result["by_artist"])
    write_csv(report_dir / "by_source.csv", result["by_source"])
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score sparse recommendations after generic evidence rules"
    )
    parser.add_argument("--profile", choices=sorted(PROFILES), default="v3")
    parser.add_argument("--ratings", type=Path)
    parser.add_argument("--report-dir", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = PROFILES[args.profile]
    ratings = args.ratings or config["ratings"]
    report_dir = args.report_dir or config["report_dir"]
    print(
        json.dumps(
            run(ratings, report_dir, profile=args.profile),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

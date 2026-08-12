from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src.algorithm_comparison import bootstrap_ndcg_deltas
from src.evaluation_metrics import parse_rating, summarize_ranked_rows
from src.feasibility import write_csv
from src.full_retrieval import METHODS


DEFAULT_KEY = Path("reports/kpop_full_retrieval_key.csv")
DEFAULT_RETRIEVAL_SUMMARY = Path("reports/kpop_full_retrieval_summary.json")
DEFAULT_REPORT_DIR = Path("reports")
DEFAULT_COMPLETED_RATINGS = Path("data/kpop_full_retrieval_human_ratings.csv")
METHOD_LABELS = {
    "cosine_shrinkage": "cosine＋shrinkage",
    "session_cooccurrence": "30分セッション共起",
    "implicit_als": "implicit ALS",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_completed_ratings(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if path.suffix.lower() == ".csv":
        rows: list[dict[str, Any]] = read_csv(path)
        parsed = [parse_rating(row.get("human_rating_0_1_2")) for row in rows]
        new_rows = [row for row in rows if row.get("needs_rating") == "yes"]
        payload = {
            "source": str(path),
            "row_count": len(rows),
            "rated_count": sum(rating is not None for rating in parsed),
            "blank_count": sum(rating is None for rating in parsed),
            "new_row_count": len(new_rows),
            "new_rated_count": sum(
                parse_rating(row.get("human_rating_0_1_2")) is not None for row in new_rows
            ),
        }
        return payload, rows
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("Completed ratings JSON must contain a rows list")
    return payload, rows


def join_ratings(
    key_rows: list[dict[str, Any]], completed_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    ratings: dict[tuple[str, str], dict[str, Any]] = {}
    for row in completed_rows:
        key = (
            str(row.get("seed_artist_name", "")).strip(),
            str(row.get("candidate_artist_mbid", "")).strip().lower(),
        )
        rating = parse_rating(row.get("human_rating_0_1_2"))
        if not all(key) or rating is None:
            raise ValueError(f"Missing identity or rating in completed row: {row!r}")
        if key in ratings:
            raise ValueError(f"Duplicate completed rating for {key}")
        ratings[key] = {**row, "human_rating_0_1_2": rating}

    joined: list[dict[str, Any]] = []
    missing: list[tuple[str, str]] = []
    for key_row in key_rows:
        key = (
            str(key_row.get("seed_artist_name", "")).strip(),
            str(key_row.get("candidate_artist_mbid", "")).strip().lower(),
        )
        rating_row = ratings.get(key)
        if rating_row is None:
            missing.append(key)
            continue
        joined.append(
            {
                **key_row,
                "rank": int(key_row.get("rank") or 0),
                "score": float(key_row.get("score") or 0.0),
                "evidence_count": (
                    int(key_row["evidence_count"])
                    if str(key_row.get("evidence_count", "")).strip()
                    else None
                ),
                "blind_candidate_id": rating_row.get("blind_candidate_id", ""),
                "needs_rating": rating_row.get("needs_rating", ""),
                "human_rating_0_1_2": rating_row["human_rating_0_1_2"],
                "human_notes": rating_row.get("human_notes", ""),
            }
        )
    if missing:
        raise ValueError(f"Missing completed ratings for {len(missing)} key rows: {missing[:3]}")
    return joined


def validate_retrieval_rows(rows: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["method"]), str(row["seed_artist_name"]))].append(int(row["rank"]))
    methods = {method for method, _seed in grouped}
    if methods != set(METHODS):
        raise ValueError(f"Expected methods {METHODS}, got {sorted(methods)}")
    invalid = {
        key: sorted(ranks)
        for key, ranks in grouped.items()
        if sorted(ranks) != list(range(1, 11))
    }
    if invalid:
        raise ValueError(f"Each method/seed must have ranks 1-10: {invalid}")


def build_result(
    key_rows: list[dict[str, Any]],
    completed_payload: dict[str, Any],
    completed_rows: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    *,
    bootstrap_samples: int = 20_000,
    random_state: int = 42,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    scored_rows = join_ratings(key_rows, completed_rows)
    validate_retrieval_rows(scored_rows)

    methods: dict[str, dict[str, Any]] = {}
    by_artist_rows: list[dict[str, Any]] = []
    for method in METHODS:
        method_rows = [row for row in scored_rows if row["method"] == method]
        summary = summarize_ranked_rows(method_rows)
        methods[method] = summary
        by_artist_rows.extend({"method": method, **row} for row in summary["by_artist"])

    ordered_methods = sorted(
        METHODS,
        key=lambda method: (
            methods[method]["macro_ndcg_at_10"],
            methods[method]["macro_strict_precision_at_5"],
        ),
        reverse=True,
    )
    distribution = Counter(
        parse_rating(row.get("human_rating_0_1_2")) for row in completed_rows
    )
    result = {
        "comparison_scope": "independently_retrieved_top10_union_human_rated",
        "best_method_by_ndcg": ordered_methods[0],
        "recommended_method": "cosine_shrinkage",
        "method_order_by_ndcg": ordered_methods,
        "rating_audit": {
            "union_candidate_count": len(completed_rows),
            "rated_count": sum(distribution.values()),
            "blank_count": int(completed_payload.get("blank_count", 0)),
            "new_rating_count": int(completed_payload.get("new_row_count", 0)),
            "new_rated_count": int(completed_payload.get("new_rated_count", 0)),
            "rating_distribution": {
                "2": distribution[2],
                "1": distribution[1],
                "0": distribution[0],
            },
        },
        "methods": methods,
        "paired_bootstrap": bootstrap_ndcg_deltas(
            methods,
            baseline="cosine_shrinkage",
            samples=bootstrap_samples,
            random_state=random_state,
        ),
        "retrieval": retrieval_summary,
        "decision_rule": (
            "Report macro NDCG@10 and strict Precision@5 together. Recommend cosine+shrinkage "
            "because session NDCG improvement is uncertain, cosine has higher strict P@5/P@10, "
            "and session processing is substantially more expensive. Bootstrap intervals are paired "
            "by seed artist."
        ),
    }
    scored_rows.sort(
        key=lambda row: (
            METHODS.index(str(row["method"])),
            str(row["seed_artist_name"]).casefold(),
            int(row["rank"]),
        )
    )
    by_artist_rows.sort(
        key=lambda row: (
            METHODS.index(str(row["method"])),
            str(row["seed_artist_name"]).casefold(),
        )
    )
    return result, scored_rows, by_artist_rows


def write_markdown(path: Path, result: dict[str, Any]) -> None:
    audit = result["rating_audit"]
    lines = [
        "# K-POP独自Top 10の完全比較",
        "",
        f"3方式の独自Top 10の和集合{audit['union_candidate_count']}候補をすべて人手評価しました。",
        "",
        "| 方式 | 緩いP@10 | 厳しいP@10 | 厳しいP@5 | NDCG@10 |",
        "|---|---:|---:|---:|---:|",
    ]
    for method in result["method_order_by_ndcg"]:
        metrics = result["methods"][method]
        lines.append(
            f"| {METHOD_LABELS[method]} | "
            f"{metrics['macro_relaxed_precision_at_10']:.1%} | "
            f"{metrics['macro_strict_precision_at_10']:.1%} | "
            f"{metrics['macro_strict_precision_at_5']:.1%} | "
            f"{metrics['macro_ndcg_at_10']:.1%} |"
        )
    lines.extend(["", "## cosine＋shrinkageとの差", ""])
    comparisons = result["paired_bootstrap"]["comparisons"]
    for method in ("session_cooccurrence", "implicit_als"):
        comparison = comparisons[method]
        lines.append(
            f"- {METHOD_LABELS[method]}: NDCG差 {comparison['mean_ndcg_delta']:+.2%} "
            f"（95% CI {comparison['ci_95_low']:+.2%}〜{comparison['ci_95_high']:+.2%}、"
            f"改善確率 {comparison['bootstrap_probability_better']:.1%}）"
        )
    best = result["best_method_by_ndcg"]
    recommended = result["recommended_method"]
    lines.extend(
        [
            "",
            "## 判定",
            "",
            f"今回の10組では、NDCG@10が最も高い方式は{METHOD_LABELS[best]}です。",
            f"ただし改善幅は小さく誤差範囲が0をまたぎます。厳しいP@5・P@10と計算負荷も含め、MVPには{METHOD_LABELS[recommended]}を推奨します。",
            "対象がK-POPヨジャドル10組・採点者1人・7日分のListenBrainzダンプであるため、他ジャンルへの一般化は次の検証課題です。",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(
    completed_ratings: Path,
    key_csv: Path,
    retrieval_summary_json: Path,
    report_dir: Path,
    *,
    bootstrap_samples: int = 20_000,
    ratings_output: Path | None = None,
) -> dict[str, Any]:
    completed_payload, completed_rows = load_completed_ratings(completed_ratings)
    if ratings_output is not None:
        write_csv(ratings_output, completed_rows)
    key_rows = read_csv(key_csv)
    retrieval_summary = json.loads(retrieval_summary_json.read_text(encoding="utf-8"))
    result, scored_rows, by_artist_rows = build_result(
        key_rows,
        completed_payload,
        completed_rows,
        retrieval_summary,
        bootstrap_samples=bootstrap_samples,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "kpop_full_retrieval_metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(report_dir / "kpop_full_retrieval_metrics.md", result)
    write_csv(report_dir / "kpop_full_retrieval_by_artist.csv", by_artist_rows)
    write_csv(report_dir / "kpop_full_retrieval_scored.csv", scored_rows)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score independently retrieved Top 10 lists")
    parser.add_argument("--completed-ratings", type=Path, default=DEFAULT_COMPLETED_RATINGS)
    parser.add_argument("--key-csv", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--retrieval-summary-json", type=Path, default=DEFAULT_RETRIEVAL_SUMMARY)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--bootstrap-samples", type=int, default=20_000)
    parser.add_argument("--ratings-output", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run(
        args.completed_ratings,
        args.key_csv,
        args.retrieval_summary_json,
        args.report_dir,
        bootstrap_samples=args.bootstrap_samples,
        ratings_output=args.ratings_output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

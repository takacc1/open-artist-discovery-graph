from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.feasibility import write_csv


@dataclass(frozen=True)
class WindowFallbackPolicy:
    primary_window_days: int = 30
    extended_window_days: int = 90
    min_primary_listeners: int = 30


def request_route(
    *,
    has_precomputed_edges: bool,
    primary_listener_count: int | None,
    extended_listener_count: int | None,
    metadata_available: bool,
    policy: WindowFallbackPolicy = WindowFallbackPolicy(),
) -> str:
    """Choose the next safe action for an artist requested by the API.

    A missing precomputed edge is not treated as a negative recommendation.
    It triggers calculation when behavioral data exists, then a separately
    labelled metadata fallback, and finally an unavailable response.
    """
    if has_precomputed_edges:
        return "serve_precomputed"
    if primary_listener_count is None:
        return "resolve_mbid_and_queue"
    if primary_listener_count >= policy.min_primary_listeners:
        return "calculate_primary_on_demand"
    if extended_listener_count is None:
        return "calculate_extended_on_demand"
    if extended_listener_count > 0:
        return "calculate_extended_on_demand"
    if metadata_available:
        return "serve_metadata_fallback"
    return "unavailable"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _similarity_summary(payload: dict[str, Any]) -> dict[str, Any]:
    similarity = payload.get("similarity", payload)
    if not isinstance(similarity, dict) or not isinstance(similarity.get("artists"), list):
        raise ValueError("Similarity JSON must contain an artists list")
    return similarity


def _artists_by_mbid(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["mbid"]): row for row in summary["artists"]}


def combine_window_results(
    primary_rows: list[dict[str, Any]],
    primary_payload: dict[str, Any],
    extended_rows: list[dict[str, Any]],
    extended_payload: dict[str, Any],
    policy: WindowFallbackPolicy = WindowFallbackPolicy(),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select 30-day results normally and 90-day results for sparse seeds."""
    primary_summary = _similarity_summary(primary_payload)
    extended_summary = _similarity_summary(extended_payload)
    primary_artists = _artists_by_mbid(primary_summary)
    extended_artists = _artists_by_mbid(extended_summary)
    target_mbids = list(primary_artists)
    for mbid in extended_artists:
        if mbid not in primary_artists:
            target_mbids.append(mbid)

    primary_by_seed: dict[str, list[dict[str, Any]]] = {}
    extended_by_seed: dict[str, list[dict[str, Any]]] = {}
    for row in primary_rows:
        primary_by_seed.setdefault(str(row["seed_artist_mbid"]), []).append(row)
    for row in extended_rows:
        extended_by_seed.setdefault(str(row["seed_artist_mbid"]), []).append(row)

    combined: list[dict[str, Any]] = []
    artist_decisions: list[dict[str, Any]] = []
    for mbid in target_mbids:
        primary = primary_artists.get(mbid, {})
        extended = extended_artists.get(mbid, {})
        primary_listeners = int(primary.get("listener_count_in_window", 0))
        extended_listeners = int(extended.get("listener_count_in_window", 0))
        primary_candidates = primary_by_seed.get(mbid, [])
        extended_candidates = extended_by_seed.get(mbid, [])

        use_primary = (
            primary_listeners >= policy.min_primary_listeners and bool(primary_candidates)
        )
        selected_rows = primary_candidates if use_primary else extended_candidates
        selected_window = (
            policy.primary_window_days if use_primary else policy.extended_window_days
        )
        fallback_reason = "" if use_primary else (
            f"primary_listeners_below_{policy.min_primary_listeners}"
            if primary_listeners < policy.min_primary_listeners
            else "primary_recommendations_unavailable"
        )
        source = "behavior_primary" if use_primary else "behavior_extended"

        if not selected_rows:
            status = "metadata_fallback_required"
            source = "metadata_pending"
        else:
            status = "ready"
            for row in selected_rows:
                combined.append(
                    {
                        **row,
                        "window_days": selected_window,
                        "recommendation_source": source,
                        "fallback_reason": fallback_reason,
                    }
                )

        artist_decisions.append(
            {
                "artist_name": primary.get("artist_name") or extended.get("artist_name", ""),
                "mbid": mbid,
                "primary_listener_count": primary_listeners,
                "extended_listener_count": extended_listeners,
                "selected_window_days": selected_window if selected_rows else None,
                "recommendation_source": source,
                "recommendation_count": len(selected_rows),
                "status": status,
                "fallback_reason": fallback_reason,
            }
        )

    combined.sort(
        key=lambda row: (
            str(row.get("seed_artist_name", "")).casefold(),
            int(row.get("rank", 0)),
        )
    )
    summary = {
        "policy": {
            "primary_window_days": policy.primary_window_days,
            "extended_window_days": policy.extended_window_days,
            "min_primary_listeners": policy.min_primary_listeners,
        },
        "target_artist_count": len(artist_decisions),
        "ready_artist_count": sum(row["status"] == "ready" for row in artist_decisions),
        "primary_artist_count": sum(
            row["recommendation_source"] == "behavior_primary" for row in artist_decisions
        ),
        "extended_artist_count": sum(
            row["recommendation_source"] == "behavior_extended" for row in artist_decisions
        ),
        "metadata_fallback_required_count": sum(
            row["status"] == "metadata_fallback_required" for row in artist_decisions
        ),
        "artists": artist_decisions,
    }
    return combined, summary


def run(
    primary_rows_path: Path,
    primary_summary_path: Path,
    extended_rows_path: Path,
    extended_summary_path: Path,
    output_dir: Path,
    policy: WindowFallbackPolicy,
) -> dict[str, Any]:
    rows, summary = combine_window_results(
        _read_csv(primary_rows_path),
        json.loads(primary_summary_path.read_text(encoding="utf-8")),
        _read_csv(extended_rows_path),
        json.loads(extended_summary_path.read_text(encoding="utf-8")),
        policy,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "similarity_top50.csv", rows)
    (output_dir / "fallback_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Use 30-day similarity normally and 90-day similarity for sparse artists"
    )
    parser.add_argument("--primary-rows", type=Path, required=True)
    parser.add_argument("--primary-summary", type=Path, required=True)
    parser.add_argument("--extended-rows", type=Path, required=True)
    parser.add_argument("--extended-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--primary-window-days", type=int, default=30)
    parser.add_argument("--extended-window-days", type=int, default=90)
    parser.add_argument("--min-primary-listeners", type=int, default=30)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = run(
        args.primary_rows,
        args.primary_summary,
        args.extended_rows,
        args.extended_summary,
        args.output_dir,
        WindowFallbackPolicy(
            primary_window_days=args.primary_window_days,
            extended_window_days=args.extended_window_days,
            min_primary_listeners=args.min_primary_listeners,
        ),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

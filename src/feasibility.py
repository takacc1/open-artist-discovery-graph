from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import requests
except ModuleNotFoundError:  # Allows data/scoring tests before optional runtime deps are installed.
    requests = None  # type: ignore[assignment]


REQUEST_ERROR = requests.RequestException if requests is not None else RuntimeError


MUSICBRAINZ_SEARCH_URL = "https://musicbrainz.org/ws/2/artist/"
LISTENBRAINZ_LISTENERS_URL = "https://api.listenbrainz.org/1/stats/artist/{mbid}/listeners"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"

DEFAULT_INPUT = Path("data/validation_artists.csv")
DEFAULT_REPORT_DIR = Path("reports")


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)


def parse_optional_bool(value: str | None) -> bool | None:
    if value is None or not value.strip():
        return None
    lowered = value.strip().casefold()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    raise ValueError(f"Unsupported boolean value: {value!r}")


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class CandidateScore:
    total: float
    exact_name: bool
    country_match: bool
    type_match: bool
    ended_match: bool
    hint_match: bool


def candidate_names(candidate: dict[str, Any]) -> list[str]:
    names = [str(candidate.get("name", ""))]
    names.extend(str(alias.get("name", "")) for alias in candidate.get("aliases", []))
    return [name for name in names if name]


def score_candidate(row: dict[str, str], candidate: dict[str, Any]) -> CandidateScore:
    expected_name = normalize_name(row["artist_name"])
    exact_name = any(normalize_name(name) == expected_name for name in candidate_names(candidate))

    expected_country = row.get("expected_country", "").strip().upper()
    country_match = not expected_country or candidate.get("country") == expected_country

    expected_type = row.get("expected_type", "").strip().casefold()
    candidate_type = str(candidate.get("type", "")).strip().casefold()
    type_match = not expected_type or candidate_type == expected_type

    expected_ended = parse_optional_bool(row.get("expected_ended"))
    actual_ended = candidate.get("life-span", {}).get("ended")
    ended_match = expected_ended is None or actual_ended is expected_ended

    hint = normalize_name(row.get("disambiguation_hint", ""))
    comment = normalize_name(str(candidate.get("disambiguation", "")))
    hint_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z]{3,}|[\u3040-\u30ff\u3400-\u9fff]{2,}", row.get("disambiguation_hint", ""))
    }
    comment_folded = str(candidate.get("disambiguation", "")).casefold()
    hint_match = not hint or hint in comment or any(token in comment_folded for token in hint_tokens)

    api_score = max(0, min(100, safe_int(candidate.get("score"))))
    total = api_score * 0.40
    total += 30 if exact_name else 0
    total += 10 if country_match else 0
    total += 8 if type_match else 0
    total += 5 if ended_match else 0
    total += 7 if hint_match else 0
    return CandidateScore(total, exact_name, country_match, type_match, ended_match, hint_match)


def compact_candidate(candidate: dict[str, Any], score: CandidateScore) -> dict[str, Any]:
    return {
        "mbid": candidate.get("id", ""),
        "name": candidate.get("name", ""),
        "disambiguation": candidate.get("disambiguation", ""),
        "country": candidate.get("country", ""),
        "type": candidate.get("type", ""),
        "ended": candidate.get("life-span", {}).get("ended"),
        "api_score": safe_int(candidate.get("score")),
        "resolver_score": round(score.total, 2),
    }


class PoliteSession:
    def __init__(self, user_agent: str, minimum_interval: float, max_retries: int = 4) -> None:
        if requests is None:
            raise RuntimeError("Install runtime dependencies with: python -m pip install -r requirements.txt")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        self.minimum_interval = minimum_interval
        self.max_retries = max_retries
        self.last_request_at = 0.0
        self.defer_until = 0.0

    @staticmethod
    def _header_delay(response: Any, attempt: int) -> float:
        for header in ("Retry-After", "X-RateLimit-Reset-In"):
            value = response.headers.get(header)
            if value:
                try:
                    return max(1.0, float(value))
                except ValueError:
                    pass
        return min(30.0, 2.0**attempt)

    def _wait_before_request(self) -> None:
        now = time.monotonic()
        delay = max(
            self.minimum_interval - (now - self.last_request_at),
            self.defer_until - now,
        )
        if delay > 0:
            time.sleep(delay)

    def get(self, url: str, **kwargs: Any) -> Any:
        for attempt in range(self.max_retries + 1):
            self._wait_before_request()
            response = self.session.get(url, timeout=30, **kwargs)
            self.last_request_at = time.monotonic()

            if response.status_code not in {429, 502, 503, 504}:
                remaining = response.headers.get("X-RateLimit-Remaining")
                reset_in = response.headers.get("X-RateLimit-Reset-In")
                if remaining is not None and reset_in is not None:
                    try:
                        if int(remaining) <= 1:
                            self.defer_until = time.monotonic() + max(1.0, float(reset_in))
                    except ValueError:
                        pass
                return response

            if attempt == self.max_retries:
                return response
            # Avoid hammering either service. A very long window is surfaced as an
            # error after a bounded wait so CLI runs do not hang indefinitely.
            time.sleep(min(60.0, self._header_delay(response, attempt)))

        raise RuntimeError("Unreachable retry state")


class MusicBrainzResolver:
    def __init__(self, user_agent: str, cache_dir: Path | None = None) -> None:
        # MusicBrainz asks clients to stay at or below one request per second.
        self.http = PoliteSession(user_agent, minimum_interval=1.05)
        self.cache_dir = cache_dir

    def _cache_path(self, artist_name: str) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(artist_name.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def search(self, artist_name: str, limit: int = 5) -> list[dict[str, Any]]:
        cache_path = self._cache_path(artist_name)
        if cache_path is not None and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        response = self.http.get(
            MUSICBRAINZ_SEARCH_URL,
            params={"query": artist_name, "fmt": "json", "limit": limit, "dismax": "true"},
        )
        response.raise_for_status()
        artists = response.json().get("artists", [])
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(artists, ensure_ascii=False), encoding="utf-8")
        return artists

    def resolve(self, row: dict[str, str]) -> dict[str, Any]:
        candidates = self.search(row["artist_name"])
        ranked = sorted(
            ((candidate, score_candidate(row, candidate)) for candidate in candidates),
            key=lambda pair: pair[1].total,
            reverse=True,
        )
        if not ranked:
            return {
                "resolution_status": "not_found",
                "resolved_mbid": "",
                "candidate_count": 0,
                "top_candidates": "[]",
                "resolution_note": "MusicBrainz returned no candidates",
            }

        best_candidate, best_score = ranked[0]
        runner_up_score = ranked[1][1].total if len(ranked) > 1 else 0.0
        score_gap = best_score.total - runner_up_score
        force_review = row.get("category") == "ambiguous_name"
        strong_match = (
            best_score.total >= 88
            and best_score.exact_name
            and best_score.country_match
            and best_score.type_match
            and score_gap >= 8
        )
        status = "manual_review" if force_review or not strong_match else "auto_resolved"
        return {
            "resolution_status": status,
            "resolved_mbid": best_candidate.get("id", ""),
            "resolved_name": best_candidate.get("name", ""),
            "resolved_country": best_candidate.get("country", ""),
            "resolved_type": best_candidate.get("type", ""),
            "resolved_ended": best_candidate.get("life-span", {}).get("ended", ""),
            "resolved_disambiguation": best_candidate.get("disambiguation", ""),
            "resolver_score": round(best_score.total, 2),
            "score_gap": round(score_gap, 2),
            "candidate_count": len(candidates),
            "top_candidates": json.dumps(
                [compact_candidate(candidate, score) for candidate, score in ranked[:3]],
                ensure_ascii=False,
            ),
            "resolution_note": "Confirm the selected MBID manually" if status == "manual_review" else "",
        }


class ListenBrainzCoverageClient:
    def __init__(self, user_agent: str, cache_dir: Path | None = None) -> None:
        self.http = PoliteSession(user_agent, minimum_interval=0.15)
        self.cache_dir = cache_dir

    def coverage(self, mbid: str) -> dict[str, Any]:
        cache_path = self.cache_dir / f"{mbid}.json" if self.cache_dir is not None else None
        if cache_path is not None and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        response = self.http.get(
            LISTENBRAINZ_LISTENERS_URL.format(mbid=mbid),
            params={"range": "all_time"},
        )
        if response.status_code == 204:
            result = {"listenbrainz_has_data": False, "total_listen_count": 0, "top_listener_count": 0}
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(result), encoding="utf-8")
            return result
        response.raise_for_status()
        payload = response.json().get("payload", {})
        # Do not persist the listener usernames returned by the endpoint.
        result = {
            "listenbrainz_has_data": safe_int(payload.get("total_listen_count")) > 0,
            "total_listen_count": safe_int(payload.get("total_listen_count")),
            "top_listener_count": len(payload.get("listeners", [])),
            "listenbrainz_last_updated": payload.get("last_updated", ""),
        }
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(result), encoding="utf-8")
        return result


def wikidata_coverage(mbids: Iterable[str], user_agent: str) -> dict[str, dict[str, Any]]:
    if requests is None:
        raise RuntimeError("Install runtime dependencies with: python -m pip install -r requirements.txt")
    unique_mbids = sorted({mbid for mbid in mbids if mbid})
    if not unique_mbids:
        return {}
    values = " ".join(json.dumps(mbid) for mbid in unique_mbids)
    query = f"""
SELECT ?mbid (SAMPLE(?item) AS ?item) (COUNT(DISTINCT ?genre) AS ?genre_count)
       (COUNT(DISTINCT ?country) AS ?country_count) (COUNT(DISTINCT ?start) AS ?start_count)
WHERE {{
  VALUES ?mbid {{ {values} }}
  ?item wdt:P434 ?mbid.
  OPTIONAL {{ ?item wdt:P136 ?genre. }}
  OPTIONAL {{ ?item (wdt:P27|wdt:P495) ?country. }}
  OPTIONAL {{ ?item (wdt:P571|wdt:P2031) ?start. }}
}}
GROUP BY ?mbid
""".strip()
    response = requests.get(
        WIKIDATA_SPARQL_URL,
        params={"query": query, "format": "json"},
        headers={"User-Agent": user_agent, "Accept": "application/sparql-results+json"},
        timeout=60,
    )
    response.raise_for_status()
    result: dict[str, dict[str, Any]] = {}
    for binding in response.json().get("results", {}).get("bindings", []):
        mbid = binding["mbid"]["value"]
        item_url = binding.get("item", {}).get("value", "")
        result[mbid] = {
            "wikidata_item": item_url.rsplit("/", 1)[-1] if item_url else "",
            "wikidata_genre_count": safe_int(binding.get("genre_count", {}).get("value")),
            "wikidata_country_count": safe_int(binding.get("country_count", {}).get("value")),
            "wikidata_start_count": safe_int(binding.get("start_count", {}).get("value")),
        }
    return result


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 50:
        raise ValueError(f"Expected exactly 50 validation artists, found {len(rows)} in {path}")
    names = [normalize_name(row["artist_name"]) for row in rows]
    if len(set(names)) != len(names):
        raise ValueError("Validation artist names must be unique after Unicode normalization")
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def compute_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    resolved = sum(row.get("resolution_status") in {"auto_resolved", "manual_review"} for row in rows)
    auto_resolved = sum(row.get("resolution_status") == "auto_resolved" for row in rows)
    lb_has_data = sum(bool(row.get("listenbrainz_has_data")) for row in rows)
    wikidata_has_genre = sum(safe_int(row.get("wikidata_genre_count")) > 0 for row in rows)
    confirmed = sum(row.get("manual_identity_status", "").strip().casefold() == "confirmed" for row in rows)
    incorrect = sum(row.get("manual_identity_status", "").strip().casefold() == "incorrect" for row in rows)
    pending = total - confirmed - incorrect
    metrics = {
        "artist_count": total,
        "musicbrainz_candidate_rate": round(resolved / total, 4),
        "musicbrainz_auto_resolution_rate": round(auto_resolved / total, 4),
        "listenbrainz_data_rate": round(lb_has_data / total, 4),
        "wikidata_genre_rate": round(wikidata_has_genre / total, 4),
        "identity_confirmed_count": confirmed,
        "identity_incorrect_count": incorrect,
        "identity_pending_count": pending,
    }
    metrics["go_no_go"] = {
        "musicbrainz_90_percent": metrics["musicbrainz_candidate_rate"] >= 0.90,
        "listenbrainz_70_percent": metrics["listenbrainz_data_rate"] >= 0.70,
        "same_name_zero_errors": incorrect == 0 if pending == 0 else None,
        "decision": (
            "PENDING_MANUAL_IDENTITY_REVIEW"
            if pending > 0
            else "GO"
            if metrics["musicbrainz_candidate_rate"] >= 0.90
            and metrics["listenbrainz_data_rate"] >= 0.70
            and incorrect == 0
            else "NO_GO_OR_REDUCE_SCOPE"
        ),
    }
    return metrics


def write_summary_markdown(path: Path, summary: dict[str, Any]) -> None:
    go_no_go = summary["go_no_go"]
    text = f"""# Phase 0 coverage report

Generated from `data/validation_artists.csv`. A candidate MBID is not treated as identity-confirmed until a person reviews it.

| Metric | Result | Target | Pass |
|---|---:|---:|:---:|
| MusicBrainz candidate coverage | {summary['musicbrainz_candidate_rate']:.1%} | 90% | {'yes' if go_no_go['musicbrainz_90_percent'] else 'no'} |
| MusicBrainz automatic resolution | {summary['musicbrainz_auto_resolution_rate']:.1%} | diagnostic only | - |
| ListenBrainz data coverage | {summary['listenbrainz_data_rate']:.1%} | 70% | {'yes' if go_no_go['listenbrainz_70_percent'] else 'no'} |
| Wikidata genre coverage | {summary['wikidata_genre_rate']:.1%} | diagnostic only | - |
| Identity checks pending | {summary['identity_pending_count']} | 0 | {'yes' if summary['identity_pending_count'] == 0 else 'no'} |
| Confirmed wrong identities | {summary['identity_incorrect_count']} | 0 | {'yes' if summary['identity_incorrect_count'] == 0 else 'no'} |

Decision: **{go_no_go['decision']}**

Review `reports/artist_coverage.csv`, verify each selected MBID, and set `manual_identity_status` to `confirmed` or `incorrect` in the source CSV before making the final Go / No-Go decision.
"""
    path.write_text(text, encoding="utf-8")


def run_validation(input_path: Path, report_dir: Path, user_agent: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = [dict(row) for row in read_rows(input_path)]
    cache_dir = report_dir / "cache"
    resolver = MusicBrainzResolver(user_agent, cache_dir / "musicbrainz")
    lb_client = ListenBrainzCoverageClient(user_agent, cache_dir / "listenbrainz")

    for row in rows:
        try:
            row.update(resolver.resolve(row))
        except REQUEST_ERROR as exc:
            row.update({"resolution_status": "request_error", "resolution_note": str(exc), "resolved_mbid": ""})

    for row in rows:
        mbid = str(row.get("resolved_mbid", ""))
        if not mbid:
            continue
        try:
            row.update(lb_client.coverage(mbid))
        except REQUEST_ERROR as exc:
            row.update({"listenbrainz_has_data": False, "listenbrainz_error": str(exc)})

    try:
        wd_by_mbid = wikidata_coverage((str(row.get("resolved_mbid", "")) for row in rows), user_agent)
    except REQUEST_ERROR as exc:
        wd_by_mbid = {}
        for row in rows:
            row["wikidata_error"] = str(exc)
    for row in rows:
        row.update(wd_by_mbid.get(str(row.get("resolved_mbid", "")), {}))

    summary = compute_summary(rows)
    report_dir.mkdir(parents=True, exist_ok=True)
    write_csv(report_dir / "artist_coverage.csv", rows)
    (report_dir / "coverage_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_summary_markdown(report_dir / "coverage_summary.md", summary)
    return rows, summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Phase 0 artist-data coverage")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument(
        "--contact",
        default=os.environ.get("PROJECT_CONTACT", ""),
        help="Contact URL or email included in the API User-Agent",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.contact:
        raise SystemExit("Set PROJECT_CONTACT or pass --contact with a reachable email address or URL")
    user_agent = f"open-artist-discovery-feasibility/0.1 ({args.contact})"
    _, summary = run_validation(args.input, args.report_dir, user_agent)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

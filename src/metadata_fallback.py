from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.feasibility import PoliteSession, WIKIDATA_SPARQL_URL, normalize_name, write_csv


MUSICBRAINZ_ARTIST_URL = "https://musicbrainz.org/ws/2/artist/{mbid}"
DEFAULT_MIN_LISTENERS = 30
DEFAULT_MAX_METADATA_ONLY_TOP10 = 5
DEFAULT_WIKIDATA_BATCH_SIZE = 100

# The validation genre is project-owned data. Mapping it to broad Wikidata genre
# IDs lets a sparse seed match external candidates without copying MusicBrainz's
# supplementary genre/tag data into the production model.
CURATED_WIKIDATA_GENRES = {
    "pop": {"Q37073", "Q131578", "Q484641"},
    "pop_rock": {"Q484641", "Q11399", "Q37073"},
    "rock": {"Q11399"},
    "rock_alternative": {"Q11366", "Q11399"},
    "kpop": {"Q213665"},
    "rnb": {"Q45981", "Q850412"},
    "hiphop": {"Q11401"},
    "electronic": {"Q9778"},
    "folk_pop": {"Q186472", "Q37073"},
    "punk_rock": {"Q3071"},
    "jazz": {"Q8341"},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _year(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = int(str(value)[:4])
    except ValueError:
        return None
    return parsed if 1000 <= parsed <= 9999 else None


@dataclass
class ArtistMetadata:
    mbid: str
    name: str
    artist_type: str = ""
    countries: list[str] = field(default_factory=list)
    begin_year: int | None = None
    wikidata_genres: list[str] = field(default_factory=list)
    wikidata_item: str = ""
    related_artist_mbids: list[str] = field(default_factory=list)
    curated_primary_genre: str = ""


class MusicBrainzMetadataClient:
    def __init__(self, user_agent: str, cache_dir: Path) -> None:
        self.http = PoliteSession(user_agent, minimum_interval=1.05)
        self.cache_dir = cache_dir

    def lookup(self, mbid: str) -> dict[str, Any]:
        cache_path = self.cache_dir / f"{mbid.lower()}.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        response = self.http.get(
            MUSICBRAINZ_ARTIST_URL.format(mbid=mbid),
            params={"fmt": "json", "inc": "artist-rels"},
        )
        response.raise_for_status()
        payload = response.json()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return payload


def _musicbrainz_features(payload: dict[str, Any]) -> dict[str, Any]:
    related = []
    for relation in payload.get("relations", []):
        artist = relation.get("artist") or {}
        mbid = str(artist.get("id", "")).strip().lower()
        if mbid:
            related.append(mbid)
    country = str(payload.get("country", "")).strip().upper()
    area = payload.get("area") or {}
    area_code = ""
    for code in area.get("iso-3166-1-codes", []):
        if code:
            area_code = str(code).upper()
            break
    return {
        "artist_type": str(payload.get("type", "")),
        "countries": sorted({value for value in (country, area_code) if value}),
        "begin_year": _year((payload.get("life-span") or {}).get("begin")),
        "related_artist_mbids": sorted(set(related)),
    }


def _wikidata_query(mbids: list[str]) -> str:
    values = " ".join(json.dumps(mbid) for mbid in mbids)
    return f"""
SELECT ?mbid ?item ?genre ?country ?start WHERE {{
  VALUES ?mbid {{ {values} }}
  ?item wdt:P434 ?mbid.
  OPTIONAL {{ ?item wdt:P136 ?genre. }}
  OPTIONAL {{ ?item (wdt:P27|wdt:P495) ?country. }}
  OPTIONAL {{ ?item (wdt:P571|wdt:P2031) ?start. }}
}}
""".strip()


def wikidata_features(
    mbids: Iterable[str],
    user_agent: str,
    *,
    batch_size: int = DEFAULT_WIKIDATA_BATCH_SIZE,
    cache_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    unique_mbids = sorted({mbid.strip().lower() for mbid in mbids if mbid.strip()})
    if not unique_mbids:
        return {}
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    http = PoliteSession(user_agent, minimum_interval=0.5)
    bindings: list[dict[str, Any]] = []
    for start in range(0, len(unique_mbids), batch_size):
        batch = unique_mbids[start : start + batch_size]
        digest = hashlib.sha256("\n".join(batch).encode("utf-8")).hexdigest()
        cache_path = cache_dir / f"{digest}.json" if cache_dir is not None else None
        if cache_path is not None and cache_path.exists():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            response = http.get(
                WIKIDATA_SPARQL_URL,
                params={"query": _wikidata_query(batch), "format": "json"},
                headers={"Accept": "application/sparql-results+json"},
            )
            response.raise_for_status()
            payload = response.json()
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
                )
        bindings.extend(payload.get("results", {}).get("bindings", []))

    aggregated: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        mbid = binding["mbid"]["value"].lower()
        current = aggregated.setdefault(
            mbid,
            {"wikidata_item": "", "wikidata_genres": set(), "countries": set(), "years": []},
        )
        item_url = binding.get("item", {}).get("value", "")
        genre_url = binding.get("genre", {}).get("value", "")
        country_url = binding.get("country", {}).get("value", "")
        start = _year(binding.get("start", {}).get("value"))
        if item_url:
            current["wikidata_item"] = item_url.rsplit("/", 1)[-1]
        if genre_url:
            current["wikidata_genres"].add(genre_url.rsplit("/", 1)[-1])
        if country_url:
            current["countries"].add(country_url.rsplit("/", 1)[-1])
        if start is not None:
            current["years"].append(start)
    result = {}
    for mbid, current in aggregated.items():
        result[mbid] = {
            "wikidata_item": current["wikidata_item"],
            "wikidata_genres": sorted(current["wikidata_genres"]),
            "wikidata_countries": sorted(current["countries"]),
            "wikidata_begin_year": min(current["years"], default=None),
        }
    return result


def catalog_from_database(database: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT mbid, name, artist_type, area_code, begin_year
                FROM artists
                ORDER BY name COLLATE NOCASE, mbid
                """
            )
        ]
    finally:
        connection.close()


def merge_validation_context(
    catalog_rows: list[dict[str, Any]], validation_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    validation_by_mbid = {
        _catalog_mbid(row): row for row in validation_rows if _catalog_mbid(row)
    }
    merged = []
    for row in catalog_rows:
        context = validation_by_mbid.get(_catalog_mbid(row), {})
        merged.append(
            {
                **row,
                "primary_genre": context.get("primary_genre") or row.get("primary_genre") or "",
                "expected_country": context.get("expected_country") or row.get("area_code") or "",
                "expected_type": context.get("expected_type") or row.get("artist_type") or "",
            }
        )
    return merged


def _catalog_mbid(row: dict[str, Any]) -> str:
    return str(
        row.get("manual_mbid") or row.get("resolved_mbid") or row.get("mbid") or ""
    ).strip().lower()


def _catalog_name(row: dict[str, Any]) -> str:
    return str(
        row.get("artist_name") or row.get("resolved_name") or row.get("name") or ""
    ).strip()


def fetch_metadata(
    validation_csv: Path,
    output_path: Path,
    cache_dir: Path,
    user_agent: str,
    *,
    catalog_rows: list[dict[str, Any]] | None = None,
    musicbrainz_mbids: set[str] | None = None,
    wikidata_batch_size: int = DEFAULT_WIKIDATA_BATCH_SIZE,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = catalog_rows or _read_csv(validation_csv)
    client = MusicBrainzMetadataClient(user_agent, cache_dir / "musicbrainz")
    artists: list[ArtistMetadata] = []
    errors: list[dict[str, str]] = []
    for row in rows:
        mbid = _catalog_mbid(row)
        name = _catalog_name(row)
        if not mbid or not name:
            continue
        mb: dict[str, Any] = {}
        if musicbrainz_mbids is None or mbid in musicbrainz_mbids:
            try:
                mb = _musicbrainz_features(client.lookup(mbid))
            except Exception as exc:  # One unavailable artist must not discard the whole cache.
                errors.append(
                    {
                        "mbid": mbid,
                        "artist_name": name,
                        "source": "MusicBrainz",
                        "error": str(exc),
                    }
                )
        countries = set(mb.get("countries", []))
        expected_country = str(
            row.get("expected_country") or row.get("area_code") or ""
        ).strip().upper()
        if expected_country:
            countries.add(expected_country)
        artists.append(
            ArtistMetadata(
                mbid=mbid,
                name=name,
                artist_type=str(
                    mb.get("artist_type")
                    or row.get("expected_type")
                    or row.get("artist_type")
                    or ""
                ),
                countries=sorted(countries),
                begin_year=mb.get("begin_year") or _year(row.get("begin_year")),
                related_artist_mbids=mb.get("related_artist_mbids", []),
                curated_primary_genre=(row.get("primary_genre") or "").strip().casefold(),
            )
        )

    try:
        wd = wikidata_features(
            (artist.mbid for artist in artists),
            user_agent,
            batch_size=wikidata_batch_size,
            cache_dir=cache_dir / "wikidata",
        )
    except Exception as exc:
        errors.append({"mbid": "*", "artist_name": "*", "source": "Wikidata", "error": str(exc)})
        wd = {}
    for artist in artists:
        extra = wd.get(artist.mbid, {})
        artist.wikidata_item = extra.get("wikidata_item", "")
        artist.wikidata_genres = extra.get("wikidata_genres", [])
        artist.countries = sorted(
            set(artist.countries) | set(extra.get("wikidata_countries", []))
        )
        if artist.begin_year is None:
            artist.begin_year = extra.get("wikidata_begin_year")

    payload = {
        "generated_at": _utc_now(),
        "sources": ["MusicBrainz", "Wikidata", "validation_curated_primary_genre"],
        "artist_count": len(artists),
        "musicbrainz_lookup_count": (
            len(artists)
            if musicbrainz_mbids is None
            else sum(artist.mbid in musicbrainz_mbids for artist in artists)
        ),
        "errors": errors,
        "artists": [asdict(artist) for artist in artists],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def metadata_similarity(seed: ArtistMetadata, candidate: ArtistMetadata) -> dict[str, Any]:
    seed_genres = set(seed.wikidata_genres) | CURATED_WIKIDATA_GENRES.get(
        seed.curated_primary_genre, set()
    )
    candidate_genres = set(candidate.wikidata_genres)
    wd_genre = _jaccard(seed_genres, candidate_genres)
    curated_match = bool(
        seed.curated_primary_genre
        and seed.curated_primary_genre == candidate.curated_primary_genre
    )
    direct_relation = (
        candidate.mbid in seed.related_artist_mbids
        or seed.mbid in candidate.related_artist_mbids
    )
    same_country = bool(set(seed.countries) & set(candidate.countries))
    same_type = bool(seed.artist_type and seed.artist_type == candidate.artist_type)
    year_gap = None
    year_score = 0.0
    if seed.begin_year is not None and candidate.begin_year is not None:
        year_gap = abs(seed.begin_year - candidate.begin_year)
        year_score = max(0.0, 1.0 - year_gap / 30.0)

    score = (
        0.45 * wd_genre
        + 0.20 * float(curated_match)
        + 0.12 * float(direct_relation)
        + 0.08 * float(same_country)
        + 0.08 * year_score
        + 0.07 * float(same_type)
    )
    # Country/type/year alone are weak demographic clues, not musical similarity.
    strong_evidence = bool(wd_genre or curated_match or direct_relation)
    if not strong_evidence:
        score = 0.0
    evidence = {
        "shared_wikidata_genres": sorted(
            seed_genres & candidate_genres
        ),
        "curated_genre_match": curated_match,
        "direct_relation": direct_relation,
        "same_country": same_country,
        "same_type": same_type,
        "year_gap": year_gap,
    }
    evidence_count = sum(
        [
            bool(evidence["shared_wikidata_genres"]),
            curated_match,
            direct_relation,
            same_country,
            same_type,
            year_gap is not None and year_gap <= 10,
        ]
    )
    return {"metadata_score": round(score, 6), "evidence_count": evidence_count, **evidence}


def _listener_counts(summary_payload: dict[str, Any]) -> dict[str, int]:
    similarity = summary_payload.get("similarity", summary_payload)
    return {
        str(row["mbid"]).lower(): int(row.get("listener_count_in_window", 0))
        for row in similarity.get("artists", [])
    }


def _metadata_reason(evidence: dict[str, Any]) -> str:
    parts = []
    if evidence.get("shared_wikidata_genres"):
        parts.append("Wikidataジャンル一致")
    if evidence.get("curated_genre_match"):
        parts.append("検証用ジャンル一致")
    if evidence.get("direct_relation"):
        parts.append("MusicBrainzに直接関係あり")
    if evidence.get("same_country"):
        parts.append("活動地域が近い")
    return " / ".join(parts) or "メタデータ補助"


def excluded_names_from_csv(path: Path) -> set[str]:
    names: set[str] = set()
    for row in _read_csv(path):
        for key in ("artist_name", "canonical_name", "name"):
            value = (row.get(key) or "").strip()
            if value:
                names.add(normalize_name(value))
    return names


def blend_low_data_results(
    behavior_rows: list[dict[str, Any]],
    summary_payload: dict[str, Any],
    metadata_payload: dict[str, Any],
    *,
    min_listeners: int = DEFAULT_MIN_LISTENERS,
    limit: int = 50,
    max_metadata_only_top10: int = DEFAULT_MAX_METADATA_ONLY_TOP10,
    excluded_candidate_names: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if min_listeners <= 0 or limit <= 0 or max_metadata_only_top10 < 0:
        raise ValueError("thresholds and limits are invalid")
    features = {
        row["mbid"].lower(): ArtistMetadata(**row)
        for row in metadata_payload.get("artists", [])
    }
    counts = _listener_counts(summary_payload)
    excluded = excluded_candidate_names or set()
    by_seed: dict[str, list[dict[str, Any]]] = {}
    for row in behavior_rows:
        by_seed.setdefault(str(row["seed_artist_mbid"]).lower(), []).append(dict(row))

    output: list[dict[str, Any]] = []
    decisions = []
    for seed_mbid, rows in by_seed.items():
        rows.sort(key=lambda row: int(row["rank"]))
        listener_count = counts.get(seed_mbid, 0)
        seed = features.get(seed_mbid)
        if listener_count >= min_listeners or seed is None:
            for row in rows[:limit]:
                output.append({**row, "recommendation_source": "behavior_primary"})
            decisions.append(
                {
                    "artist_name": rows[0]["seed_artist_name"],
                    "mbid": seed_mbid,
                    "listener_count": listener_count,
                    "strategy": "behavior_primary",
                    "metadata_candidate_count": 0,
                    "top10_changed_count": 0,
                }
            )
            continue

        behavior_weight = min(0.8, max(0.15, 0.8 * listener_count / min_listeners))
        candidates: dict[str, dict[str, Any]] = {}
        excluded_count = 0
        for row in rows:
            if normalize_name(str(row["candidate_artist_name"])) in excluded:
                excluded_count += 1
                continue
            candidate_mbid = str(row["candidate_artist_mbid"]).lower()
            rank_score = 1.0 / math.log2(int(row["rank"]) + 1)
            candidates[candidate_mbid] = {
                **row,
                "behavior_score": float(row.get("similarity_score") or 0),
                "behavior_rank_score": rank_score,
                "metadata_score": 0.0,
                "relation_score": 0.0,
                "metadata_evidence": {},
            }

        metadata_candidates = 0
        for candidate_mbid, candidate in features.items():
            if candidate_mbid == seed_mbid:
                continue
            if normalize_name(candidate.name) in excluded:
                continue
            evidence = metadata_similarity(seed, candidate)
            if evidence["metadata_score"] <= 0:
                continue
            metadata_candidates += 1
            current = candidates.setdefault(
                candidate_mbid,
                {
                    "seed_artist_name": seed.name,
                    "seed_artist_mbid": seed.mbid,
                    "candidate_artist_name": candidate.name,
                    "candidate_artist_mbid": candidate.mbid,
                    "cosine_similarity": 0.0,
                    "shrinkage_factor": 0.0,
                    "common_listener_count": 0,
                    "candidate_listener_count": counts.get(candidate_mbid, 0),
                    "behavior_score": 0.0,
                    "behavior_rank_score": 0.0,
                },
            )
            current["metadata_score"] = evidence["metadata_score"]
            current["relation_score"] = 1.0 if evidence["direct_relation"] else 0.0
            current["metadata_evidence"] = evidence

        ranked = []
        for current in candidates.values():
            metadata_score = float(current.get("metadata_score", 0))
            behavior_rank_score = float(current.get("behavior_rank_score", 0))
            total = behavior_weight * behavior_rank_score + (1 - behavior_weight) * metadata_score
            has_behavior = behavior_rank_score > 0
            has_metadata = metadata_score > 0
            source = (
                "behavior_metadata_blend"
                if has_behavior and has_metadata
                else "metadata_fallback"
                if has_metadata
                else "behavior_low_data"
            )
            evidence = current.get("metadata_evidence", {})
            evidence_count = int(evidence.get("evidence_count", 0))
            confidence = "medium" if has_metadata and evidence_count >= 3 else "low"
            reason = _metadata_reason(evidence) if has_metadata else current.get("reason", "行動類似度")
            ranked.append(
                {
                    **current,
                    "similarity_score": round(total, 6),
                    "metadata_score": round(metadata_score, 6),
                    "metadata_evidence_count": evidence_count,
                    "behavior_weight": round(behavior_weight, 6),
                    "recommendation_source": source,
                    "confidence": confidence,
                    "reason": reason,
                    "metadata_evidence": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    "window_days": 30,
                    "fallback_reason": f"listeners_below_{min_listeners}",
                }
            )
        ranked.sort(
            key=lambda row: (
                float(row["similarity_score"]),
                float(row["metadata_score"]),
                str(row["candidate_artist_mbid"]),
            ),
            reverse=True,
        )
        selected_top10: list[dict[str, Any]] = []
        metadata_only_count = 0
        for row in ranked:
            if len(selected_top10) >= min(10, limit):
                break
            if row["recommendation_source"] == "metadata_fallback":
                if metadata_only_count >= max_metadata_only_top10:
                    continue
                metadata_only_count += 1
            selected_top10.append(row)
        selected_ids = {id(row) for row in selected_top10}
        ordered = selected_top10 + [row for row in ranked if id(row) not in selected_ids]
        old_top10 = [str(row["candidate_artist_mbid"]).lower() for row in rows[:10]]
        new_top10 = [str(row["candidate_artist_mbid"]).lower() for row in ordered[:10]]
        for rank, row in enumerate(ordered[:limit], 1):
            row["rank"] = rank
            output.append(row)
        decisions.append(
            {
                "artist_name": seed.name,
                "mbid": seed_mbid,
                "listener_count": listener_count,
                "strategy": "behavior_metadata_blend",
                "behavior_weight": round(behavior_weight, 6),
                "metadata_candidate_count": metadata_candidates,
                "excluded_behavior_candidate_count": excluded_count,
                "top10_changed_count": len(set(old_top10) ^ set(new_top10)) // 2,
            }
        )

    output.sort(key=lambda row: (str(row["seed_artist_name"]).casefold(), int(row["rank"])))
    low_data = [row for row in decisions if row["listener_count"] < min_listeners]
    report = {
        "generated_at": _utc_now(),
        "policy": {
            "min_listener_count": min_listeners,
            "max_metadata_only_candidates_in_top10": max_metadata_only_top10,
            "known_candidates_excluded": bool(excluded),
            "behavior_weight": "clamp(0.8 * listeners / threshold, 0.15, 0.8)",
            "metadata_weights": {
                "wikidata_genre_jaccard": 0.45,
                "curated_primary_genre_match": 0.20,
                "musicbrainz_direct_relation": 0.12,
                "country_match": 0.08,
                "begin_year_proximity": 0.08,
                "artist_type_match": 0.07,
            },
        },
        "artist_count": len(decisions),
        "low_data_artist_count": len(low_data),
        "low_data_with_metadata_candidates": sum(
            row["metadata_candidate_count"] > 0 for row in low_data
        ),
        "artists": decisions,
    }
    return output, report


def blend_files(
    behavior_rows_path: Path,
    behavior_summary_path: Path,
    metadata_path: Path,
    output_dir: Path,
    min_listeners: int,
    limit: int,
    max_metadata_only_top10: int = DEFAULT_MAX_METADATA_ONLY_TOP10,
    excluded_artists_path: Path | None = None,
) -> dict[str, Any]:
    rows, report = blend_low_data_results(
        _read_csv(behavior_rows_path),
        json.loads(behavior_summary_path.read_text(encoding="utf-8")),
        json.loads(metadata_path.read_text(encoding="utf-8")),
        min_listeners=min_listeners,
        limit=limit,
        max_metadata_only_top10=max_metadata_only_top10,
        excluded_candidate_names=(
            excluded_names_from_csv(excluded_artists_path)
            if excluded_artists_path is not None
            else None
        ),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "similarity_top50.csv", rows)
    (output_dir / "metadata_fallback_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch metadata and blend sparse artist recommendations")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="Fetch MusicBrainz and Wikidata metadata")
    fetch.add_argument("--input", type=Path, default=Path("data/validation_artists.csv"))
    fetch.add_argument(
        "--database",
        type=Path,
        help="Use every artist in the serving SQLite DB as the candidate catalog",
    )
    fetch.add_argument(
        "--behavior-summary",
        type=Path,
        help="When using --database, fetch MusicBrainz relations only for sparse seeds",
    )
    fetch.add_argument("--output", type=Path, default=Path("reports/metadata/artist_features.json"))
    fetch.add_argument("--cache-dir", type=Path, default=Path("reports/cache/metadata"))
    fetch.add_argument("--contact", default=os.environ.get("PROJECT_CONTACT", ""))
    fetch.add_argument("--min-listeners", type=int, default=DEFAULT_MIN_LISTENERS)
    fetch.add_argument(
        "--wikidata-batch-size", type=int, default=DEFAULT_WIKIDATA_BATCH_SIZE
    )
    blend = sub.add_parser("blend", help="Blend metadata into artists below the listener threshold")
    blend.add_argument("--behavior-rows", type=Path, required=True)
    blend.add_argument("--behavior-summary", type=Path, required=True)
    blend.add_argument("--metadata", type=Path, required=True)
    blend.add_argument("--output-dir", type=Path, required=True)
    blend.add_argument("--min-listeners", type=int, default=DEFAULT_MIN_LISTENERS)
    blend.add_argument("--limit", type=int, default=50)
    blend.add_argument(
        "--max-metadata-only-top10", type=int, default=DEFAULT_MAX_METADATA_ONLY_TOP10
    )
    blend.add_argument(
        "--exclude-artists",
        type=Path,
        help="Local CSV of known artists to exclude from sparse evaluation candidates",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "fetch":
        if not args.contact:
            raise SystemExit("Set PROJECT_CONTACT or pass --contact")
        catalog_rows = None
        if args.database:
            catalog_rows = merge_validation_context(
                catalog_from_database(args.database), _read_csv(args.input)
            )
        musicbrainz_mbids = None
        if args.database:
            if args.behavior_summary:
                listener_counts = _listener_counts(
                    json.loads(args.behavior_summary.read_text(encoding="utf-8"))
                )
                musicbrainz_mbids = {
                    mbid
                    for mbid, count in listener_counts.items()
                    if count < args.min_listeners
                }
            else:
                # Avoid thousands of rate-limited lookups by default. Wikidata
                # still covers the full DB candidate catalog.
                musicbrainz_mbids = set()
        payload = fetch_metadata(
            args.input,
            args.output,
            args.cache_dir,
            f"open-artist-discovery-metadata/0.1 ({args.contact})",
            catalog_rows=catalog_rows,
            musicbrainz_mbids=musicbrainz_mbids,
            wikidata_batch_size=args.wikidata_batch_size,
        )
        print(
            json.dumps(
                {
                    "artist_count": payload["artist_count"],
                    "musicbrainz_lookup_count": payload["musicbrainz_lookup_count"],
                    "errors": payload["errors"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        report = blend_files(
            args.behavior_rows,
            args.behavior_summary,
            args.metadata,
            args.output_dir,
            args.min_listeners,
            args.limit,
            args.max_metadata_only_top10,
            args.exclude_artists,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

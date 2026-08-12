from __future__ import annotations

import os
from collections import defaultdict
from contextlib import contextmanager
from typing import Annotated, Any, Iterator, Literal
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field


CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.8, "low": 0.5}
SUPPORTED_MODES = {"near", "bridge", "adventure"}


class ArtistSummary(BaseModel):
    mbid: str
    name: str
    artist_type: str | None = None
    area_code: str | None = None


class ArtistDetail(ArtistSummary):
    sort_name: str | None = None
    begin_year: int | None = None
    end_year: int | None = None
    data_version: str | None = None
    updated_at: str
    neighbor_count: int
    active_model: str


class Neighbor(BaseModel):
    artist_mbid: str
    artist_name: str
    rank: int
    total_score: float
    behavior_score: float
    metadata_score: float
    relation_score: float
    confidence: Literal["high", "medium", "low"]
    common_listener_count: int
    recommendation_source: str
    window_days: int
    model_version: str


class SeedScore(BaseModel):
    seed_artist_mbid: str
    seed_artist_name: str
    similarity_score: float


class RecommendationItem(BaseModel):
    artist_mbid: str
    artist_name: str
    score: float
    confidence: Literal["high", "medium", "low"]
    matched_seed_count: int
    seed_scores: list[SeedScore]
    recommendation_source: str
    window_days: int
    reason: str


class RecommendationRequest(BaseModel):
    seed_artist_mbids: list[UUID] = Field(min_length=1, max_length=5)
    mode: Literal["near", "bridge", "adventure"] = "near"
    limit: int = Field(default=10, ge=1, le=50)


class RecommendationResponse(BaseModel):
    search_id: UUID | None = None
    mode: Literal["near", "bridge", "adventure"]
    model_version: str
    seed_count: int
    missing_seed_mbids: list[str]
    recommendations: list[RecommendationItem]


class FeedbackRequest(BaseModel):
    rating: Literal[0, 1, 2]


class PublicSeed(BaseModel):
    mbid: str
    name: str


class PublicRecommendation(BaseModel):
    artist_mbid: str
    artist_name: str
    score: float


class RecentSearch(BaseModel):
    search_id: UUID
    mode: Literal["near", "bridge", "adventure"]
    seed_artists: list[PublicSeed]
    recommendations: list[PublicRecommendation]
    feedback_rating: Literal[0, 1, 2] | None = None
    created_at: str


class DataVersionResponse(BaseModel):
    model_version: str
    window_days: int
    generated_at: str
    artist_count: int
    source_artist_count: int
    edge_count: int


def configured_origins() -> list[str]:
    value = os.environ.get(
        "ARTIST_DISCOVERY_CORS_ORIGINS",
        "http://localhost:3000,https://nextsound-jp.vercel.app,https://open-artist-discovery.tthbjcv.chatgpt.site",
    )
    return [origin.strip() for origin in value.split(",") if origin.strip()]


@contextmanager
def connect() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        yield connection


def active_model(connection: psycopg.Connection[dict[str, Any]]) -> str:
    row = connection.execute(
        "SELECT model_version FROM model_versions WHERE is_active = TRUE"
    ).fetchone()
    if row is None:
        raise ValueError("No active model version")
    return str(row["model_version"])


def data_version() -> dict[str, Any]:
    with connect() as connection:
        selected_model = active_model(connection)
        row = connection.execute(
            """
            SELECT
                model.model_version,
                model.window_days,
                model.generated_at::text AS generated_at,
                (SELECT COUNT(*) FROM artists) AS artist_count,
                (
                    SELECT COUNT(DISTINCT source_artist_id)
                    FROM artist_edges
                    WHERE model_version = model.model_version
                ) AS source_artist_count,
                (
                    SELECT COUNT(*)
                    FROM artist_edges
                    WHERE model_version = model.model_version
                ) AS edge_count
            FROM model_versions AS model
            WHERE model.model_version = %s
            """,
            (selected_model,),
        ).fetchone()
    assert row is not None
    return dict(row)


def search_artists(query: str, limit: int) -> list[dict[str, Any]]:
    normalized = query.strip()
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT mbid, name, artist_type, area_code
            FROM artists
            WHERE name ILIKE %s
            ORDER BY
                CASE
                    WHEN lower(name) = lower(%s) THEN 0
                    WHEN name ILIKE %s THEN 1
                    ELSE 2
                END,
                lower(name)
            LIMIT %s
            """,
            (f"%{normalized}%", normalized, f"{normalized}%", limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_artist(artist_mbid: str) -> dict[str, Any] | None:
    with connect() as connection:
        selected_model = active_model(connection)
        row = connection.execute(
            """
            SELECT
                artist.mbid,
                artist.name,
                artist.sort_name,
                artist.artist_type,
                artist.area_code,
                artist.begin_year,
                artist.end_year,
                artist.data_version,
                artist.updated_at::text AS updated_at,
                COUNT(edge.target_artist_id) AS neighbor_count
            FROM artists AS artist
            LEFT JOIN artist_edges AS edge
              ON edge.source_artist_id = artist.artist_id
             AND edge.model_version = %s
            WHERE artist.mbid = %s
            GROUP BY artist.artist_id
            """,
            (selected_model, artist_mbid.strip().lower()),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["active_model"] = selected_model
    return result


def get_neighbors(source_mbid: str, limit: int) -> list[dict[str, Any]]:
    with connect() as connection:
        selected_model = active_model(connection)
        rows = connection.execute(
            """
            SELECT
                target.mbid AS artist_mbid,
                target.name AS artist_name,
                edge.rank,
                edge.total_score,
                edge.behavior_score,
                edge.metadata_score,
                edge.relation_score,
                edge.confidence,
                edge.common_listener_count,
                edge.recommendation_source,
                edge.window_days,
                edge.model_version
            FROM artist_edges AS edge
            JOIN artists AS source ON source.artist_id = edge.source_artist_id
            JOIN artists AS target ON target.artist_id = edge.target_artist_id
            WHERE source.mbid = %s AND edge.model_version = %s
            ORDER BY edge.rank
            LIMIT %s
            """,
            (source_mbid.strip().lower(), selected_model, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def recommend(seed_mbids: list[str], *, mode: str, limit: int) -> dict[str, Any]:
    normalized_mode = "near" if mode == "close" else mode
    if normalized_mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported mode: {mode}")
    seeds = list(dict.fromkeys(mbid.strip().lower() for mbid in seed_mbids if mbid.strip()))
    if not seeds:
        raise ValueError("At least one seed MBID is required")

    with connect() as connection:
        selected_model = active_model(connection)
        seed_rows = connection.execute(
            "SELECT artist_id, mbid, name FROM artists WHERE mbid = ANY(%s)",
            (seeds,),
        ).fetchall()
        seed_by_id = {int(row["artist_id"]): dict(row) for row in seed_rows}
        found_mbids = {str(row["mbid"]) for row in seed_rows}
        missing_mbids = [mbid for mbid in seeds if mbid not in found_mbids]
        if not seed_by_id:
            return {
                "mode": normalized_mode,
                "model_version": selected_model,
                "seed_count": len(seeds),
                "missing_seed_mbids": missing_mbids,
                "recommendations": [],
            }

        seed_ids = list(seed_by_id)
        rows = connection.execute(
            """
            SELECT
                edge.source_artist_id,
                target.artist_id AS target_artist_id,
                target.mbid AS target_mbid,
                target.name AS target_name,
                edge.total_score,
                edge.confidence,
                edge.recommendation_source,
                edge.window_days
            FROM artist_edges AS edge
            JOIN artists AS target ON target.artist_id = edge.target_artist_id
            WHERE edge.source_artist_id = ANY(%s)
              AND edge.model_version = %s
            """,
            (seed_ids, selected_model),
        ).fetchall()

    seed_id_set = set(seed_ids)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if int(row["target_artist_id"]) not in seed_id_set:
            grouped[int(row["target_artist_id"])].append(dict(row))

    denominator = len(seeds)
    confidence_order = {"low": 0, "medium": 1, "high": 2}
    recommendations = []
    for candidate_rows in grouped.values():
        weighted = [
            float(row["total_score"]) * CONFIDENCE_WEIGHT[str(row["confidence"])]
            for row in candidate_rows
        ]
        matched = len({int(row["source_artist_id"]) for row in candidate_rows})
        coverage = matched / denominator
        maximum = max(weighted)
        average_all_seeds = sum(weighted) / denominator
        if normalized_mode == "near":
            final_score = maximum + (1 - maximum) * 0.08 * max(matched - 1, 0)
        elif normalized_mode == "bridge":
            final_score = average_all_seeds + 0.20 * coverage
        else:
            novelty = 1 - min(max(float(row["total_score"]) for row in candidate_rows), 1)
            final_score = 0.45 * maximum + 0.35 * coverage + 0.20 * novelty

        best = max(candidate_rows, key=lambda row: float(row["total_score"]))
        seed_scores = [
            {
                "seed_artist_mbid": seed_by_id[int(row["source_artist_id"])]["mbid"],
                "seed_artist_name": seed_by_id[int(row["source_artist_id"])]["name"],
                "similarity_score": float(row["total_score"]),
            }
            for row in sorted(
                candidate_rows,
                key=lambda item: float(item["total_score"]),
                reverse=True,
            )
        ]
        confidence = max(
            (str(row["confidence"]) for row in candidate_rows),
            key=lambda value: confidence_order[value],
        )
        recommendations.append(
            {
                "artist_mbid": best["target_mbid"],
                "artist_name": best["target_name"],
                "score": round(final_score, 6),
                "confidence": confidence,
                "matched_seed_count": matched,
                "seed_scores": seed_scores,
                "recommendation_source": best["recommendation_source"],
                "window_days": best["window_days"],
                "reason": f"入力{matched}組との類似関係を確認",
            }
        )

    recommendations.sort(
        key=lambda row: (row["score"], row["matched_seed_count"], row["artist_mbid"]),
        reverse=True,
    )
    return {
        "mode": normalized_mode,
        "model_version": selected_model,
        "seed_count": len(seeds),
        "missing_seed_mbids": missing_mbids,
        "recommendations": recommendations[:limit],
    }


def record_search(seed_mbids: list[str], result: dict[str, Any]) -> str:
    with connect() as connection:
        rows = connection.execute(
            "SELECT mbid, name FROM artists WHERE mbid = ANY(%s)",
            (seed_mbids,),
        ).fetchall()
        artist_by_mbid = {str(row["mbid"]): str(row["name"]) for row in rows}
        seeds = [
            {"mbid": mbid, "name": artist_by_mbid[mbid]}
            for mbid in seed_mbids
            if mbid in artist_by_mbid
        ]
        public_recommendations = [
            {
                "artist_mbid": str(item["artist_mbid"]),
                "artist_name": str(item["artist_name"]),
                "score": float(item["score"]),
            }
            for item in result["recommendations"]
        ]
        row = connection.execute(
            """
            INSERT INTO recommendation_searches (
                mode, model_version, seed_artists, recommendations
            ) VALUES (%s, %s, %s, %s)
            RETURNING search_id::text AS search_id
            """,
            (
                result["mode"],
                result["model_version"],
                Jsonb(seeds),
                Jsonb(public_recommendations),
            ),
        ).fetchone()
    assert row is not None
    return str(row["search_id"])


def get_recent_searches(limit: int) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                search_id::text AS search_id,
                mode,
                seed_artists,
                recommendations,
                feedback_rating,
                created_at::text AS created_at
            FROM recommendation_searches
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def record_feedback(search_id: str, rating: int) -> bool:
    with connect() as connection:
        row = connection.execute(
            """
            UPDATE recommendation_searches
            SET feedback_rating = %s, feedback_created_at = now()
            WHERE search_id = %s
            RETURNING search_id
            """,
            (rating, search_id),
        ).fetchone()
    return row is not None


app = FastAPI(
    title="Open Artist Discovery Graph API",
    version="1.0.0",
    description="計算済みの推薦グラフをNeon PostgreSQLから返します。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def call_database(function: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except HTTPException:
        raise
    except (psycopg.Error, RuntimeError, ValueError) as error:
        raise HTTPException(
            status_code=503,
            detail="推薦DBへ接続できません。しばらくしてから再試行してください。",
        ) from error


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"name": "Open Artist Discovery Graph API", "docs": "/docs"}


@app.get("/health", tags=["system"])
def health() -> dict[str, Any]:
    version = call_database(data_version)
    return {"status": "ok", "active_model": version["model_version"]}


@app.get("/artists/search", response_model=list[ArtistSummary], tags=["artists"])
def artist_search(
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> list[dict[str, Any]]:
    if not q.strip():
        raise HTTPException(status_code=422, detail="検索文字を入力してください。")
    return call_database(search_artists, q.strip(), limit)


@app.get("/artists/{artist_mbid}/neighbors", response_model=list[Neighbor], tags=["artists"])
def artist_neighbors(
    artist_mbid: UUID,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[dict[str, Any]]:
    mbid = str(artist_mbid)
    if call_database(get_artist, mbid) is None:
        raise HTTPException(status_code=404, detail="アーティストが見つかりません。")
    return call_database(get_neighbors, mbid, limit)


@app.get("/artists/{artist_mbid}", response_model=ArtistDetail, tags=["artists"])
def artist_detail(artist_mbid: UUID) -> dict[str, Any]:
    artist = call_database(get_artist, str(artist_mbid))
    if artist is None:
        raise HTTPException(status_code=404, detail="アーティストが見つかりません。")
    return artist


@app.post(
    "/recommendations",
    response_model=RecommendationResponse,
    tags=["recommendations"],
)
def recommendations(payload: RecommendationRequest) -> dict[str, Any]:
    seed_mbids = list(dict.fromkeys(str(mbid) for mbid in payload.seed_artist_mbids))
    result = call_database(recommend, seed_mbids, mode=payload.mode, limit=payload.limit)
    result["search_id"] = call_database(record_search, seed_mbids, result)
    return result


@app.get(
    "/searches/recent",
    response_model=list[RecentSearch],
    tags=["feedback"],
)
def recent_searches(
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> list[dict[str, Any]]:
    return call_database(get_recent_searches, limit)


@app.post("/searches/{search_id}/feedback", tags=["feedback"])
def feedback(search_id: UUID, payload: FeedbackRequest) -> dict[str, bool]:
    saved = call_database(record_feedback, str(search_id), payload.rating)
    if not saved:
        raise HTTPException(status_code=404, detail="検索履歴が見つかりません。")
    return {"saved": True}


@app.get("/data-version", response_model=DataVersionResponse, tags=["system"])
def current_data_version() -> dict[str, Any]:
    return call_database(data_version)

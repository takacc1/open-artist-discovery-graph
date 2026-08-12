from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.serving_db import data_version, get_artist, get_neighbors, recommend, search_artists


DEFAULT_DATABASE = Path("reports/serving/artist_discovery.sqlite3")


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
    mode: Literal["near", "bridge", "adventure"]
    model_version: str
    seed_count: int
    missing_seed_mbids: list[str]
    recommendations: list[RecommendationItem]


class DataVersionResponse(BaseModel):
    model_version: str
    window_days: int
    generated_at: str
    artist_count: int
    source_artist_count: int
    edge_count: int


def configured_database() -> Path:
    return Path(os.environ.get("ARTIST_DISCOVERY_DB", str(DEFAULT_DATABASE)))


def configured_origins() -> list[str]:
    value = os.environ.get(
        "ARTIST_DISCOVERY_CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    )
    return [origin.strip() for origin in value.split(",") if origin.strip()]


def create_app(database: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="Open Artist Discovery Graph API",
        version="0.1.0",
        description=(
            "計算済みのアーティスト類似グラフから、検索・近傍・複数シード推薦を返します。"
            "リクエスト時に外部音楽APIは呼びません。"
        ),
    )
    app.state.database = database or configured_database()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=configured_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    def ready_database() -> Path:
        path = Path(app.state.database)
        if not path.is_file():
            raise HTTPException(
                status_code=503,
                detail="推薦DBが見つかりません。先にsrc.serving_dbで構築してください。",
            )
        return path

    def call_database(function: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return function(ready_database(), *args, **kwargs)
        except HTTPException:
            raise
        except (sqlite3.Error, ValueError) as error:
            raise HTTPException(
                status_code=503,
                detail="推薦DBを読み取れません。管理者に確認してください。",
            ) from error

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "name": "Open Artist Discovery Graph API",
            "docs": "/docs",
        }

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
        rows = call_database(search_artists, q.strip(), limit)
        return [
            {
                "mbid": row["mbid"],
                "name": row["name"],
                "artist_type": row["artist_type"],
                "area_code": row["area_code"],
            }
            for row in rows
        ]

    @app.get("/artists/{artist_mbid}/neighbors", response_model=list[Neighbor], tags=["artists"])
    def artist_neighbors(
        artist_mbid: UUID,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ) -> list[dict[str, Any]]:
        mbid = str(artist_mbid)
        if call_database(get_artist, mbid) is None:
            raise HTTPException(status_code=404, detail="アーティストが見つかりません。")
        return call_database(get_neighbors, mbid, limit=limit)

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
        return call_database(
            recommend,
            seed_mbids,
            mode=payload.mode,
            limit=payload.limit,
        )

    @app.get("/data-version", response_model=DataVersionResponse, tags=["system"])
    def current_data_version() -> dict[str, Any]:
        return call_database(data_version)

    return app


app = create_app()

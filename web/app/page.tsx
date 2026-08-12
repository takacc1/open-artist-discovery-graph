"use client";

import { useEffect, useMemo, useRef, useState } from "react";

type Artist = {
  mbid: string;
  name: string;
  artist_type?: string | null;
  area_code?: string | null;
};

type SeedScore = {
  seed_artist_mbid: string;
  seed_artist_name: string;
  similarity_score: number;
};

type Recommendation = {
  artist_mbid: string;
  artist_name: string;
  score: number;
  confidence: "high" | "medium" | "low";
  matched_seed_count: number;
  seed_scores: SeedScore[];
  recommendation_source: string;
  window_days: number;
  reason: string;
};

type Mode = "near" | "bridge" | "adventure";
type ApiState = "checking" | "connected" | "preview";
type FeedbackRating = 0 | 1 | 2;

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "https://open-artist-discovery-api.vercel.app";

const artists: Artist[] = [
  { mbid: "b51c672b-85e0-48fe-8648-470a2422229f", name: "aespa", artist_type: "Group", area_code: "KR" },
  { mbid: "b2f2216a-d7a9-4ce0-8b8f-f494d9a8c196", name: "IVE", artist_type: "Group", area_code: "KR" },
  { mbid: "8da127cc-c432-418f-b356-ef36210d82ac", name: "TWICE", artist_type: "Group", area_code: "KR" },
  { mbid: "bce172fc-51bb-43f7-9a25-b406a0a581d5", name: "ITZY", artist_type: "Group", area_code: "KR" },
  { mbid: "1ee37742-1e3d-4e61-84d2-bc85f4c1459a", name: "LE SSERAFIM", artist_type: "Group", area_code: "KR" },
  { mbid: "2d623e82-73e2-4179-9c44-e963980f2d58", name: "NMIXX", artist_type: "Group", area_code: "KR" },
  { mbid: "4f0cb3b7-6c06-4317-ae35-ddf3106a17ee", name: "Red Velvet", artist_type: "Group", area_code: "KR" },
  { mbid: "49204a7a-ed85-407a-828f-6fd46f1d8126", name: "NewJeans", artist_type: "Group", area_code: "KR" },
];

const modeOptions: Array<{
  id: Mode;
  label: string;
  description: string;
}> = [
  { id: "near", label: "近い", description: "よく似ている" },
  { id: "bridge", label: "橋渡し", description: "好みの中間" },
  { id: "adventure", label: "冒険", description: "少し意外" },
];

const previewArtists = artists.slice(3);
const previewScores = [0.808743, 0.776156, 0.772171, 0.767704, 0.75673];

function previewRecommendations(selected: Artist[]): Recommendation[] {
  return previewArtists.map((artist, index) => ({
    artist_mbid: artist.mbid,
    artist_name: artist.name,
    score: previewScores[index],
    confidence: "high",
    matched_seed_count: selected.length,
    seed_scores: selected.map((seed, seedIndex) => ({
      seed_artist_mbid: seed.mbid,
      seed_artist_name: seed.name,
      similarity_score: Math.max(0.42, previewScores[index] - 0.13 - seedIndex * 0.035),
    })),
    recommendation_source: "behavior_primary",
    window_days: 30,
    reason: `入力${selected.length}組との類似関係を確認`,
  }));
}

function confidenceLabel(confidence: Recommendation["confidence"]) {
  return confidence === "high" ? "高" : confidence === "medium" ? "中" : "低";
}

function sourceLabel(source: string) {
  if (source === "behavior_primary") return "聴取傾向";
  if (source === "behavior_metadata_blend") return "聴取＋メタデータ";
  if (source === "metadata_fallback") return "メタデータ補助";
  return "類似グラフ";
}

function modeLabel(mode: Mode) {
  return modeOptions.find((option) => option.id === mode)?.label ?? mode;
}

export default function Home() {
  const [selected, setSelected] = useState<Artist[]>([]);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Artist[]>([]);
  const [mode, setMode] = useState<Mode>("bridge");
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(false);
  const [apiState, setApiState] = useState<ApiState>("checking");
  const [notice, setNotice] = useState("");
  const [searchId, setSearchId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<FeedbackRating | null>(null);
  const [feedbackSaving, setFeedbackSaving] = useState(false);
  const resultsRef = useRef<HTMLElement>(null);

  const selectedIds = useMemo(() => new Set(selected.map((artist) => artist.mbid)), [selected]);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}/health`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("API unavailable");
        setApiState("connected");
      })
      .catch(() => setApiState("preview"));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (recommendations.length === 0) return;
    window.requestAnimationFrame(() => {
      resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, [recommendations.length]);

  useEffect(() => {
    const search = query.trim();
    if (search.length < 2) {
      setSearchResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(
          `${API_BASE}/artists/search?q=${encodeURIComponent(search)}&limit=8`,
          { signal: controller.signal },
        );
        if (!response.ok) throw new Error("Search failed");
        setSearchResults(await response.json());
        setApiState("connected");
      } catch {
        const normalized = search.toLocaleLowerCase();
        setSearchResults(
          artists.filter((artist) => artist.name.toLocaleLowerCase().includes(normalized)),
        );
      }
    }, 240);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [query]);

  function addArtist(artist: Artist) {
    if (selectedIds.has(artist.mbid) || selected.length >= 5) return;
    setSelected((current) => [...current, artist]);
    setQuery("");
    setSearchResults([]);
    setRecommendations([]);
    setSearchId(null);
    setFeedback(null);
  }

  function removeArtist(mbid: string) {
    setSelected((current) => current.filter((artist) => artist.mbid !== mbid));
    setRecommendations([]);
    setSearchId(null);
    setFeedback(null);
  }

  async function discover() {
    if (selected.length === 0) return;
    setLoading(true);
    setNotice("");
    try {
      const response = await fetch(`${API_BASE}/recommendations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          seed_artist_mbids: selected.map((artist) => artist.mbid),
          mode,
          limit: 10,
        }),
      });
      if (!response.ok) throw new Error("Recommendation failed");
      const payload = await response.json();
      setRecommendations(payload.recommendations);
      setSearchId(payload.search_id ?? null);
      setFeedback(null);
      setApiState("connected");
      setNotice(
        payload.recommendations.length
          ? ""
          : "候補がありませんでした。組み合わせを変えてみてください。",
      );
    } catch {
      setRecommendations(previewRecommendations(selected));
      setSearchId(null);
      setFeedback(null);
      setApiState("preview");
      setNotice("API未接続のため、実際のv4確認結果をプレビュー表示しています。");
    } finally {
      setLoading(false);
    }
  }

  async function submitFeedback(rating: FeedbackRating) {
    if (!searchId || feedbackSaving) return;
    setFeedbackSaving(true);
    try {
      const response = await fetch(`${API_BASE}/searches/${searchId}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating }),
      });
      if (!response.ok) throw new Error("Feedback failed");
      setFeedback(rating);
    } catch {
      setNotice("回答を保存できませんでした。もう一度お試しください。");
    } finally {
      setFeedbackSaving(false);
    }
  }

  return (
    <main>
      <header className="site-header">
        <a className="brand" href="#top" aria-label="Open Artist Discovery ホーム">
          Open Artist Discovery
        </a>
        <div className="header-right">
          <div className="header-meta">
            <span className={`status-dot ${apiState}`} />
            {apiState === "connected" ? "API LIVE" : apiState === "checking" ? "CHECKING" : "PREVIEW"}
          </div>
        </div>
      </header>

      <section className="hero" id="top">
        <div className="hero-shade" aria-hidden="true" />
        <div className="hero-copy">
          <h1>FIND YOUR<br /><em>NEXT SOUND</em></h1>
        </div>
        <div className="hero-foot">
          <a href="#discover">START <span aria-hidden="true" /></a>
        </div>
      </section>

      <section className="discovery-section" id="discover">
      <div className="discovery-shell" aria-label="推薦条件">
        <div className="step-panel artist-panel">
          <div className="step-heading">
            <span>01</span>
            <div><h2>好きなアーティスト</h2></div>
            <b>{selected.length} / 5</b>
          </div>

          <div className="selected-artists" aria-label="選択中のアーティスト">
            {selected.map((artist, index) => (
              <button key={artist.mbid} className="artist-chip" onClick={() => removeArtist(artist.mbid)}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                {artist.name}
                <i aria-hidden="true">×</i>
              </button>
            ))}
          </div>

          <div className="search-wrap">
            <label htmlFor="artist-search">追加する</label>
            <div className="search-field">
              <span aria-hidden="true">⌕</span>
              <input
                id="artist-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={selected.length >= 5 ? "5組選択済みです" : "例：ヨルシカ、NCT、IVE"}
                disabled={selected.length >= 5}
                autoComplete="off"
              />
            </div>
            {searchResults.length > 0 && (
              <div className="search-results" role="listbox" aria-label="検索結果">
                {searchResults.map((artist) => (
                  <button
                    key={artist.mbid}
                    onClick={() => addArtist(artist)}
                    disabled={selectedIds.has(artist.mbid)}
                  >
                    <span><strong>{artist.name}</strong><small>{artist.area_code ?? "--"} · {artist.artist_type ?? "Artist"}</small></span>
                    <b>{selectedIds.has(artist.mbid) ? "選択中" : "＋"}</b>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="step-panel mode-panel">
          <div className="step-heading">
            <span>02</span>
            <div><h2>探し方</h2></div>
          </div>
          <div className="mode-grid">
            {modeOptions.map((option) => (
              <button
                key={option.id}
                data-mode={option.id}
                className={mode === option.id ? "mode-card active" : "mode-card"}
                onClick={() => {
                  setMode(option.id);
                  setRecommendations([]);
                  setSearchId(null);
                  setFeedback(null);
                }}
                aria-pressed={mode === option.id}
              >
                <strong>{option.label}<span aria-hidden="true">→</span></strong>
                <p>{option.description}</p>
              </button>
            ))}
          </div>
        </div>

        <div className="action-row">
          <p>選んだ内容と回答は匿名で保存されます。</p>
          <button className="discover-button" onClick={discover} disabled={loading || selected.length === 0}>
            <span>{loading ? "探索中…" : "おすすめを探す"}</span>
            <b aria-hidden="true">↗</b>
          </button>
        </div>
      </div>
      </section>

      <section
        ref={resultsRef}
        className={recommendations.length ? "results-section visible" : "results-section"}
        aria-live="polite"
      >
        {recommendations.length > 0 && (
          <>
            <div className="results-heading">
              <div>
                <h2>おすすめ</h2>
                <p>{selected.map((artist) => artist.name).join(" × ")} · {modeLabel(mode)}</p>
              </div>
              <span>{recommendations.length}組</span>
            </div>
            {notice && <div className={apiState === "preview" ? "notice preview" : "notice"}>{notice}</div>}
            <div className="recommendation-list">
              {recommendations.map((item, index) => (
                <article className="recommendation-card" key={item.artist_mbid}>
                  <div className="rank">{String(index + 1).padStart(2, "0")}</div>
                  <div className="recommendation-main">
                    <div className="card-title-row">
                      <div>
                        <h3>{item.artist_name}</h3>
                      </div>
                      <div className="fit-score"><strong>{Math.round(item.score * 100)}</strong><span>適合度</span></div>
                    </div>
                    <div className="score-track"><i style={{ width: `${Math.max(8, item.score * 100)}%` }} /></div>
                    <div className="evidence-row">
                      <div className="confidence"><span className={`confidence-mark ${item.confidence}`} />信頼度 {confidenceLabel(item.confidence)}</div>
                    </div>
                    <details className="recommendation-details">
                      <summary>理由を見る</summary>
                      <p>{sourceLabel(item.recommendation_source)} · {item.reason}</p>
                      <div className="seed-scores">
                        {item.seed_scores.map((seed) => (
                          <span key={seed.seed_artist_mbid}>{seed.seed_artist_name}<b>{Math.round(seed.similarity_score * 100)}</b></span>
                        ))}
                      </div>
                    </details>
                  </div>
                </article>
              ))}
            </div>
            {searchId && (
              <div className="feedback-panel">
                <h3>おすすめはどうだった？</h3>
                <div>
                  {([
                    [2, "よかった"],
                    [1, "まあまあ"],
                    [0, "合わなかった"],
                  ] as const).map(([rating, label]) => (
                    <button
                      key={rating}
                      className={feedback === rating ? "selected" : ""}
                      onClick={() => submitFeedback(rating)}
                      disabled={feedbackSaving}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                {feedback !== null && <p>回答ありがとう。</p>}
              </div>
            )}
          </>
        )}
      </section>

      <footer>
        <div className="brand footer-brand">Open Artist Discovery</div>
        <p>Data: MusicBrainz · ListenBrainz · Wikidata<br />Photos: Unsplash</p>
        <a href="#top">TOP ↑</a>
      </footer>
    </main>
  );
}

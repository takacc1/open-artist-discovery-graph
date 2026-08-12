"use client";

import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import styles from "./admin.module.css";

type FeedbackRating = 0 | 1 | 2;
type Mode = "near" | "bridge" | "adventure";

type FeedbackSummary = {
  total_searches: number;
  answered_count: number;
  unanswered_count: number;
  good_count: number;
  okay_count: number;
  bad_count: number;
  good_rate: number;
  mode_counts: Record<Mode, number>;
};

type FeedbackEntry = {
  search_id: string;
  mode: Mode;
  model_version: string;
  seed_artists: Array<{ mbid: string; name: string }>;
  recommendations: Array<{ artist_mbid: string; artist_name: string; score: number }>;
  feedback_rating: FeedbackRating | null;
  created_at: string;
  feedback_created_at: string | null;
};

type FeedbackData = {
  summary: FeedbackSummary;
  entries: FeedbackEntry[];
};

type Filter = "all" | "answered" | "unanswered";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "https://open-artist-discovery-api.vercel.app";

const modeLabels: Record<Mode, string> = {
  near: "近い",
  bridge: "橋渡し",
  adventure: "冒険",
};

function ratingLabel(rating: FeedbackRating | null) {
  if (rating === 2) return "よかった";
  if (rating === 1) return "まあまあ";
  if (rating === 0) return "合わなかった";
  return "未回答";
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export default function AdminFeedbackPage() {
  const [password, setPassword] = useState("");
  const [adminToken, setAdminToken] = useState("");
  const [data, setData] = useState<FeedbackData | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const visibleEntries = useMemo(() => {
    if (!data) return [];
    if (filter === "answered") return data.entries.filter((entry) => entry.feedback_rating !== null);
    if (filter === "unanswered") return data.entries.filter((entry) => entry.feedback_rating === null);
    return data.entries;
  }, [data, filter]);

  async function loadFeedback(token: string) {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/admin/feedback?limit=200`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (!response.ok) {
        if (response.status === 401) throw new Error("管理パスワードが違います。");
        throw new Error("データを読み込めませんでした。少し待ってから再度お試しください。");
      }
      setData(await response.json());
      setAdminToken(token);
      setPassword("");
    } catch (reason) {
      setData(null);
      setAdminToken("");
      setError(reason instanceof Error ? reason.message : "読み込みに失敗しました。");
    } finally {
      setLoading(false);
    }
  }

  function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (password.trim()) void loadFeedback(password.trim());
  }

  if (!data) {
    return (
      <main className={styles.loginPage}>
        <a className={styles.brand} href="/">Open Artist Discovery</a>
        <form className={styles.loginCard} onSubmit={submitPassword}>
          <span>ADMIN</span>
          <h1>アンケート結果</h1>
          <p>管理パスワードを入力してください。</p>
          <label htmlFor="admin-password">管理パスワード</label>
          <input
            id="admin-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            autoFocus
          />
          {error && <p className={styles.error}>{error}</p>}
          <button type="submit" disabled={loading || !password.trim()}>
            {loading ? "確認中…" : "結果を見る"}
          </button>
        </form>
      </main>
    );
  }

  const summary = data.summary;
  const answered = Math.max(summary.answered_count, 1);
  const ratings = [
    { label: "よかった", count: summary.good_count, className: styles.good },
    { label: "まあまあ", count: summary.okay_count, className: styles.okay },
    { label: "合わなかった", count: summary.bad_count, className: styles.bad },
  ];

  return (
    <main className={styles.dashboard}>
      <header className={styles.header}>
        <div>
          <a className={styles.brand} href="/">Open Artist Discovery</a>
          <p>ADMIN DASHBOARD</p>
        </div>
        <button onClick={() => void loadFeedback(adminToken)} disabled={loading}>
          {loading ? "更新中…" : "更新"}
        </button>
      </header>

      <section className={styles.titleBlock}>
        <p>FEEDBACK OVERVIEW</p>
        <h1>アンケート結果</h1>
      </section>

      <section className={styles.statGrid} aria-label="集計結果">
        <article><span>全検索</span><strong>{summary.total_searches}</strong><small>件</small></article>
        <article><span>回答済み</span><strong>{summary.answered_count}</strong><small>件</small></article>
        <article><span>未回答</span><strong>{summary.unanswered_count}</strong><small>件</small></article>
        <article className={styles.highlight}><span>よかった率</span><strong>{summary.good_rate}</strong><small>%</small></article>
      </section>

      <section className={styles.breakdownGrid}>
        <article className={styles.panel}>
          <div className={styles.panelHeading}>
            <h2>回答の内訳</h2>
            <span>{summary.answered_count} ANSWERS</span>
          </div>
          <div className={styles.ratingBars}>
            {ratings.map((rating) => (
              <div key={rating.label}>
                <div><span>{rating.label}</span><b>{rating.count}件</b></div>
                <i><em className={rating.className} style={{ width: `${(rating.count / answered) * 100}%` }} /></i>
              </div>
            ))}
          </div>
        </article>

        <article className={styles.panel}>
          <div className={styles.panelHeading}>
            <h2>モード別検索</h2>
            <span>ALL SEARCHES</span>
          </div>
          <div className={styles.modeCounts}>
            {(Object.keys(modeLabels) as Mode[]).map((mode) => (
              <div key={mode}><span>{modeLabels[mode]}</span><strong>{summary.mode_counts[mode] ?? 0}</strong><small>件</small></div>
            ))}
          </div>
        </article>
      </section>

      <section className={styles.recordsSection}>
        <div className={styles.recordsHeading}>
          <div><p>RECENT SEARCHES</p><h2>検索ごとの回答</h2></div>
          <div className={styles.filters}>
            {(["all", "answered", "unanswered"] as Filter[]).map((value) => (
              <button key={value} className={filter === value ? styles.active : ""} onClick={() => setFilter(value)}>
                {value === "all" ? "すべて" : value === "answered" ? "回答済み" : "未回答"}
              </button>
            ))}
          </div>
        </div>

        <div className={styles.recordList}>
          {visibleEntries.map((entry) => (
            <article key={entry.search_id} className={styles.record}>
              <div className={styles.recordTop}>
                <span className={`${styles.ratingBadge} ${entry.feedback_rating === null ? styles.unanswered : ""}`}>
                  {ratingLabel(entry.feedback_rating)}
                </span>
                <time>{formatDate(entry.created_at)}</time>
                <small>{modeLabels[entry.mode]}</small>
              </div>
              <h3>{entry.seed_artists.map((artist) => artist.name).join(" × ")}</h3>
              <p>→ {entry.recommendations.slice(0, 5).map((artist) => artist.artist_name).join("、") || "候補なし"}</p>
              <details>
                <summary>推薦10組を見る</summary>
                <ol>
                  {entry.recommendations.map((artist) => (
                    <li key={artist.artist_mbid}><span>{artist.artist_name}</span><b>{Math.round(artist.score * 100)}</b></li>
                  ))}
                </ol>
                <small>モデル：{entry.model_version}</small>
              </details>
            </article>
          ))}
          {visibleEntries.length === 0 && <p className={styles.empty}>該当する記録はありません。</p>}
        </div>
      </section>
    </main>
  );
}

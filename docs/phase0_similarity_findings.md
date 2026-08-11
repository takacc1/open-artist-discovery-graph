# Phase 0 類似度試作の結果

## 今回の入力

- ListenBrainz公式Spark増分ダンプ: 2026-08-03〜2026-08-09の7日分
- ダンプ内listen: 28,543,932件
- Artist MBID付きlisten: 23,384,992件（81.9%）
- 集計できたユーザー: 29,056
- 集計できたアーティスト: 262,819
- 検証対象: `data/validation_artists.csv` の50組

通常のlistensダンプを使った最初の試作ではArtist MBID利用率が5.5%でした。Spark版の `artist_credit_mbids` を使うことで、名前による推測をせずに利用率を約82%まで改善できました。

生のユーザー名・ユーザーIDは保存していません。user IDは実行ごとの秘密鍵で匿名化し、最終結果にはアーティスト同士の集計値だけを出しています。

## 1日分との比較

| 指標 | 1日分 | 7日分 |
|---|---:|---:|
| listen行数 | 3,921,852 | 28,543,932 |
| Artist MBID付きlisten | 3,156,220 | 23,384,992 |
| 集計ユーザー | 18,473 | 29,056 |
| 集計アーティスト | 107,725 | 262,819 |
| 検証50組のリスナー集合 | 2,537 | 8,511 |
| 期間内リスナーあり | 49/50 | 50/50 |
| 推薦を出せた数 | 44/50 | 50/50 |
| `expected_similar` Top 10 hit | 22/50（44%） | 31/50（62%） |

`expected_similar` は完全な正解集合ではないため、62%はモデルの正解率ではありません。既知の候補を最低限再現できるかを見る診断値です。

## 信頼度と順位安定性

- high: 242件
- medium: 115件
- low: 143件
- 1日版と7日版のTop 10重なり率: 全体平均35%
- K-POPヨジャドル10組のTop 10重なり率: 平均70%

K-POPは短期間でも比較的安定しています。一方、betcover!!、BOØWY、cero、downy、Lamp、SUPERCARなどは1日版と7日版の上位候補が大きく変わりました。小規模アーティストの品質判断に1日分だけを使うべきではないことが確認できました。

## K-POPヨジャドル10組

10組すべてで上位10候補を出し、10組すべてで参考候補がTop 10に入りました。上位5候補は次のとおりです。

| 入力 | 7日分の上位候補 |
|---|---|
| aespa | IVE、LE SSERAFIM、NMIXX、ILLIT、Red Velvet |
| IVE | ITZY、aespa、NMIXX、STAYC、LE SSERAFIM |
| TWICE | NewJeans、ITZY、IVE、LE SSERAFIM、aespa |
| Red Velvet | aespa、NMIXX、TWICE、ITZY、STAYC |
| BLACKPINK | LISA、JENNIE、ROSÉ、TWICE、LE SSERAFIM |
| LE SSERAFIM | ILLIT、aespa、KATSEYE、NewJeans、IVE |
| NewJeans | TWICE、ILLIT、LE SSERAFIM、aespa、IVE |
| NMIXX | IVE、aespa、ITZY、fromis_9、KISS OF LIFE |
| ITZY | IVE、EVERGLOW、NMIXX、TWICE、Kep1er |
| (G)I-DLE | ITZY、aespa、IVE、KISS OF LIFE、K/DA |

## MBID監査の影響

50組を目視確認し、Aphex Twinの誤候補を修正しました。修正後はBoards of Canada、Autechre、Squarepusherなどが上位に入り、MusicBrainz候補取得率、ListenBrainzデータ取得率、同名誤結合の基準はすべて合格しました。詳細は `docs/identity_review.md` に記録しています。

## 次の評価

7日分でデータ取得、名寄せ、cosine＋shrinkageの技術検証は合格しました。K-POP候補100件を採点する `reports/kpop_recommendation_evaluation.csv` も生成できます。

`human_rating_0_1_2` に次を入力します。

- 2: かなり納得
- 1: 意外だがあり
- 0: 違う

採点後、Precision@10とNDCG@10を計算し、cosine＋shrinkage、セッション共起、implicit ALSを同じ正解データで比較します。

現段階の判定は「基礎データと計算方式はGO、公開用の推薦品質は人手評価待ち」です。

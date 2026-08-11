# Phase 0 類似度試作の結果

## 今回の入力

- ListenBrainz公式Spark増分ダンプ: 2026-08-09の1日分
- ダンプ内listen: 3,921,852件
- Artist MBID付きlisten: 3,156,220件（80.5%）
- 集計できたユーザー: 18,473
- 集計できたアーティスト: 107,725
- 検証対象: `data/validation_artists.csv` の50組

通常のlistensダンプではArtist MBID付きが217,023件（5.5%）でした。Spark版の `artist_credit_mbids` を使うことで、名前による推測をせずに利用率を大きく改善できました。

生のユーザー名・ユーザーIDは保存していません。user IDは実行ごとの秘密鍵で匿名化し、最終結果にはアーティスト同士の集計値だけを出しています。

## 全体結果

- 期間内にリスナーがいた検証アーティスト: 49/50
- 上位候補を出せた検証アーティスト: 44/50
- 手入力した `expected_similar` がTop 10に入った割合: 22/50（44.0%）
- 推薦を出せた44組だけで見た割合: 22/44（50.0%）
- 出力候補の信頼度: high 79件、medium 89件、low 255件

`expected_similar` は完全な正解集合ではないため、この割合はモデルの正解率ではありません。既知の候補を最低限再現できるかを見る診断値です。

## K-POPヨジャドル10組

10組すべてで上位10候補を出し、10組すべてで参考候補がTop 10に入りました。

| 入力 | 上位候補の例 | 共通リスナー |
|---|---|---:|
| aespa | IVE、LE SSERAFIM、TWICE、ITZY、ILLIT | 最大60 |
| IVE | TWICE、aespa、ITZY、NMIXX、NewJeans | 最大39 |
| TWICE | IVE、NewJeans、aespa、ITZY、BLACKPINK | 最大45 |
| Red Velvet | IVE、aespa、tripleS、TWICE、NewJeans | 最大46 |
| BLACKPINK | LISA、JENNIE、ROSÉ、JISOO、TWICE | 最大34 |
| LE SSERAFIM | ILLIT、KATSEYE、aespa、IVE、BABYMONSTER | 最大60 |
| NewJeans | ILLIT、TWICE、IVE、LE SSERAFIM、Red Velvet | 最大39 |
| NMIXX | IVE、aespa、ITZY、fromis_9、CHUNG HA | 最大38 |
| ITZY | IVE、TWICE、(G)I-DLE、aespa、CHUNG HA | 最大29 |
| (G)I-DLE | K/DA、Jaira Burns、ITZY、TWICE、aespa | 最大26 |

1日分でもK-POPの主要候補は `medium` または `high` が中心になりました。一方、DYGLは期間内のリスナーがなく、Homecomings、ゆらゆら帝国、フリッパーズ・ギター、muque、PAS TASTAは共通リスナー不足で候補を出せていません。

## MBID監査の影響

50組を目視確認し、Aphex Twinの誤候補を修正しました。修正後はBoards of Canada、Autechre、Squarepusherなどが上位に入り、ListenBrainzデータ被覆率も100%になりました。詳細は `docs/identity_review.md` に記録しています。

## 次の判断

データ取得、名寄せ、cosine＋shrinkageの技術検証は合格です。K-POPの初期結果も有望です。ただし公開品質を判断するには、次の検証が残ります。

1. 7日分で小規模アーティストの被覆率と順位安定性を確認
2. あなたの3段階評価でPrecision@10とNDCG@10を計算
3. 共通リスナー最低人数を2・5・10で比較
4. cosine＋shrinkageとセッション共起、implicit ALSを比較

現段階の判定は「基礎データと計算方式はGO、公開用の推薦品質判定は保留」です。

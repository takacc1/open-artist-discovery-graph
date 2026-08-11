# Phase 0 類似度試作の結果

## 今回の入力

- ListenBrainz公式増分ダンプ: 2026-08-09の1日分
- ダンプ内listen: 3,921,860件
- Artist MBID付きlisten: 217,023件（5.5%）
- 集計できたユーザー: 5,467
- 集計できたアーティスト: 29,077
- 検証対象: `data/validation_artists.csv` の50組

生のユーザー名・ユーザーIDは保存していません。user IDは実行ごとの秘密鍵で匿名化し、最終結果にはアーティスト同士の集計値だけを出しています。

## 全体結果

- 期間内にリスナーがいた検証アーティスト: 38/50
- 上位候補を出せた検証アーティスト: 31/50
- 手入力した `expected_similar` がTop 10に入った割合: 13/50（26.0%）
- 推薦を出せた31組だけで見た割合: 13/31（41.9%）

`expected_similar` は完全な正解集合ではないため、この割合はモデルの正解率ではありません。既知の候補を最低限再現できるかを見る診断値です。

## K-POPヨジャドル10組

10組すべてで上位10候補を出せました。参考候補がTop 10に入ったのは7/10です。

| 入力 | 上位で確認できた候補の例 | 期間内リスナー |
|---|---|---:|
| aespa | LE SSERAFIM、IVE、JISOO、ITZY | 16 |
| IVE | TWICE、STAYC、Red Velvet、aespa | 14 |
| TWICE | IVE、NAYEON、CHAEYOUNG、aespa | 19 |
| BLACKPINK | ROSÉ、JISOO、aespa、JENNIE | 26 |
| LE SSERAFIM | BABYMONSTER、XG、aespa、NewJeans | 15 |
| NewJeans | ILLIT、LE SSERAFIM、Hearts2Hearts | 15 |
| (G)I-DLE | Red Velvet、KISS OF LIFE、IVE、LE SSERAFIM | 10 |

方向性は妥当ですが、1日分では共通リスナー数が少なく、今回の候補はすべて `low` 信頼度です。特にリスナー4人のITZYなどは順位が不安定になりやすいため、公開用の品質判断にはまだ使えません。

## 次の判断

計算方法そのものは動き、K-POPでは期待した近傍がかなり出ました。一方、邦楽の小規模アーティストと信頼度の評価には1日分では不足しています。次は複数日分を同じ匿名化集計へ追加し、以下を比較します。

1. 7日分と30日分で候補順位が安定するか
2. 共通リスナー最低人数を2・5・10で変えた場合の被覆率
3. cosine＋shrinkageとセッション共起の比較
4. 50組のMBIDを人が最終確認した後の再評価

現段階の判定は「類似度計算の技術検証は成功、推薦品質の合格判定は保留」です。

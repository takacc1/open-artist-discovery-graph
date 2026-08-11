# 検証用50アーティストのMBID監査

`data/validation_artists.csv` の50組について、名前、国・地域、Person/Group、活動状況、曖昧さの説明、検索候補を照合し、`manual_mbid`へ固定しました。

## 結果

- 確認済み: 50/50
- 誤結合のまま残っている件数: 0
- MusicBrainz候補取得率: 100%
- ListenBrainzデータ取得率: 100%
- Go / No-Go: GO

## 特に同名確認が必要だったアーティスト

| 入力 | 固定したMusicBrainz登録 | 判断材料 |
|---|---|---|
| cero | [196b78c8-e4de-4440-93b8-db52b0af56cd](https://musicbrainz.org/artist/196b78c8-e4de-4440-93b8-db52b0af56cd) | Japanese rock band。Argentine band候補を除外 |
| Lamp | [8bff8d1d-2e87-4b97-9909-2bd3404e92b9](https://musicbrainz.org/artist/8bff8d1d-2e87-4b97-9909-2bd3404e92b9) | JP・Group・Japanese group |
| MONO | [ffe02aed-ef7e-4736-a186-c2f1dd55ce8d](https://musicbrainz.org/artist/ffe02aed-ef7e-4736-a186-c2f1dd55ce8d) | 日本のpost-rock band。英国の同名duoを除外 |
| Boris | [57652bf8-cfe8-42e7-b9a7-5572a7080d8d](https://musicbrainz.org/artist/57652bf8-cfe8-42e7-b9a7-5572a7080d8d) | JP・Group・Japanese experimental band |
| toe | [7ea8a523-b33d-4944-ad31-fad25a81d603](https://musicbrainz.org/artist/7ea8a523-b33d-4944-ad31-fad25a81d603) | JP・Group・Japanese math rock |
| downy | [f2bec217-8b84-42d4-9395-1421f1fa8a51](https://musicbrainz.org/artist/f2bec217-8b84-42d4-9395-1421f1fa8a51) | JP・Group。Personの同名候補を除外 |
| Aphex Twin | [f22942a1-6f70-4f48-866e-238cb2308fbd](https://musicbrainz.org/artist/f22942a1-6f70-4f48-866e-238cb2308fbd) | GB・Person、WarpやSpotify、Wikidata等との関連を確認 |

## 修正した誤候補

Aphex Twinは自動検索で `65016d2c-b183-405b-91a4-275e6b7b5600` が選ばれていましたが、この登録には外部関連がなく、活動開始情報も本人と一致しませんでした。本体の `f22942a1-6f70-4f48-866e-238cb2308fbd` に修正しています。

今後の再検索で候補順位が変わっても同じ本人を使えるよう、確認済みMBIDを `manual_mbid` に保存し、取得処理はその値を優先します。

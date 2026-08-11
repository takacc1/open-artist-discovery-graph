# Open Artist Discovery Graph — Phase 0

画面開発の前に、50組の代表アーティストでデータ被覆率と同名誤結合を検証するための最小構成です。

## 現在の成果物

- `data/validation_artists.csv`: 検証用50組、期待する近傍、名寄せ用ヒント（K-POPヨジャドル10組を含む）
- `data/source_fields.csv`: MVPで使うフィールドと使わないフィールドの台帳
- `notebooks/01_data_feasibility.ipynb`: MusicBrainz、ListenBrainz、Wikidataの被覆率検証
- `src/feasibility.py`: NotebookからもCLIからも使える再実行可能な取得処理
- `tests/test_feasibility.py`: 50組の一意性と同名候補のスコアリング検査

## 実行方法

Python 3.11以上を想定しています。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export PROJECT_CONTACT="your-email@example.com"
python -m src.feasibility
```

MusicBrainzの利用方針に合わせ、`PROJECT_CONTACT`に連絡可能なメールアドレスまたはURLを指定します。50件のMusicBrainz検索は1秒以上の間隔を置くため、約1分かかります。

ListenBrainzは公開統計APIを匿名で読み取り、ログインやUser Tokenは使いません。APIのレート制限ヘッダーに従い、429/503では待機して再試行します。取得結果は `reports/cache/` に保存し、再実行時に同じAPIを不要に呼びません。

出力は次の3ファイルです。

- `reports/artist_coverage.csv`
- `reports/coverage_summary.json`
- `reports/coverage_summary.md`

## 同名誤結合の確認

自動検索で得たMBIDは、人が確認するまで本人と断定しません。`reports/artist_coverage.csv`の次の列を確認します。

- `resolved_mbid`
- `resolved_name`
- `resolved_country`
- `resolved_type`
- `resolved_disambiguation`
- `top_candidates`

確認後、`data/validation_artists.csv`の `manual_identity_status` を `confirmed` または `incorrect` に更新し、再実行します。50件すべてが確認済みになるまで、Go / No-Goは `PENDING_MANUAL_IDENTITY_REVIEW` のままです。

## プライバシー上の制限

ListenBrainzのリスナー統計APIはユーザー名を返しますが、本処理はそれを保存しません。保存するのはデータ有無、総再生数、返却された上位リスナー数だけです。

## テスト

```bash
python -m unittest discover -s tests -v
```

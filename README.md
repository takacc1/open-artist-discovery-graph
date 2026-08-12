# Open Artist Discovery Graph

50組の基礎検証、15組のクロスジャンル検証、26組の若者向け人気アーティスト検証から、推薦DB・HTTP API・Web MVPまでを一つにしたプロジェクトです。

## 現在の成果物

- `data/validation_artists.csv`: 基礎50組、クロスジャンル15組、若者向け人気26組の期待近傍と名寄せ用ヒント
- `data/source_fields.csv`: MVPで使うフィールドと使わないフィールドの台帳
- `notebooks/01_data_feasibility.ipynb`: MusicBrainz、ListenBrainz、Wikidataの被覆率検証
- `src/feasibility.py`: NotebookからもCLIからも使える再実行可能な取得処理
- `tests/test_feasibility.py`: 90組の一意性、検証枠、同名候補のスコアリング検査
- `src/similarity.py`: ListenBrainzダンプからcosine類似度＋shrinkageを計算
- `src/listenbrainz_window.py`: 公式ミラーから連続した日次Spark増分を選び、SHA-256検証付きで30日窓を保存
- `src/recommendation_fallback.py`: 30日を基本に、低データ時は90日、未計算時はオンデマンド計算へ振り分け
- `src/metadata_fallback.py`: MusicBrainz／Wikidataメタデータを取得し、30人未満だけ行動類似度と混合
- `src/metadata_fallback_metrics.py`: 拡張メタデータ補助の80候補を0・1・2で集計し、方式別の弱点を確認
- `src/metadata_evidence_metrics.py`: v3/v4の共通証拠ルールを再集計し、安全停止を含めて合否判定
- `src/serving_db.py`: アーティストとTop候補をグラフ形式で保存し、検索・単一近傍・複数シード推薦を実行
- `src/window_revaluation.py`: 期間を広げたTop 10と前回評価を照合し、新候補だけの再評価表を生成
- `src/window_revaluation_metrics.py`: 30日版260候補の採点を集計し、7日版との差とデータ量別品質を出力
- `notebooks/02_similarity_prototype.ipynb`: 類似度計算を再実行するNotebook入口
- `tests/test_similarity.py`: MBID抽出、log変換、信頼度区分の検査
- `src/evaluation.py`: K-POP候補を人が0・1・2で採点するCSVを生成
- `tests/test_evaluation.py`: 評価対象カテゴリと空の採点欄を検査
- `src/evaluation_metrics.py`: 採点結果からPrecision@5/10とNDCG@10を集計
- `src/algorithm_comparison.py`: 同じ採点済み候補をcosine、30分セッション共起、implicit ALSで再順位付け
- `src/full_retrieval.py`: 3方式が独自に取得したTop 10を統合し、方式名を隠した追加採点表を生成
- `src/full_retrieval_metrics.py`: 独自Top 10と採点済み和集合を照合し、方式別のPrecisionとNDCGを集計
- `src/cross_genre_evaluation.py`: クロスジャンル15組の順位を隠した採点表と照合キーを生成
- `src/cross_genre_metrics.py`: 採点済み150候補を元順位へ戻し、全体・ジャンル別・データ量別に集計
- `src/youth_popular_evaluation.py`: 若者向け人気26組の順位を隠した採点表と照合キーを生成
- `src/youth_popular_metrics.py`: 採点済み220候補を元順位へ戻し、市場・ジャンル・データ量別に集計
- `data/*human_ratings.csv`: 人手評価のローカル入力。個人の音楽嗜好と自由記述を含むためGitHubには公開しない
- `docs/phase0_similarity_findings.md`: 実データでの初回結果と次の判断
- `docs/identity_review.md`: 50組のMBID監査と同名候補の判断記録
- `web/`: アーティスト選択、3モード、推薦理由・適合度・信頼度を表示するWeb MVP

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

## 類似度の試作

MusicBrainzの名寄せ結果 `reports/artist_coverage.csv` と、ListenBrainz公式のSpark増分ダンプを使います。Spark版にはListenBrainz側で対応付けられた `artist_credit_mbids` があり、名前だけでArtist MBIDを推測せずに利用率を上げられます。ダンプはプロジェクト外に置き、次のように実行します。

```bash
python -m src.similarity \
  --spark-archive /path/to/day-1-spark-dump.tar /path/to/day-2-spark-dump.tar
```

公式ミラーから直近の連続30日を取得して再計算する場合は次を使います。同じ日付のアーカイブは再利用し、公式SHA-256と一致しないファイルは採用しません。

```bash
python -m src.listenbrainz_window \
  --days 30 \
  --output-dir /path/to/listenbrainz-spark-30d

python -m src.similarity \
  --spark-manifest /path/to/listenbrainz-spark-30d/manifest.json \
  --coverage reports/artist_coverage.csv \
  --report-dir reports/30d \
  --work-db /path/to/listenbrainz_artist_similarity_30d.sqlite3 \
  --limit 50
```

処理は、再生数をユーザー×Artist MBIDで集計し、100回を上限に `log(1 + count)` へ変換します。そのベクトルのcosine類似度に `共通リスナー数 / (共通リスナー数 + 10)` を掛け、少人数だけで一致した候補を下げます。

出力は次の3ファイルです。

- `reports/similarity_top10.csv`: 50組ごとの上位10候補、理由、類似度、信頼度
- `reports/similarity_summary.json`: 全数値とアーティスト別の検証結果
- `reports/similarity_summary.md`: 読みやすい要約

`expected_similar` はAPIから得た正解ではなく、結果評価用にこちらで手入力した参考候補です。Top 10に含まれた割合を診断値として出します。

## 30日・90日のフォールバック

本番では30日分の行動類似度を基本にし、入力アーティストのリスナーが30人未満、または候補を生成できない場合だけ90日分へ切り替えます。90日でも候補がなければ、行動類似度と同じ得点として扱わず、MusicBrainz／Wikidataを使う `metadata_fallback` として分けます。

2026-08-12時点の公式日次増分ミラーは30日分を保持しており、今回すぐ作れた最長窓は2026-07-13〜2026-08-11です。直近の全量Sparkダンプは約191GBでローカル空き容量を超えるため、90日版は未作成です。今後は日次増分を保存して60日・90日へ伸ばし、それまでは30日で30人未満のアーティストを `metadata_fallback` 対象にします。

30日版と90日版をそれぞれ計算した後、次で公開用の結果を選択できます。

```bash
python -m src.recommendation_fallback \
  --primary-rows reports/30d/similarity_top50.csv \
  --primary-summary reports/30d/similarity_summary.json \
  --extended-rows reports/90d/similarity_top50.csv \
  --extended-summary reports/90d/similarity_summary.json \
  --output-dir reports/serving
```

90日分がたまるまでの低データ対策は、次の2段階で再実行できます。

```bash
python -m src.metadata_fallback fetch \
  --input data/validation_artists.csv \
  --database reports/serving/artist_discovery.sqlite3 \
  --behavior-summary reports/30d/similarity_summary.json \
  --output reports/metadata/serving_catalog_features.json \
  --contact https://github.com/takacc1/open-artist-discovery-graph

python -m src.metadata_fallback blend \
  --behavior-rows reports/30d/similarity_top50.csv \
  --behavior-summary reports/30d/similarity_summary.json \
  --metadata reports/metadata/serving_catalog_features.json \
  --output-dir reports/serving_metadata_evidence_v4 \
  --max-broad-metadata-only-top10 0 \
  --min-behavior-only-common-listeners 3 \
  --exclude-artists data/known_artists_local.csv
```

`data/known_artists_local.csv` は `artist_name,canonical_name` のローカル除外表です。個人の嗜好にあたるためGit管理せず、低データの入力だけで行動候補とメタデータ候補の両方から除外します。データが十分な入力の通常ランキングは変更しません。

補助点はWikidataジャンル45%、検証用の自前ジャンル20%、MusicBrainzの直接関係12%、国8%、開始年8%、種別7%です。国・年代・種別だけでは推薦候補にせず、ジャンル一致または直接関係を必須にします。MusicBrainzの補助データであるgenres/tagsはライセンス方針に合わせて使いません。30人未満ではリスナー数に応じて行動点の重みを15〜80%へ変えます。

人手評価で見つかった単独方式の弱さには、アーティスト名ごとの例外ではなく全候補共通の証拠条件を使います。v4ではpop・rock・J-popなど広いジャンルだけが一致したメタデータ単独候補をTop 10へ入れません。直接関係、管理したジャンル、自動取得した具体的なジャンルのいずれかを必須にし、広いジャンル・国・年代・種別は補助証拠としてだけ使います。聴取データだけの候補は共通リスナー3人以上を必須とし、3人未満なら強いメタデータ証拠との組合せが必要です。条件を通る候補が10件未満なら、弱い候補で埋めず件数を減らします。Wikidataは100 MBIDずつ分割取得してキャッシュし、MusicBrainzの個別照会は低データ入力に絞ります。

現在の候補プールは公開用DB内の1,978組です。うち1,676組にWikidata項目、1,422組にジャンルがあり、30人未満の若者向け8組とDYGLの計9組すべてに補助候補を生成できました。若者向け8組では、行動Top 50に含まれた既知候補45件を除外し、メタデータ候補にも同じ除外表を適用しています。

最初の80候補の人手評価は、評価2が35件、評価1が31件、評価0が14件でした。緩いP@10は82.5%、厳しいP@5は57.5%、NDCG@10は88.6%で合格しましたが、評価0率17.5%が10%未満の基準を超えたため総合判定は `REVIEW` です。聴取とメタデータの混合は緩い精度92.3%・評価0率7.7%だった一方、メタデータ単独は72.4%・27.6%、少人数の聴取データ単独は75.0%・25.0%でした。

共通ルール適用後は8組の上位候補が80件から76件になり、7組はTop 10を生成、ヤングスキニーは条件を通った6件だけを返しました。前回評価48件を引き継ぎ、新規28件も人手評価した結果、評価2が36件、評価1が32件、評価0が8件です。候補充足率95.0%、固定枠の緩いP@10は85.0%、返した候補内の緩い精度は89.5%、厳しいP@5は47.5%、NDCG@10は85.6%でした。評価0は前回の14件から8件へ減りましたが、評価0率10.5%が10%未満の基準を0.5ポイント超えたため総合判定は `REVIEW` のままです。新モデルは有効化しません。

v4ではv3候補9件を除外し、そのうち4件は評価0でした。v3評価67件を引き継ぎ、新規7件も人手評価した結果、評価2が39件、評価1が31件、評価0が4件です。候補充足率92.5%、固定枠の緩いP@10は87.5%、返した候補内の緩い精度は94.6%、厳しいP@5は50.0%、NDCG@10は90.4%、評価0率は5.4%でした。4つのMVP基準をすべて満たして `PASS` となったため、v4を有効モデルに切り替えました。7組はTop 10を返し、ヤングスキニーは弱い候補で埋めず4件だけを返します。

共通ルール版76候補は次で再集計できます。個人の採点とメモはローカルCSVのままGit管理しません。

```bash
python -m src.metadata_evidence_metrics --profile v4
```

80候補は次で再集計できます。個人の採点とメモはローカルCSVのままGit管理しません。

```bash
python -m src.metadata_fallback_metrics
```

保存される辺は「サカナクションなら常にくるり」という固定ルールではなく、特定の集計期間・モデル版で計算したTop 50のキャッシュです。モデル更新時に順位を入れ替えます。未計算アーティストがリクエストされた場合は、MusicBrainzでMBIDを解決して計算待ちへ入れ、データがあれば30日または90日でオンデマンド計算し、なければメタデータ推薦またはデータ不足を返します。

## 推薦用グラフDB

`src.serving_db` は、アーティストを点、Top候補を有向辺としてSQLiteへ保存するPhase 1用のローカル実装です。本番のPostgreSQLへ移しやすいよう、アーティスト本体、類似辺、モデル版、入力ファイルのチェックサムを別テーブルにしています。個人の聴取履歴は保存しません。

現在の有効モデルである30日版は次で再構築できます。

```bash
python -m src.serving_db build \
  --database reports/serving/artist_discovery.sqlite3 \
  --coverage reports/artist_coverage.csv \
  --similarity reports/serving_metadata_evidence_v4/similarity_top50.csv \
  --model-version cosine-shrinkage-specific-evidence-30d-v4 \
  --window-days 30
```

現在のv4は90入力アーティストを収録し、弱い候補を安全停止で除いた4,454辺を有効モデルとして保存します。DBは行動点、メタデータ点、関係点、信頼度に加え、`edge_evidence`へ共通リスナー数とメタデータ根拠を分離保存します。

共通の証拠条件を入れた `cosine-shrinkage-evidence-rules-30d-v3` は4,456辺・根拠4,545件を非アクティブで保存しています。v3は人手評価を完了しましたが判定が `REVIEW` のため有効化していません。

広いジャンルだけのメタデータ単独候補を除外した `cosine-shrinkage-specific-evidence-30d-v4` は、4,454辺・根拠4,544件を保存し、人手評価 `PASS` 後に有効化しました。v2とv3はロールバック用に残るため、ローカルSQLite全体は1,978アーティスト・23,270辺・根拠18,189件です。DBファイルは生成物としてGit管理しません。既に保存済みのモデルを再取込せず切り替える場合は、`python -m src.serving_db activate --database reports/serving/artist_discovery.sqlite3 --model-version cosine-shrinkage-specific-evidence-30d-v4` を使います。

若者向け26組の30日版Top 10は、前回と同じ113候補の評価を引き継ぎ、新候補147件を追加採点しました。集計は次で再実行できます。

```bash
python -m src.window_revaluation_metrics
```

260候補すべての結果は、推薦生成率100%、緩いP@10 91.5%、厳しいP@10 52.7%、厳しいP@5 63.1%、NDCG@10 89.9%、評価0率8.5%で、4つのMVP基準を満たし `PASS` です。7日版より40候補増えて生成率が15.4ポイント改善し、厳しいP@5も7.6ポイント上がりました。

ただし、30日でもリスナー30人未満の8組は、緩いP@10 76.3%、評価0率23.8%で基準未達です。100人以上の13組は緩いP@10 100%、評価0件でした。この差から、30人未満は低信頼表示とMusicBrainz／Wikidata補完へ回します。

```bash
# 名前検索
python -m src.serving_db search \
  --database reports/serving/artist_discovery.sqlite3 \
  --query サカナクション

# 単一アーティストの近傍
python -m src.serving_db neighbors \
  --database reports/serving/artist_discovery.sqlite3 \
  --mbid 01830cc1-8a04-4dfb-9e4a-d557dfda6a93

# 3組を統合した「橋渡し」推薦
python -m src.serving_db recommend \
  --database reports/serving/artist_discovery.sqlite3 \
  --seed-mbid 01830cc1-8a04-4dfb-9e4a-d557dfda6a93 \
  --seed-mbid dfc6a151-3792-4695-8fda-f64723eaa788 \
  --seed-mbid 338f5d97-3133-4bf8-a58e-068ff9b5405d \
  --mode bridge
```

現在のモード別統合式はAPI接続を確認するための初期実装です。`near`は最も強い類似関係、`bridge`は複数シードへの接続率、`adventure`は信頼度を保ちながら近すぎない候補を加点します。モード別品質はWeb公開前に別途人手評価します。

## 推薦HTTP API

`src.api` は、画面と推薦DBをつなぐFastAPI製のHTTP窓口です。起動後は画面側がアーティスト名やMBIDを送り、JSONで検索結果・推薦理由・スコア・信頼度を受け取れます。リクエスト時にMusicBrainzやListenBrainzは呼ばず、有効化済みDBだけを読みます。

公開版はNeon PostgreSQLへ1,978アーティスト・23,270辺・18,189根拠を移行し、Vercel上の `https://open-artist-discovery-api.vercel.app` から読み出します。Web画面は `https://nextsound-jp.vercel.app` で公開しています。ローカルSQLiteは再計算と移行元のバックアップとして残し、公開時に開発用パソコンをサーバーとして使いません。Vercel用APIは `api_service/`、SQLiteからPostgreSQLへの移行処理は `src/migrate_sqlite_to_postgres.py` にあります。

```bash
source .venv/bin/activate
uvicorn src.api:app --reload --host 127.0.0.1 --port 8000
```

ローカル起動後は `http://127.0.0.1:8000/docs`、公開版は `https://open-artist-discovery-api.vercel.app/docs` で全APIを試せます。

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/health` | APIと推薦DBが利用可能か確認 |
| GET | `/artists/search?q=aespa` | 名前でアーティストを検索 |
| GET | `/artists/{mbid}` | アーティスト基本情報を取得 |
| GET | `/artists/{mbid}/neighbors` | 1組の近いアーティストを取得 |
| POST | `/recommendations` | 1〜5組を統合して推薦し、匿名の検索記録を保存 |
| GET | `/searches/recent` | 最近の匿名検索と推薦上位3組を取得 |
| POST | `/searches/{search_id}/feedback` | 推薦への3段階評価（0〜2）を保存 |
| GET | `/data-version` | 有効モデル・期間・辺数を取得 |

複数シード推薦の入力例です。

```json
{
  "seed_artist_mbids": [
    "b51c672b-85e0-48fe-8648-470a2422229f",
    "b2f2216a-d7a9-4ce0-8b8f-f494d9a8c196",
    "8da127cc-c432-418f-b356-ef36210d82ac"
  ],
  "mode": "bridge",
  "limit": 10
}
```

この例はaespa・IVE・TWICEの3組に共通してつながる候補を返します。実データの動作確認では、ITZY、LE SSERAFIM、NMIXX、Red Velvet、NewJeansが上位5組でした。MBID形式、入力1〜5組、取得件数1〜50をAPI側で検証し、存在しないMBIDは `missing_seed_mbids` で返します。DBの場所は `ARTIST_DISCOVERY_DB`、画面の許可元は `ARTIST_DISCOVERY_CORS_ORIGINS` 環境変数で変更できます。

公開画面からの検索では、選択済みアーティスト、モード、推薦結果、任意の3段階評価だけを保存します。氏名、メールアドレス、入力途中の検索語、個人の聴取履歴は保存しません。

K-POPヨジャドル10組の候補を人が評価するシートは次で作成します。

```bash
python -m src.evaluation
```

`reports/kpop_recommendation_evaluation.csv` の `human_rating_0_1_2` に、`2=かなり納得`、`1=意外だがあり`、`0=違う`を入力します。

採点後の集計は次で実行します。

```bash
python -m src.evaluation_metrics
```

3方式の比較には、類似度計算で作った匿名化済み作業DBと同じ7日分のSparkダンプを使います。

```bash
python -m src.algorithm_comparison \
  --spark-archive /path/to/day-1.tar /path/to/day-2.tar /path/to/day-3.tar
```

比較は、採点済み100候補を各方式で再順位付けする実験です。30分以上の空白でセッションを区切り、共起cosineに少数セッション向けshrinkageを掛けます。ALSは64因子、15反復、正則化0.1、alpha 20、乱数seed 42を既定値にしています。

出力は次の3ファイルです。

- `reports/kpop_algorithm_comparison.csv`: 候補ごとの3方式のスコアと順位
- `reports/kpop_algorithm_comparison.json`: 方式別指標、実行条件、bootstrap結果
- `reports/kpop_algorithm_comparison.md`: 読みやすい比較表

同じ候補集合なのでPrecision@10は方式間で変わりません。順位差はPrecision@5とNDCG@10で判断します。完全な検索性能比較には、各方式が独自に取得したTop 10の和集合を追加採点します。

独自Top 10の和集合を作る完全比較の準備は次で実行します。

```bash
python -m src.full_retrieval \
  --spark-archive /path/to/day-1.tar /path/to/day-2.tar /path/to/day-3.tar
```

出力は次の3ファイルです。

- `reports/kpop_full_retrieval_evaluation.csv`: 方式名と順位を隠した採点表
- `reports/kpop_full_retrieval_key.csv`: 採点後に照合する方式別順位とスコア
- `reports/kpop_full_retrieval_summary.json`: 候補数、重複率、実行条件

採点表は候補順を入力アーティストごとに固定乱数で混ぜ、以前の採点を自動的に引き継ぎます。`needs_rating=yes`の行だけ追加採点します。

採点済みデータを `data/kpop_full_retrieval_human_ratings.csv` に保存した後、完全比較は次で実行します。

```bash
python -m src.full_retrieval_metrics
```

今回の結果は、cosine＋shrinkageが厳しいPrecision@5 58%、Precision@10 46%、NDCG@10 87.75%、セッション共起が46%、43%、88.43%、implicit ALSが36%、38%、86.67%でした。セッション共起のNDCG差はcosine比+0.67ポイントですが、bootstrap 95% CIは-2.36〜+3.94ポイントで優位とはいえません。上位の強い候補率、計算量、実装の単純さを合わせ、MVPにはcosine＋shrinkageを採用します。

## クロスジャンル15組の追加検証

ユーザーが実際に聴くアーティストから、ポップ4、ロック／オルタナ2、ヒップホップ2、R&B2、電子音楽2、クラシック／劇伴2、ジャズ1を追加しました。日本5組、海外10組です。

MusicBrainzのMBIDは15/15を確認して固定し、ListenBrainzデータも15/15で取得できました。既存の匿名集計済み7日DBから再計算すると、15組すべてにTop 10を出力できました。

```bash
python -m src.similarity \
  --reuse-work-db \
  --work-db /private/tmp/listenbrainz_artist_similarity.sqlite3 \
  --coverage reports/artist_coverage.csv \
  --report-dir reports/cross_genre \
  --dump-stats-json reports/similarity_summary.json \
  --category cross_genre_validation

python -m src.evaluation \
  --similarity reports/cross_genre/similarity_top10.csv \
  --output reports/cross_genre/recommendation_evaluation.csv \
  --category cross_genre_validation

python -m src.cross_genre_evaluation
```

採点表は150候補の順位・類似度・共通リスナー数を隠し、入力アーティストごとに固定乱数で並べ替えています。採点済みデータは `data/cross_genre_human_ratings.csv` に保存し、次で元順位へ戻して集計します。

```bash
python -m src.cross_genre_metrics
```

150件すべてを採点し、評価2が74件、評価1が70件、評価0が6件でした。緩いPrecision@10は96.0%、厳しいPrecision@10は49.3%、厳しいPrecision@5は56.0%、NDCG@10は91.7%です。事前基準の「緩いP@10 80%以上」「厳しいP@5 40%以上」「評価0率10%未満」をすべて満たしたため、クロスジャンルでもMVP成立判定は `PASS` です。

ジャンル別では電子音楽・R&B・ポップが強く、クラシック／劇伴は厳しいP@5が30%でした。特に久石譲は評価0が3件あり、作曲家・演奏家・オーケストラの候補タイプ制御が改善点です。低データ群はMichel CamiloとSTUTSの2組だけなので、データ量による差はまだ一般化しません。

## 若者向け人気26組の追加検証

ユーザー指定のJ-POP／ロック19組とK-POP7組を `youth_popular_validation` として追加しました。MusicBrainzは26/26を本人確認してMBIDを固定し、ListenBrainz統計APIでも26/26に過去データがあり、同名誤結合は0件です。

既存の7日分匿名DBで類似度を計算すると、24/26組に期間内リスナーがあり、22/26組でTop 10を作れました。3Houseとヤングスキニーは期間内0人、マルシィは1人、おいしくるメロンパンは2人だったため、少人数だけの一致を推薦しない条件により候補を出していません。残る22組・220候補を盲検採点対象にしています。

```bash
python -m src.similarity \
  --reuse-work-db \
  --work-db /private/tmp/listenbrainz_artist_similarity.sqlite3 \
  --coverage reports/artist_coverage.csv \
  --report-dir reports/youth_popular \
  --dump-stats-json reports/similarity_summary.json \
  --category youth_popular_validation

python -m src.evaluation \
  --similarity reports/youth_popular/similarity_top10.csv \
  --output reports/youth_popular/recommendation_evaluation.csv \
  --category youth_popular_validation

python -m src.youth_popular_evaluation

python -m src.youth_popular_metrics
```

手入力した参考候補のTop 10 hit率は全26組基準で50.0%です。これは正解率ではなく、既知の候補を最低限再現できたかを見る診断値です。

220候補をすべて0・1・2で盲検採点した結果、評価2が101件、評価1が107件、評価0が12件でした。推薦生成率84.6%、緩いPrecision@10は94.5%、厳しいPrecision@10は45.9%、厳しいPrecision@5は55.5%、NDCG@10は91.8%、評価0率は5.5%です。事前に定めた4基準をすべて満たし、若者向け公開対象でもMVP成立判定は `PASS` です。

K-POP7組は緩いP@10が100%、厳しいP@5が74.3%、評価0が0件でした。国内15組は緩いP@10が92.0%、厳しいP@5が46.7%、評価0率が8.0%です。一方、7日間リスナー30人未満の群は評価0率12.2%まで悪化したため、公開時は期間延長またはメタデータによるフォールバックを入れます。Top 10を作れなかった3House、ヤングスキニー、マルシィ、おいしくるメロンパンの4組は、精度指標へ無理に含めず推薦生成率として別評価しています。

## 同名誤結合の確認

自動検索で得たMBIDは、人が確認するまで本人と断定しません。現在の90組はすべて確認し、`data/validation_artists.csv` の `manual_mbid` に固定済みです。基礎50組の詳細監査は `docs/identity_review.md` にあり、追加枠も同じ列で確認しています。

- `resolved_mbid`
- `resolved_name`
- `resolved_country`
- `resolved_type`
- `resolved_disambiguation`
- `top_candidates`

確認後、`manual_identity_status` を `confirmed` または `incorrect` に更新します。固定した `manual_mbid` は再検索で順位が変わっても優先されます。今回の結果は50/50確認済み、同名誤結合0件、判定 `GO` です。

## プライバシー上の制限

ListenBrainzのリスナー統計APIはユーザー名を返しますが、被覆率検証はそれを保存しません。類似度計算でもダンプ内のユーザー名は読み取らず、数値IDは実行ごとの秘密鍵で直ちに匿名化します。作業DBと最終出力のどちらにも生のユーザー名・ユーザーIDは保存せず、最終出力には個人別履歴も残しません。共通リスナー数が2人未満の組み合わせも出力しません。

## テスト

```bash
python -m unittest discover -s tests -v
```

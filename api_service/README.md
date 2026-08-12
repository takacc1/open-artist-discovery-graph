# Recommendation API for Vercel

Neon PostgreSQLへ保存した計算済み推薦グラフを読み取り、検索・近傍・複数シード推薦を返すFastAPIです。推薦時には匿名の検索内容を保存し、3段階アンケートにも対応します。

必要な環境変数は次の3つです。

- `DATABASE_URL`: Neonのpooled PostgreSQL接続文字列
- `ARTIST_DISCOVERY_CORS_ORIGINS`: 許可する画面URLをカンマ区切りで指定
- `ADMIN_DASHBOARD_TOKEN`: `/admin/feedback` を見るための管理パスワード

個人の聴取履歴、氏名、メールアドレス、入力途中の検索語は保存しません。保存する利用データは、選択済みアーティスト、モード、推薦結果、任意の3段階評価だけです。

選択アーティストに出方向の辺がない場合だけ、同じ類似グラフの入方向の辺を逆向きに使い、空の推薦結果を避けます。

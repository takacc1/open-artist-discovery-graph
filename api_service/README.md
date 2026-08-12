# Recommendation API for Vercel

Neon PostgreSQLへ保存した計算済み推薦グラフを読み取り、検索・近傍・複数シード推薦を返すFastAPIです。

必要な環境変数は次の2つです。

- `DATABASE_URL`: Neonのpooled PostgreSQL接続文字列
- `ARTIST_DISCOVERY_CORS_ORIGINS`: 許可する画面URLをカンマ区切りで指定

個人の聴取履歴は保存せず、集計済みのアーティストと推薦関係だけを読み取ります。

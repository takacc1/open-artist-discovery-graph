# Open Artist Discovery — Web MVP

好きなアーティストを1〜5組選び、`近い`・`橋渡し`・`冒険`の3モードから推薦結果を確認するWeb画面です。推薦理由、適合度、信頼度、入力アーティスト別のスコアを表示します。

## ローカル起動

Node.js 22.13以上を想定しています。

```bash
npm install
cp .env.example .env.local
npm run dev
```

別のターミナルで、リポジトリ直下から推薦APIも起動します。

```bash
source .venv/bin/activate
uvicorn src.api:app --reload --host 127.0.0.1 --port 8000
```

画面は `http://127.0.0.1:3000`、APIの確認画面は `http://127.0.0.1:8000/docs` です。

## API接続

接続先は `NEXT_PUBLIC_API_BASE_URL` で設定します。未指定時は `http://127.0.0.1:8000` を使います。

APIへ接続できない場合は、画面が壊れないようにプレビューモードへ切り替わります。プレビューはaespa・IVE・TWICEの実計算済みv4結果を表示します。本番公開では、推薦APIを外部から接続できる場所へ配置して環境変数を更新します。

## 確認コマンド

```bash
npm test
```

ビルド後のHTMLに主要な日本語UIとAPI接続処理が含まれること、初期テンプレートが残っていないことを検査します。

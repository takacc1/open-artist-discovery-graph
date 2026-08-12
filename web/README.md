# Open Artist Discovery — Web MVP

好きなアーティストを1〜5組選び、`近い`・`橋渡し`・`冒険`の3モードから推薦結果を確認するWeb画面です。推薦理由、適合度、信頼度、入力アーティスト別のスコアに加え、3段階アンケートを表示します。

公開版: https://nextsound-jp.vercel.app

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

接続先は `NEXT_PUBLIC_API_BASE_URL` で設定します。公開版はVercel上の
`https://open-artist-discovery-api.vercel.app` を使います。ローカル開発では
`.env.local` に `http://127.0.0.1:8000` を指定できます。

通常は公開APIへ接続し、DB内の1,978組を名前検索できます。APIへ接続できない場合だけ、画面が壊れないようにプレビューモードへ切り替わります。プレビューはaespa・IVE・TWICEの実計算済みv4結果を表示します。

初期状態ではアーティストは未選択です。推薦ボタンを押すと結果まで自動で移動します。選択済みアーティスト、モード、推薦結果、任意の3段階評価だけを匿名で保存します。

`/admin` は管理者専用のアンケート集計画面です。管理パスワードは画面のソースへ埋め込まず、入力時に保護されたAPIへ送信し、タブを閉じると画面から消えます。

## 確認コマンド

```bash
npm test
```

ビルド後のHTMLに主要な日本語UIとAPI接続処理が含まれること、初期テンプレートが残っていないことを検査します。

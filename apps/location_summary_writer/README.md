# Location Summary Writer

`Location Summary Writer` は、**既存の日記生成/Notion更新システムから独立**して動く小さなバッチです。  
このディレクトリ配下だけで依存関係・実行ロジックが完結しており、他コードを import しません。

- 入力: Notion `Location_Log` DB（前日05:00〜当日05:00 JST）
- 処理: 位置ログをセグメント化して OpenAI で「日記風」要約
- 出力: Notion `Daily Log` DB の対象日ページへ `Location summary` を上書き

---

## 1. 事前準備（Notion Integration）

1. Notion の **My integrations** で新規 Integration を作成
2. Integration token を控える（`NOTION_TOKEN`）
3. `Location_Log` DB と `Daily Log` DB を開く
4. それぞれ右上の `...` → `Connections` から作成した Integration を Share
5. 各 DB URL から DB ID を取得して控える
   - URL の `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` 部分（ハイフン有無どちらでも可）

---

## 2. 事前準備（OpenAI API Key）

1. OpenAI の API key を作成
2. key を控える（`OPENAI_API_KEY`）

---

## 3. GitHub Secrets / Variables（この workflow 専用）

`.github/workflows/location_summary.yml` で使用する Secrets:

### 必須
- `NOTION_TOKEN`
- `LOCATION_LOG_DB_ID`
- `DAILY_LOG_DB_ID`
- `OPENAI_API_KEY`

### 任意（未設定時デフォルトあり）
- 空文字（`""`）や空白のみの値は **未設定と同じ扱い** になります。
- `TZ`（default: `Asia/Tokyo`）
- `WINDOW_START_HOUR`（default: `5`）
- `DAILY_LOG_DATE_PROP`（default: `Date`）
- `DAILY_LOG_LOCATION_SUMMARY_PROP`（default: `Location summary`）
- `LOCATION_LOG_TIME_PROP`（default: `Time`）
- `LOCATION_LOG_PLACE_PROP`（default: `Place`）
- `LOCATION_LOG_LAT_PROP`（default: `Latitude (raw)`）
- `LOCATION_LOG_LON_PROP`（default: `Longitude (raw)`）
- `OPENAI_MODEL`（default: `gpt-4.1-mini`）
- `OPENAI_BASE_URL`（default: `https://api.openai.com/v1`）
- `DRY_RUN`（default: `false`）
- `LOCATION_ROUND_DECIMALS`（default: `4`）
- `TIME_BUCKET_MINUTES`（default: `30`）

> `OPENAI_API_KEY` は必須です。空文字の場合も未設定扱いとなり、`ConfigError` で起動失敗します。

> `DRY_RUN=true` にすると Notion 更新をスキップし、生成結果のみログ出力します。

---

## 4. スケジュール

- GitHub Actions の cron は **UTC基準**
- JST 06:00 実行は UTC 21:00（前日）
- そのため cron は `0 21 * * *`

---

## 5. ローカル実行

```bash
cd apps/location_summary_writer
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export NOTION_TOKEN=...
export LOCATION_LOG_DB_ID=...
export DAILY_LOG_DB_ID=...
export OPENAI_API_KEY=...
export DRY_RUN=true

python src/main.py
```

---

## 6. 仕様メモ

- 対象窓は `[window_start, window_end)`（start含む、end含まない）
- `window_end` は「直近の05:00（JST）」
- `diary_date` は `window_end - 1日`
- OpenAI には JSON スキーマ付きで要求し、JSON以外は受け付けない
- 429/5xx は指数バックオフでリトライ
- ログ 0 件なら `位置ログがありませんでした` を保存

---

## 7. トラブルシュート

- `401 Unauthorized`（Notion/OpenAI）
  - token/key が無効、または Secrets 設定ミス
- `403 Forbidden`（Notion）
  - Integration が DB に Share されていない
- `429`（OpenAI）
  - レート制限。自動リトライ後も失敗する場合は実行間隔か利用プランを見直し
- `Daily Log property not found`
  - `DAILY_LOG_LOCATION_SUMMARY_PROP` などのプロパティ名が Notion 側と不一致
- `Daily Log page not found`
  - 対象日（前日）の `Date` プロパティが一致するページが無い

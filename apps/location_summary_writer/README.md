# Location Summary Writer

`Location Summary Writer` は、**既存の日記生成/Notion更新システムから独立**して動く小さなバッチです。  
このディレクトリ配下だけで依存関係・実行ロジックが完結しており、他コードを import しません。

- 入力: Notion `Stay Sessions` DB + `Tasks` DB（前日05:00〜当日05:00 JST）
- 処理: 滞在セッションと予定を事実ベースで整形し「日記風」要約を生成（推測禁止）
- 出力: Notion `Daily Log` DB の対象日ページへ `Location summary` を上書き

---

## 1. 事前準備（Notion Integration）

1. Notion の **My integrations** で新規 Integration を作成
2. Integration token を控える（`NOTION_TOKEN`）
3. `Stay Sessions` DB・`Tasks` DB・`Daily Log` DB を開く
4. それぞれ右上の `...` → `Connections` から作成した Integration を Share
5. 各 DB URL から DB ID を取得して控える
   - URL の `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` 部分（ハイフン有無どちらでも可）

---

## 2. GitHub Secrets / Variables（この workflow 専用）

`.github/workflows/location_summary.yml` で使用する Secrets:

### 必須
- `NOTION_TOKEN`
- `STAY_SESSIONS_DB_ID`
- `TASK_DB_ID`
- `DAILY_LOG_DB_ID`

### 任意（未設定時デフォルトあり）
- 空文字（`""`）や空白のみの値は **未設定と同じ扱い** になります。
- `TZ`（default: `Asia/Tokyo`）
- `WINDOW_START_HOUR`（default: `5`）
- `DAILY_LOG_DATE_PROP`（default: `Date`）
- `DAILY_LOG_LOCATION_SUMMARY_PROP`（default: `Location summary`）
- `DRY_RUN`（default: `false`）

> `DRY_RUN=true` にすると Notion 更新をスキップし、生成結果のみログ出力します。

---

## 3. スケジュール

- GitHub Actions の cron は **UTC基準**
- JST 06:17 実行は UTC 21:17（前日）
- そのため cron は `17 21 * * *`
- GitHub Actions の schedule は **毎時0分付近が混雑しやすい** ため、
  0分ちょうどを避ける設定にしています（取りこぼし対策）。

---

## 4. ローカル実行

```bash
cd apps/location_summary_writer
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export NOTION_TOKEN=...
export STAY_SESSIONS_DB_ID=...
export TASK_DB_ID=...
export DAILY_LOG_DB_ID=...
export DRY_RUN=true

python src/main.py
```

---

## 5. 仕様メモ

- 対象窓は `[window_start, window_end)`（start含む、end含まない）
- `window_end` は「直近の05:00（JST）」で、対象窓は前日05:00〜当日05:00
- `diary_date` は `window_end - 1日`
- Stay Sessions は `SessionStart <= window_end AND SessionEnd >= window_start` で取得し、窓外はクリップ
- `DurationMin <= 0` は除外し、同一表示名かつ10分以内の分断はマージ
- TASK_DB_ID（タスク管理DB）から `Event Date` が対象窓内のものだけ反映
- 文章は推測禁止（滞在事実・予定の存在のみ記述）

---

## 6. トラブルシュート

- `401 Unauthorized`（Notion）
  - token が無効、または Secrets 設定ミス
- `403 Forbidden`（Notion）
  - Integration が DB に Share されていない
- `Daily Log property not found`
  - `DAILY_LOG_LOCATION_SUMMARY_PROP` などのプロパティ名が Notion 側と不一致
- `Daily Log page not found`
  - 対象日（前日）の `Date` プロパティが一致するページが無い

# notion-diary-automation

Notion の Daily Log を中心に、前日のデータ ingest → Location summary 生成 → Diary / Sleep insights / Today advice 生成 → 朝メール配信までを GitHub Actions でつなぐ自動化リポジトリです。

## 4 workflows の正式名称と実行順

| Order | Workflow name | Trigger | Notes |
| --- | --- | --- | --- |
| 01 | `Daily Diary 01 - Ingest Daily Log` | `workflow_dispatch` | Daily Log の ensure と ingest を実行 |
| 02 | `Daily Diary 02 - Generate Location Summary` | `workflow_run` from 01 / manual | `Location summary (GPT)` を更新 |
| 03 | `Daily Diary 03 - Generate Diary & Sleep Insights` | `workflow_run` from 02 / manual | `Sleep Analysis JP` / `Today Condition Forecast JP` / `Today advice` / `Diary` を更新 |
| 04 | `Daily Diary 04 - Publish Daily Mail` | `workflow_run` from 03 / manual | 朝メールを配信 |

`workflow_run` の参照先も上記名称に合わせて実装済みです。GitHub Actions の cron を使う場合、**cron は UTC 基準**です。JST の朝実行にしたい場合は UTC に換算して設定してください。

## 実装上のフロー

1. **Ingest**
   - Daily Log ページを ensure します。
   - Tasks / Health / Expenses を Daily Log に取り込みます。
2. **Location Summary**
   - `Location summary (GPT)` を更新します。
3. **Generate Diary & Sleep Insights**
   - Daily Log を再読込します。
   - sleep 系入力があれば `scripts/sleep_condition_generator.py` が当日値・7日平均・平均との差分・既存コンテキストを使って、`Sleep Analysis JP` / `Today Condition Forecast JP` / `Today advice` を生成します。
   - `today_advice` が sleep 生成結果で得られない場合は `scripts/mood_advice_generator.py` で補完生成します。
   - その後、同じ Daily Log の内容から Diary を生成します。
4. **Publish Daily Mail**
   - メール本文の表示順は次のとおりです。
     1. `Today advice`
     2. `Sleep Analysis JP`
     3. `Today Condition Forecast JP`
     4. `就寝時間 / 起床時間 / 睡眠時間`
     5. `Diary`
     6. Summary / Expenses / Done / Drop / Meal

## Daily Log DB に必要な sleep 系プロパティ一覧

以下の Notion 表示名を前提に実装しています。値がなければ Python 側は `None` として安全に扱います。

- `Sleep Start`
- `Sleep End`
- `Sleep Duration`
- `Sleep Score`
- `Sleep Source`
- `Sleep Heart Rate`
- `Deep Duration`
- `REM Duration`
- `Readiness Stars`
- `Readiness HRV`
- `Readiness BPM`
- `Baseline HRV`
- `Baseline Waking BPM`
- `Sleep Analysis JP`
- `Today Condition Forecast JP`
- `Today advice`

## 生成テキストの役割分担

- `Sleep Analysis JP`: 昨夜の睡眠データの**分析**。
- `Today Condition Forecast JP`: 今日の体調・集中力・疲労感などの**予測**。
- `Today advice`: 今日すぐ実行する短い**具体行動**。メール本文の最上部に表示。

## Secrets

### ingest workflow に必要な secrets

- `TASKS_CLOSED_URL`
- `DAILY_LOG_UPSERT_URL`
- `WORKERS_BEARER_TOKEN`

※ ingest workflow ではメール送信用 secret は不要です。

### publish workflow に必要な secrets

- `MAIL_FROM`
- `MAIL_TO`
- `GMAIL_APP_PASSWORD`
- `DAILY_LOG_UPSERT_URL`
- `WORKERS_BEARER_TOKEN`
- `PUBLIC_BASE_URL`
- `MAIL_LINK_SECRET`

### generate diary / sleep insights workflow で必要な secrets

- `DAILY_LOG_UPSERT_URL`
- `WORKERS_BEARER_TOKEN`
- `OPENAI_API_KEY`
- 必要に応じて `OPENAI_MODEL`, `TODAY_ADVICE_MINI_MODEL`, `TODAY_ADVICE_FINAL_MODEL`

## 実装メモ

- `publish/read_daily_log.py` は sleep 系・Today advice 系プロパティを `DailyLogSummary` に揃えて返します。
- `scripts/daily_job.py` は sleep insights の保存後に Daily Log を再読込し、後続の diary / mail で同じフィールドを安全に参照します。
- `publish/render_mail.py` はテンプレート payload に sleep / readiness / advice 系フィールドを渡します。
- `publish/email_templates.py` は値があるセクションだけを表示し、睡眠時間は `7時間15分` の形式で表示します。

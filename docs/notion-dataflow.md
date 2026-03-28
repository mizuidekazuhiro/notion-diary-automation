# Notion Data Flow Mapping

## 1. DB 一覧

| DB用途 | 環境変数名 | 実利用箇所 | 読み/書き |
| --- | --- | --- | --- |
| Daily Log | `DAILY_LOG_DB_ID` | `workers/src/index.ts`, `publish/read_daily_log.py`, `scripts/daily_job.py`, `publish/email_templates.py` | 読み書き |
| Tasks | `TASK_DB_ID` | `workers/src/index.ts`, `workers/src/application/daily_log_task_relations.ts` | 主に読み |
| Inbox | `INBOX_DB_ID` | `workers/src/index.ts` | 読み |
| Health | `HEALTH_DB_ID` | `workers/src/index.ts` | 読み |
| Expenses | `EXPENSES_DB_ID` | `workers/src/index.ts`, `scripts/expense_f_aggregator.py` | 読み |
| Location Log | `LOCATION_LOG_DB_ID` | `workers/src/index.ts` | 読み |

## 2. 全体フロー

```text
Phase A (Ingest)
  ├─ ensure Daily Log
  ├─ Tasks relation ingest
  ├─ Expenses ingest
  └─ Health ingest (sleep / nutrition / meal photo)
        ↓
Daily Diary 02 - Generate Location Summary
        ↓
Phase C (Notify Diary)
  ├─ Daily Log read
  ├─ expense_f_aggregator (Python から Notion API 直接読取)
  ├─ sleep_condition_generator
  ├─ f_risk_generator
  └─ diary_generator
        ↓
Phase B (Publish)
  └─ render_mail / send_mail
```

## 3. 正式な sleep プロパティ対応

| Notion 表示名 | 内部名 | 読み元 | 書き先 |
| --- | --- | --- | --- |
| Sleep Start | `sleep_start` | Health DB | Daily Log |
| Sleep End | `sleep_end` | Health DB | Daily Log |
| Sleep Duration | `sleep_duration_min` | Health DB | Daily Log |
| Sleep Score | `sleep_score` | Health DB | Daily Log |
| Sleep Source | `sleep_source` | Health DB | Daily Log |
| Sleep Heart Rate | `sleep_heart_rate` | Health DB | Daily Log |
| Deep Duration | `deep_duration_min` | Health DB | Daily Log |
| REM Duration | `rem_duration_min` | Health DB | Daily Log |
| Readiness Stars | `readiness_stars` | Health DB | Daily Log |
| Readiness HRV | `readiness_hrv` | Health DB | Daily Log |
| Readiness BPM | `readiness_bpm` | Health DB | Daily Log |
| Baseline HRV | `baseline_hrv` | Health DB | Daily Log |
| Sleep Analysis JP | `sleep_analysis_jp` | Daily Log | Daily Log |
| Today Condition Forecast JP | `today_condition_forecast_jp` | Daily Log | Daily Log |

### 旧名の扱い

- `Sleep Analysis` は read alias のみ許容し、保存先は必ず `Sleep Analysis JP` です。
- `Today Condition Forecast` は read alias のみ許容し、保存先は必ず `Today Condition Forecast JP` です。
- `Baseline Waking BPM` は現 DB では使いません。

## 4. property 解決ルール

- Workers / publish 側では property 名を normalize して比較します。
- normalize では以下を吸収します。
  - 大文字小文字差
  - 前後空白
  - スペース
  - アンダースコア (`_`)
  - ハイフン (`-`)
- 曖昧一致で複数候補が見つかった場合は warning を出し、書き込みを skip します。
- schema validation の Missing 表示も正式名ベースです。

## 5. overwrite 仕様

- `scripts/daily_job.py` は Phase C で weather → Expense F 集計 → sleep insights → F risk → Today advice → Diary → notify 判定を直列実行します。
- sleep signal がある日は `Sleep Analysis JP` / `Today Condition Forecast JP` を毎回再生成して上書き保存します。
- diary 保存とは独立して sleep 系のみ保存しても失敗しないようにしています。
- Publish / Mail は `Sleep Start` / `Sleep End` / `Sleep Duration` / `Sleep Analysis JP` / `Today Condition Forecast JP` のうち値があるものだけを表示します。

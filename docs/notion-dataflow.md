# Notion Data Flow Mapping

## 1) Notion DB一覧

| DB用途 | 環境変数名 | 実利用箇所 | 読み/書き |
|---|---|---|---|
| Daily Log | `DAILY_LOG_DB_ID` | `workers/src/index.ts`, `publish/read_daily_log.py`, `scripts/daily_job.py` | 読み書き |
| Tasks | `TASK_DB_ID` | `workers/src/index.ts`, `workers/src/application/daily_log_task_relations.ts` | 主に読み |
| Inbox | `INBOX_DB_ID` | `workers/src/index.ts` | 読み |
| Health condition | `HEALTH_DB_ID` | `workers/src/index.ts` | 読み |
| Expenses | `EXPENSES_DB_ID` | `workers/src/index.ts` | 読み |
| Location Log | `LOCATION_LOG_DB_ID` | `workers/src/index.ts` | 読み |

## 2) 全体フロー

```text
Phase A (Ingest)
  ├─ ensure Daily Log
  ├─ Tasks relation ingest
  ├─ Expenses ingest
  └─ Health ingest (sleep / nutrition / meal photo)
        ↓
Location Summary Writer
        ↓
Phase C (Notify Diary)
  ├─ Daily Log read
  ├─ sleep_condition_generator
  └─ diary_generator
        ↓
Phase B (Publish)
  └─ render_mail / send_mail
```

## 3) 睡眠プロパティの読み元 / 書き先 / 利用箇所

| Notion表示名 | 内部名 | 読み元 | 書き先 | 利用箇所 |
|---|---|---|---|---|
| Sleep Start | `sleep_start` | Health DB | Daily Log | `workers/src/index.ts`, `publish/read_daily_log.py`, `scripts/daily_job.py`, `publish/email_templates.py` |
| Sleep End | `sleep_end` | Health DB | Daily Log | 同上 |
| Sleep Duration | `sleep_duration_min` | Health DB | Daily Log | `workers/src/index.ts`, `publish/read_daily_log.py`, `scripts/sleep_condition_generator.py`, `scripts/diary_generator.py`, `publish/email_templates.py` |
| Sleep Score | `sleep_score` | Health DB | Daily Log | `workers/src/index.ts`, `publish/read_daily_log.py`, `scripts/sleep_condition_generator.py`, `scripts/diary_generator.py` |
| Sleep Source | `sleep_source` | Health DB | Daily Log | `workers/src/index.ts`, `publish/read_daily_log.py`, `scripts/diary_generator.py` |
| Sleep Heart Rate | `sleep_heart_rate` | Health DB | Daily Log | `workers/src/index.ts`, `publish/read_daily_log.py`, `scripts/sleep_condition_generator.py`, `scripts/diary_generator.py` |
| Deep Duration | `deep_duration_min` | Health DB | Daily Log | `workers/src/index.ts`, `publish/read_daily_log.py`, `scripts/sleep_condition_generator.py`, `scripts/diary_generator.py` |
| REM Duration | `rem_duration_min` | Health DB | Daily Log | 同上 |
| Readiness Stars | `readiness_stars` | Health DB | Daily Log | `workers/src/index.ts`, `publish/read_daily_log.py`, `scripts/sleep_condition_generator.py`, `scripts/diary_generator.py` |
| Readiness HRV | `readiness_hrv` | Health DB | Daily Log | 同上 |
| Readiness BPM | `readiness_bpm` | Health DB | Daily Log | 同上 |
| Baseline HRV | `baseline_hrv` | Health DB | Daily Log | 同上 |
| Baseline Waking BPM | `baseline_waking_bpm` | Health DB | Daily Log | 同上 |
| Sleep Analysis | `sleep_analysis_jp` | Daily Log | Daily Log | `workers/src/index.ts`, `publish/read_daily_log.py`, `scripts/sleep_condition_generator.py`, `scripts/daily_job.py`, `scripts/diary_generator.py`, `publish/email_templates.py` |
| Today Condition Forecast | `today_condition_forecast_jp` | Daily Log | Daily Log | 同上 |

## 4) property 解決ルール

- Workers は property 名を normalize して比較します。
- normalize では以下を吸収します。
  - 大文字小文字差
  - 前後空白
  - スペース
  - アンダースコア (`_`)
  - ハイフン (`-`)
- 複数候補に一致した場合は warning を出し、自動更新を skip します。

## 5) sleep 未入力時の挙動

- Ingest は睡眠値がない場合でも成功扱いで継続します。
- Notify Diary は睡眠 signal が無ければ sleep insight 生成を skip します。
- Publish / mail は値がある項目だけ表示します。

## 6) 上書き更新の仕様

- `scripts/daily_job.py` は Notify 実行のたびに `scripts/sleep_condition_generator.py` を呼びます。
- sleep signal がある日は `Sleep Analysis` / `Today Condition Forecast` を再生成し、Workers の `generate_diary` endpoint 経由で毎回上書き保存します。
- 既存値があっても保護しません。同じ入力なら再実行可能という意味で冪等です。

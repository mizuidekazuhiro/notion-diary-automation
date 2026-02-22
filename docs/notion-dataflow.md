# Notion Data Flow Mapping

## 1) Notion DB一覧

| DB用途 | 環境変数名 | 実利用箇所 | 読み/書き |
|---|---|---|---|
| Daily Log | `DAILY_LOG_DB_ID` | `workers/src/index.ts`, `workers/src/application/daily_log_task_relations.ts` | 読み書き |
| Tasks | `TASK_DB_ID` | `workers/src/index.ts`, `workers/src/application/daily_log_task_relations.ts` | 主に読み（relation書き込みはDaily Log側） |
| Inbox | `INBOX_DB_ID` | `workers/src/index.ts` | 読み |
| Health condition | `HEALTH_DB_ID` | `workers/src/index.ts` | 読み |
| Expenses | `EXPENSES_DB_ID` | `workers/src/index.ts` | 読み |
| Location Log | `LOCATION_LOG_DB_ID` | `workers/src/index.ts` | 読み |

## 2) データフロー図（文章＋図）

```text
Tasks DB / Health DB / Expenses DB / Location Log DB
    ↓ (query)
application flow in workers/src/index.ts
    ↓
domain helpers (meal_summary, location_summary, daily_log_ingest)
    ↓
Daily Log DB (create/update page properties and relations)
```

```text
Tasks DB
  ↓ queryDatabaseAll(status/date filter)
updateDailyLogTaskRelations()
  ↓ find/create Daily Log page by Date
Daily Log DB
  ↓ update "Done Tasks" / "Drop Tasks"
```

## 3) プロパティ単位マッピング（主要）

| DB | Property | 読み | 書き | ファイル |
|---|---|---:|---:|---|
| Tasks | `Status` (`TASK_STATUS_PROPERTY_NAME`) | ✅ | - | `workers/src/index.ts`, `workers/src/application/daily_log_task_relations.ts` |
| Tasks | `Done date` (`TASK_DONE_DATE_PROPERTY_NAME`) | ✅ | - | 同上 |
| Tasks | `Drop date` (`TASK_DROP_DATE_PROPERTY_NAME`) | ✅ | - | 同上 |
| Daily Log | `Date` | ✅ | ✅ | `workers/src/index.ts`, `workers/src/application/daily_log_task_relations.ts` |
| Daily Log | `Target Date` | ✅ | ✅ | `workers/src/index.ts` |
| Daily Log | `Activity Summary` | - | ✅ | `workers/src/index.ts` |
| Daily Log | `Diary` | - | ✅ | `workers/src/index.ts` |
| Daily Log | `Done Tasks` | - | ✅ | `workers/src/application/daily_log_task_relations.ts` |
| Daily Log | `Drop Tasks` | - | ✅ | `workers/src/application/daily_log_task_relations.ts` |
| Daily Log | `Location summary` | - | ✅ | `workers/src/index.ts` |
| Daily Log | `Meal summary` | - | ✅ | `workers/src/index.ts` |
| Daily Log | `Expenses total` | - | ✅ | `workers/src/index.ts` |
| Daily Log | `Expenses` | - | ✅ | `workers/src/index.ts` |

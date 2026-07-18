# 司法試験 Study Session API

PC用タイマーなどから、勉強1回ごとのセッションを既存のNotion `App Usage Sessions` DBへ登録するAPIです。

## Endpoint

```text
POST /execute/api/study/session
```

既存の `notion-diary-automation` Cloudflare Worker内に実装します。別Workerや新しいNotion DBは作成しません。既存DBのIDは実装上の既定値を使用し、必要な場合だけ `APP_USAGE_SESSIONS_DB_ID` 環境変数で上書きできます。

## Authentication

```http
Authorization: Bearer <WORKERS_BEARER_TOKEN>
Content-Type: application/json
```

トークンはGitHubへコミットせず、PC側の環境変数またはローカル設定に保存します。

## Request body

```json
{
  "session_id": "windows-20260718-120000-7b9f",
  "started_at": "2026-07-18T12:00:00+09:00",
  "ended_at": "2026-07-18T12:30:00+09:00",
  "app": "Itojuku",
  "device": "Windows PC",
  "source": "shortcut"
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `session_id` | Yes | 再送時の二重登録を防ぐ一意のID |
| `started_at` | Yes | 学習開始日時（ISO 8601） |
| `ended_at` | Yes | 学習終了日時（ISO 8601） |
| `app` | No | `Itojuku` または `Anki`。省略時は `Itojuku` |
| `device` | No | 端末名。省略時は `Windows PC` |
| `source` | No | 記録元。省略時は既存選択肢の `shortcut` |

## Processing

1. Bearer Tokenを検証する。
2. `session_id` が既に存在するか確認する。
3. 未登録なら `App Usage Sessions` DBへ1ページ追加する。
4. 終了時刻から午前4時境界の `Target Date` をAPI側で計算する。
5. 同日のセッションを再集計する。
6. Daily Logの `Study Minutes`、`Study Sessions`、`Study Last Used At`を更新する。

同じ `session_id` を再送した場合、新しいページは作成せず、既存セッションを基に日次合計だけを再同期します。

## Notion properties

既存の `App Usage Sessions` DBの以下のプロパティを使用します。

- `Name`
- `Session ID`
- `Start At`
- `End At`
- `Duration Min`
- `Target Date`
- `App`
- `Device`
- `Source`

## Date boundary

学習日の境界は日本時間の午前4時です。

```text
Target Date = date(ended_at in Asia/Tokyo - 4 hours)
```

午前0時から3時59分に終了したセッションは前日の学習として扱います。

## curl example

```bash
curl -X POST "https://<worker-host>/execute/api/study/session" \
  -H "Authorization: Bearer $WORKERS_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "windows-20260718-120000-7b9f",
    "started_at": "2026-07-18T12:00:00+09:00",
    "ended_at": "2026-07-18T12:30:00+09:00",
    "app": "Itojuku",
    "device": "Windows PC"
  }'
```

## Response outline

```json
{
  "ok": true,
  "created": true,
  "duplicate": false,
  "session_id": "windows-20260718-120000-7b9f",
  "target_date": "2026-07-18",
  "duration_min": 30,
  "daily_totals": {
    "study_minutes": 150,
    "study_sessions": 3,
    "study_last_used_at": "2026-07-18T03:30:00.000Z"
  },
  "daily_log_updated": true
}
```

## Validation

Workerテストでは、午前4時境界、時間計算、既定値、終了時刻が開始時刻以前の場合の拒否を確認します。CIではテストに加えてTypeScript型検査を実行します。

## Operational note

Notionは `Session ID` にデータベース上の一意制約を設定できないため、APIは登録前に重複照会を行います。通常のPCアプリ再送には対応できますが、完全に同時刻の並行リクエストまで厳密に排除するものではありません。

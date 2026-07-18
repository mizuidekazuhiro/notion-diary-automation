# 司法試験 Study API

PC用タイマーなどから、NotionのDaily Logへ司法試験の勉強時間を登録するための専用URLです。

## Endpoint

```text
POST /execute/api/study/session
```

このURLは既存のDaily Log更新処理へ安全にルーティングされるため、既存と同じBearer認証、日付検証、Notionプロパティ型検証が適用されます。

## Authentication

```http
Authorization: Bearer <WORKERS_BEARER_TOKEN>
Content-Type: application/json
```

トークンをソースコードやGitHubへコミットしないでください。PC側では環境変数またはローカルの `.env` に保存します。

## Request body

```json
{
  "target_date": "2026-07-18",
  "study_minutes": 125,
  "study_sessions": 3,
  "study_last_used_at": "2026-07-18T12:45:00+09:00"
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `target_date` | `YYYY-MM-DD` | Daily Logの対象日 |
| `study_minutes` | number | 対象日の累計勉強時間（分） |
| `study_sessions` | number | 対象日の累計セッション数 |
| `study_last_used_at` | ISO 8601 | 最後に学習を終了した日時 |

## Important: values are cumulative totals

現行のv1 APIは加算値ではなく、対象日の**累計値**を保存します。

PCタイマーは次の順序で使用します。

1. `GET /api/daily_log?date=YYYY-MM-DD` で現在値を取得する。
2. 今回のセッション時間を `study_minutes` に加える。
3. `study_sessions` を1増やす。
4. 新しい累計値を `POST /execute/api/study/session` に送る。

例：現在120分・2セッションで、今回30分勉強した場合は、`study_minutes: 150`、`study_sessions: 3` を送ります。

## curl example

```bash
curl -X POST "https://<worker-host>/execute/api/study/session" \
  -H "Authorization: Bearer $WORKERS_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "target_date": "2026-07-18",
    "study_minutes": 150,
    "study_sessions": 3,
    "study_last_used_at": "2026-07-18T12:45:00+09:00"
  }'
```

## Date boundary

PCアプリ側では午前4時を学習日の境界として扱います。

```text
study_date = date(now in Asia/Tokyo - 4 hours)
```

午前0時から3時59分に終了したセッションは、前日のDaily Logへ登録します。

## Current limitation

GETとPOSTの間に別端末から更新が入ると、後から送った累計値で上書きされます。PCアプリとiPhoneショートカットを同時に終了させない通常運用では問題になりにくいですが、将来はセッションID付きの原子的な加算APIへ拡張します。

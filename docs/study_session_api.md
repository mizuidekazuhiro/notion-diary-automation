# 司法試験 Study API

既存Cloudflare Worker内で、Notion `App Usage Sessions` とDaily LogのStudy値を更新します。認証はいずれも次のBearer tokenです。

```http
Authorization: Bearer <WORKERS_BEARER_TOKEN>
Content-Type: application/json
```

## 通常セッション

```text
POST /execute/api/study/session
```

Itojukuなど1回ごとのセッションを登録します。

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

`session_id` が既にあればページを増やさず、日次合計だけを再計算します。学習日は終了時刻をAsia/Tokyoへ変換し、4時間引いた日付です。

## Anki日次aggregate

```text
POST /execute/api/study/anki-daily
```

```json
{
  "target_date": "2026-08-10",
  "study_minutes": 90.17,
  "study_sessions": 8,
  "first_review_at": "2026-08-10T04:05:00+09:00",
  "last_review_at": "2026-08-11T01:20:00+09:00",
  "review_count": 137,
  "max_time_review_count": 3,
  "source": "anki_revlog"
}
```

Workerは `Session ID = anki-revlog:<target_date>` を生成します。同じ日を再送すると、既存ページの値を上書きします。追加されるApp Usage Sessionsプロパティは次の3つだけです。

- `Session Count` (`number`)
- `Review Count` (`number`)
- `Max Time Review Count` (`number`)

`Source` selectには `anki_revlog` を追加します。

レビューが0件の日は、minutes/sessions/review_count/max_time_review_countを0、first/lastをnullにします。レビューがある場合、first/lastは対象日の `04:00:00 JST` 以上、翌日 `04:00:00 JST` 未満でなければ400です。

PC revlogページが存在する日は、それだけをAnkiの正本とし、同日の旧iPhone Anki行をDaily Log合計から除外します。Anki以外は従来どおり加算します。

## Daily Log再集計

```text
POST /execute/api/study/reconcile
```

```json
{
  "target_date": "2026-08-10"
}
```

App Usage Sessionsの保存済み値だけでDaily Logを再計算します。Daily Log作成直後に `scripts/daily_job.py` が呼ぶため、Anki aggregateの方が先に届いても値を失いません。Daily Logがまだない場合は200で `daily_log_updated=false` を返します。

## 集計結果

```json
{
  "daily_totals": {
    "study_minutes": 120.17,
    "study_sessions": 9,
    "study_last_used_at": "2026-08-11T01:20:00.000Z",
    "anki_revlog_authoritative": true
  },
  "daily_log_updated": true
}
```

詳細なPCセットアップは [Anki PC Automatic Study Tracking](anki_pc_automatic_study_tracking.md) を参照してください。

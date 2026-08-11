# Health recovery runbook

最終確認日: 2026-08-11

## 確認できた事実

- 2026-07-20のHealthページには `Sleep Duration Min = 374` と睡眠時刻がありました。
- 2026-07-21以降の最新ページは、日付とSourceだけで主要Health項目が空です。
- 空ページは7月21日以前にも断続的に存在しており、送信元は以前から不安定でした。
- 2026-07-21 13:28、15:53、16:15 UTCのPhase AはWorkerから `401 invalid bearer token` を受けて失敗しました。
- GitHubの `WORKERS_BEARER_TOKEN` 更新後、16:21 UTCのPhase Aは成功しました。
- このリポジトリはHealth condition DBへ元データを書きません。空ページを作っている処理はiPhoneショートカット等の外部送信元です。

したがって、7月21日のDaily automation停止には認証不一致がありましたが、それだけでは7月21日以降の空Healthページ継続を説明できません。残る原因は外部送信元が実測値を取得できないまま骨組みページを作成していることです。

## このPRで直す範囲

- Healthの `no_data / stale / degraded` は通知対象にしますが、Phase Aと後続の日記・メール処理は止めません。
- 認証・HTTP・Notion API障害の `failed` は、TasksとExpensesの処理とDaily Logの要約更新を終え、sanitized logへ診断を残してからPhase Aを失敗させます。
- 空の栄養・体重・Meal summaryでDaily Logの既存値を消しません。
- 部分データは `degraded` とし、有効な項目だけ更新します。
- ログには状態、項目名、理由コードだけを残します。

失われたHealth実測値の復元やiPhoneショートカット自体の編集は、このリポジトリからは実行できません。

## 外部送信元の復旧手順

1. iPhoneでHealth送信ショートカットを開き、HealthKitへの読取権限を確認します。
2. Sleep、HRV、Readiness、Nutritionの取得結果を、Notion送信前にQuick Look等で確認します。
3. 主要項目が全て空ならNotionページを作らず、ショートカットをエラー終了させます。
4. Workerを経由する構成なら、iPhone側Bearer tokenとCloudflare Workerの `WORKERS_BEARER_TOKEN` を同時に更新します。GitHub Actions側だけ更新してもiPhone側は直りません。
5. 対象日、Source、少なくとも1つの主要Health値を含むテスト送信を1回実行します。
6. Health condition DBで対象日の実測値を確認後、`Daily Diary 01 - Ingest Daily Log` を手動実行します。
7. Phase AとPhase Dが完了し、read-only canaryのHealth警告が解消することを確認します。

## 送信元の必須ガード

送信前に次を満たさない場合は、ページ作成を禁止します。

```text
target_date is present
AND source is present
AND at least one of:
  sleep_duration_min, sleep_score,
  readiness_hrv, readiness_bpm,
  kcal, protein, fat, carb
```

`Date` と `Source` だけのページは成功ではありません。すでに作成された空ページは自動削除せず、正しい値を同じページへ補完するか、送信元を復旧後に管理者が整理します。

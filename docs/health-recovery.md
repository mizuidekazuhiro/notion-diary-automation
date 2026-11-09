# Health recovery runbook

最終確認日: 2026-10-16

最新の証拠と上流APIの修正候補は [daily_mail_quality_330_332_health_investigation.md](daily_mail_quality_330_332_health_investigation.md) を参照してください。以下の7月の記録は過去時点の観測です。

## 確認できた事実

- 2026-07-20のHealthページには `Sleep Duration Min = 374` と睡眠時刻がありました。
- 2026-07-21以降の最新ページは、日付とSourceだけで主要Health項目が空です。
- 空ページは7月21日以前にも断続的に存在しており、送信元は以前から不安定でした。
- 2026-07-21 13:28、15:53、16:15 UTCのPhase AはWorkerから `401 invalid bearer token` を受けて失敗しました。
- GitHubの `WORKERS_BEARER_TOKEN` 更新後、16:21 UTCのPhase Aは成功しました。
- このリポジトリはHealth condition DBへ元データを書きません。上流 `Daily_Log_Healthcare` のHealth APIには実測値なしでページを作れる経路があり、Supplement Intake APIにもrelation用の空Healthページ作成経路があります。どの経路が実際の欠測ページを作ったかは未確定です。

したがって、7月21日のDaily automation停止には認証不一致がありましたが、それだけでは空Healthページ継続を説明できません。空ページの存在だけからHealth送信元が実行された、または送信時点で実測値が空だったと断定できません。送信元のHTTP応答とWorker受信ログを照合する必要があります。

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
4. 送信URLを確認して認証を切り分けます。上流 `Daily_Log_Healthcare` の `/api/health/daily` は `HEALTH_API_KEY`、下流の `/execute/api/daily_log/ingest_health` は `WORKERS_BEARER_TOKEN` を使います。401の場合だけ、失敗した経路の送信側Bearerと該当WorkerのSecretを照合します。両キーが同じと仮定して一括更新しないでください。
5. 対象日、Source、少なくとも1つの主要Health値を含むテスト送信を1回実行します。
6. Health condition DBで対象日の実測値を確認後、`Daily Diary 01 - Ingest Daily Log` を手動実行します。
7. Phase AとPhase Dが完了し、read-only canaryのHealth警告が解消することを確認します。

## 送信元の必須ガード

Daily Mailの主要項目欠測を解消する送信には、次の主要項目を少なくとも1つ含めます。

```text
target_date is present
AND source is present
AND at least one of:
  sleep_duration_min, sleep_score,
  readiness_hrv, readiness_bpm,
  kcal, protein, fat, carb
```

上流APIの修正候補は、数値実測項目または有効な睡眠開始/終了時刻が皆無のリクエストを422にします。体重のみ等の従来の部分更新は維持するため、APIの成功がDaily Mailの主要8項目や当日睡眠の充足を保証するわけではありません。

`Date` と `Source` だけのページはHealth実測値の成功ではありません。すでに作成された空ページは自動削除せず、送信元から取得した同日の実測値がある場合のみ同じ日付へ送ります。過去日の値を流用しません。

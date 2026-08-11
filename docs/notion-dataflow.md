# Notion data flow

最終確認日: 2026-08-11

この文書は、現在の `main` とNotionの実スキーマを基準にしています。Notionのページタイトルは表示用で、日付処理のキーには使いません。Daily Logは `Target Date`、各入力DBはそれぞれの正式な日付プロパティを正本とします。

## 自動化の全体像

```text
iPhone / 外部サービス
  ├─ Health condition DB  ─┐
  ├─ Expenses DB          ├─ Phase A: Daily Log作成・入力転記
  ├─ Tasks DB             ┘
  └─ Location Log DB ─────── Phase B: Location summary生成
                                  ↓
                         Phase C: Weather / Expense F / Sleep
                                  / Notes / F Risk / Today advice / Diary
                                  ↓
                         Phase D: Mail送信 → 最終品質ゲート
                                  ↓
                         Notion read-only canary
```

Phase Aは `Daily Diary 01 - Ingest Daily Log`、Phase BはLocation summary、Phase Cは生成・保存、Phase Dはメール配信です。Healthの `no_data`、`stale`、`degraded` はsanitized warningを残して処理を継続し、Health欄なしで日記とメールを生成します。認証・HTTP・Notion API障害を表す `failed` だけは、他ソースの処理とDaily Log要約更新を終えた後にPhase Aを失敗させます。

## DBと責務

| DB | 設定 | このリポジトリの責務 | 読み書き |
| --- | --- | --- | --- |
| Daily Log | `DAILY_LOG_DB_ID` | 日次集約、生成結果、送信メタデータ | 読み書き |
| Health condition | `HEALTH_DB_ID` | 外部送信済みHealthの検証とDaily Log転記 | 読みのみ |
| Expenses | `EXPENSES_DB_ID` | 日次支出、Expense F教師データ | 読みのみ |
| Tasks | `TASK_DB_ID` | Done/DropとDaily Log relation | 主に読み |
| Inbox | `INBOX_DB_ID` | Notes入力 | 読み |
| Location Log | `LOCATION_LOG_DB_ID` | 滞在・天気地点の入力 | 読み |

Health condition DBへ値を送るiPhoneショートカット等は、このリポジトリの管理外です。Workerの `/execute/api/daily_log/ingest_health` はHealth DBを読み、Daily Logへ転記するだけです。

## Healthの品質契約

主要8項目は `Sleep Duration Min`、`Sleep Score`、`Readiness HRV`、`Readiness BPM`、`Kcal`、`Protein`、`Fat`、`Carb` です。

| 状態 | 意味 | Daily Log更新 | Phase A |
| --- | --- | --- | --- |
| `ok` | 主要項目の50%以上があり対象日と一致 | 値がある項目だけ更新 | 継続 |
| `degraded` | 主要項目はあるが50%未満 | 値がある項目だけ更新 | 継続し、診断を残す |
| `no_data` | ページなし、または主要項目が全て空 | 更新しない | 警告して継続 |
| `stale` | `data_date` が対象日と不一致 | 更新しない | 警告して継続 |
| `failed` | 認証、HTTP、Notion API等の失敗 | 更新しない | 失敗 |

診断値は `data_date`、`last_valid_at`、`completeness`、`available_fields`、`error_code` です。ログには項目名と状態だけを出し、Healthの実測値やトークンは出しません。

空値は削除命令として扱いません。栄養、体重、睡眠、Readiness、Meal summary、Meal Photosはいずれも、Health側に有効値がある場合だけDaily Logを更新します。部分的なHealthレコードを再処理しても、Daily Logの既存値を空欄で消しません。

## Healthプロパティ対応

| Health condition | Daily Log | 用途 |
| --- | --- | --- |
| `Sleep Start` | `Sleep Start` | 睡眠開始 |
| `Sleep End` | `Sleep End` | 睡眠終了 |
| `Sleep Duration Min` | `Sleep Duration` | 睡眠分数 |
| `Sleep Score` | `Sleep Score` | 睡眠スコア |
| `Sleep Source` | `Sleep Source` | 睡眠取得元 |
| `Sleep Heart Rate` | `Sleep Heart Rate` | 睡眠時心拍 |
| `Deep Duration Min` | `Deep Duration` | 深い睡眠 |
| `REM Duration Min` | `REM Duration` | REM睡眠 |
| `Readiness Stars` | `Readiness Stars` | Readiness |
| `Readiness HRV` | `Readiness HRV` | HRV |
| `Readiness BPM` | `Readiness BPM` | 起床時心拍 |
| `Baseline HRV` | `Baseline HRV` | HRV基準値 |
| `Baseline Waking BPM` | `Baseline Waking BPM` | 起床時心拍基準値 |
| `Protein` / `Fat` / `Carb` / `Kcal` / `Weight` | 同名 | 栄養・体重 |
| `Meal Photos` | `Meal Photos` | 食事写真 |

表示名と内部名の差は、大文字小文字、空白、`_`、`-` を正規化して解決します。複数候補に一致した場合は安全のため書き込みません。

## 日付境界

標準の日付境界はJST 05:00です。睡眠、支出、栄養、位置情報はこの境界で日付帰属を決めます。Study/Ankiのみ意図的に04:00を使います。Today sleepとして使えるのは、対象日の保存済み値か、05:00境界で対象日に帰属する候補だけです。

## 生成データと正本

- Expense Fの正本はExpenses DBの `F` チェックボックスです。
- F Riskの永続状態は現在 `automation-state` ブランチです。Daily Logへの移行は別PRでdual-writeとreadbackを経て行います。
- `Sleep Analysis JP` と `Today Condition Forecast JP` は生成値です。Diaryのraw inputには混ぜません。
- `Location summary (GPT)` が正式な生成先です。`Location summary` は読取互換のみ残しています。
- `Weather Summary` と数値プロパティが正式な天気データです。`Weather` は互換表示として残っています。

## 監視と復旧

1. Phase Aで各入力を取得し、Healthの品質状態を確定します。
2. `no_data / stale / degraded` はDaily Logの既存Healthを保持し、警告を残して後続処理を継続します。
3. `failed` は認証・API障害としてPhase Aを失敗させます。
4. Phase Dはメール送信後に全ソースの品質を再評価し、Health欠損だけならwarningにします。
5. read-only canaryがNotionスキーマ、Expense F query、最新Health、Daily Log読取を独立確認します。
6. Health送信元の復旧手順は [health-recovery.md](health-recovery.md) を参照します。

棚卸し対象は [obsolete-inventory.md](obsolete-inventory.md)、古いOpen PRの扱いは [open-pr-disposition-2026-08-11.md](open-pr-disposition-2026-08-11.md) に分離しています。

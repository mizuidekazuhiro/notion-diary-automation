# notion-diary-automation

Notion の Daily Log を中心に、前日のデータを **Phase A: ingest → Phase B: publish source prep → Phase C: generate/notify → Phase D: publish mail** とつなぐ自動化リポジトリです。現在の GitHub Actions では Phase B は `Location summary (GPT)` 更新として実装され、Phase C は sleep insights / Today advice / Diary の生成と通知判定を担当します。

## Workflow 名と依存関係

| Order | Workflow name | Trigger | 実責務 |
| --- | --- | --- | --- |
| 01 | `Daily Diary 01 - Ingest Daily Log` | `workflow_dispatch` | Phase A: Daily Log の ensure / ingest |
| 02 | `Daily Diary 02 - Generate Location Summary` | `workflow_run` from 01 / manual | Phase B: `Location summary (GPT)` 更新 |
| 03 | `Daily Diary 03 - Generate Diary & Sleep Insights` | `workflow_run` from 02 / manual | Phase C: sleep insights → Today advice → Diary → notify 判定 |
| 04 | `Daily Diary 04 - Publish Daily Mail` | `workflow_run` from 03 / manual | Phase D: 朝メール配信 |

`workflow_run.workflows` は上記 `name:` と一致しています。README の名称・YAML の `name:`・依存先は同じです。

## Phase ごとの最終仕様

### Phase A: ingest
- Daily Log ページを ensure します。
- Tasks / Health / Expenses を Daily Log に取り込みます。

### Phase B: publish source prep
- `apps/location_summary_writer` が `Location summary (GPT)` を更新します。
- ここでは Today advice / sleep insights / Diary は生成しません。

### Phase C: generate/notify
`scripts/daily_job.py --phase notify_diary` は次の順番で**直列実行**します。

1. sleep insights 生成
2. sleep insights 保存
3. Daily Log 再読込
4. Today advice 生成
5. Today advice 保存
6. Daily Log 再読込
7. Diary 生成
8. Diary 保存
9. Daily Log 再読込
10. notify 判定

#### 役割分離
- `scripts/sleep_condition_generator.py` は **`sleep_analysis_jp` / `today_condition_forecast_jp` の2項目だけ**生成します。
- `scripts/mood_advice_generator.py` は **`today_advice` だけ**生成します。
- `scripts/diary_generator.py` は Diary だけを生成します。
- Diary は後段で sleep insights と Today advice を参照できますが、責務としては「後段参照」のみです。

#### Today advice の入力ルール
- 過去30日を使います。
- 高評価日は mood 4/5、低評価日は 1/2、中間日は 3 です。
- 高評価5件・低評価5件は、可能な限り偏らないように抽出します。不足時はその件数でフォールバックします。
- diary 本文 / 過去 diary 本文は使いません。
- 日本語自由記述として使うのは `notes` のみです。
- `location summary` は構造化コンテキストとして使ってよい設計です。
- `meal / done / drop / spend / sleep / notes / 記録有無 / location summary` を広く入力に含めます。
- mini モデル → 上位モデルの Pattern B を維持しています。
- debug summary には、過去30日件数・高評価/低評価サンプル件数・notes 使用件数・diary 不使用・token 数を出します。

#### sleep insights の入力ルール
- `trend_values` を常に構築し、値がなければ `null` のまま扱います。
- 少なくとも 7日平均 / 前日比 / 直近3日トレンド / 直近平均との差分 を含めます。
- sleep prompt には Today advice 向けの文言を入れません。
- sleep debug は full input dump と summary dump を分けて保存します。
- 入力が最低限しかない場合も、そのことが debug summary に残ります。

#### notify フラグ
- `email_disabled` のときは `mark_diary_notified` しません。
- 実際に通知送信が成功したときだけ notified フラグを立てる設計です。
- `already_notified` では notify だけをスキップし、生成ロジックは先に動きます。
- `missing_page_url` や送信失敗時も notified は更新しません。

### Phase D: publish mail
- `publish/render_mail.py` が payload に `today_advice` / sleep 系 / Diary を渡します。
- `publish/email_templates.py` は値があるセクションだけ描画します。
- メール本文の表示順は次のとおりです。
  1. `Today advice`
  2. `Sleep Analysis JP`
  3. `Today Condition Forecast JP`
  4. `就寝時間`
  5. `起床時間`
  6. `睡眠時間`
  7. `Diary`
  8. `Summary`
  9. `Expenses / Done / Drop / Meal`

## Daily Log DB に必要な sleep 系プロパティ

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

## 実装メモ

- `publish/read_daily_log.py` は sleep 系・Today advice 系プロパティを `DailyLogSummary` に揃えて返します。
- `scripts/daily_job.py` は Phase C の各保存後に Daily Log を再読込します。
- `scripts/diary_generator.py` の `event_date / done_date` ルールは維持しています。future event を当日実施と誤認しません。
- 現在の設計では **Today advice は当日 diary を参照しません**。`notes` は使いますが `diary` は使いません。

## Debug ログの見方

- Phase C のログプレフィックスは `phase_c_sleep_*` / `phase_c_today_advice_*` / `phase_c_diary_*` / `phase_c_notify_*` で統一しています。
- skip 理由は固定語彙で出します。主に `no_daily_log`, `no_sleep_signal`, `existing_today_advice`, `existing_diary`, `missing_page_url`, `already_notified`, `email_disabled` を使います。
- sleep insights debug は `debug/sleep_insights_*_full_YYYY-MM-DD.json` と `debug/sleep_insights_*_summary_YYYY-MM-DD.json` に分かれます。
- Today advice debug は stage ごとに input dump / summary / prompt を出します。
- いずれも secrets は含めません。

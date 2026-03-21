# notion-diary-automation

Notion Daily Log を中心に、Tasks / Expenses / Meal / Location summary / Mail の既存フローを維持したまま、睡眠データを正式対応させるための automation リポジトリです。今回の変更では、Health / Shortcut → Workers → Daily Log → sleep_condition_generator → diary generation → publish / mail の流れに、睡眠系プロパティを最小差分で接続しました。

## 全体構成

### Workflow 連鎖

1. **Phase A (Ingest)**
   - Daily Log を ensure します。
   - Tasks / Expenses / Health データを取り込みます。
   - Health 由来の睡眠値を Daily Log へ upsert します。
2. **Location Summary Writer**
   - 既存どおり Location summary を更新します。
3. **Phase C (Notify Diary)**
   - Daily Log を読み出します。
   - sleep input signal があれば、`Sleep Analysis` と `Today Condition Forecast` を毎回再生成して上書き保存します。
   - diary prompt に睡眠情報も渡し、日記を生成します。
4. **Phase B (Publish)**
   - 毎朝メール / publish で睡眠要約を必要十分な範囲で表示します。

### 睡眠連携の流れ

```text
Health / Shortcut
  ↓
Workers ingest_health
  ↓
Daily Log
  ↓
scripts/sleep_condition_generator.py
  ↓
Sleep Analysis / Today Condition Forecast を Daily Log に保存
  ↓
scripts/diary_generator.py
  ↓
publish / mail
```

## Notion 表示名と内部名の対応表

### Daily Log / Notion 表示名 → コード内部名

| Notion表示名 | 内部名 | 型の目安 | 主な利用 phase |
| --- | --- | --- | --- |
| Sleep Start | `sleep_start` | Date | Ingest / Notify / Publish |
| Sleep End | `sleep_end` | Date | Ingest / Notify / Publish |
| Sleep Duration | `sleep_duration_min` | Number | Ingest / Notify / Publish |
| Sleep Score | `sleep_score` | Number | Ingest / Notify |
| Sleep Source | `sleep_source` | Rich text or Select | Ingest / Notify |
| Sleep Heart Rate | `sleep_heart_rate` | Number | Ingest / Notify |
| Deep Duration | `deep_duration_min` | Number | Ingest / Notify |
| REM Duration | `rem_duration_min` | Number | Ingest / Notify |
| Readiness Stars | `readiness_stars` | Number | Ingest / Notify |
| Readiness HRV | `readiness_hrv` | Number | Ingest / Notify |
| Readiness BPM | `readiness_bpm` | Number | Ingest / Notify |
| Baseline HRV | `baseline_hrv` | Number | Ingest / Notify |
| Baseline Waking BPM | `baseline_waking_bpm` | Number | Ingest / Notify |
| Sleep Analysis | `sleep_analysis_jp` | Rich text | Notify / Publish |
| Today Condition Forecast | `today_condition_forecast_jp` | Rich text | Notify / Publish |

### Health DB で前提にする内部名

| Health DB property | 用途 |
| --- | --- |
| `sleep_start` | 就寝開始 |
| `sleep_end` | 起床 |
| `sleep_duration_min` | 睡眠時間（分） |
| `sleep_score` | 睡眠スコア |
| `sleep_source` | 睡眠データのソース |
| `sleep_heart_rate` | 睡眠時心拍 |
| `deep_duration_min` | 深睡眠時間（分） |
| `rem_duration_min` | REM時間（分） |
| `readiness_stars` | readiness 星評価 |
| `readiness_hrv` | readiness HRV |
| `readiness_bpm` | readiness BPM |
| `baseline_hrv` | baseline HRV |
| `baseline_waking_bpm` | baseline 起床時 BPM |

## Notion 側で必要な睡眠プロパティ一覧

### Daily Log に必要な列

- Sleep Start
- Sleep End
- Sleep Duration
- Sleep Score
- Sleep Source
- Sleep Heart Rate
- Deep Duration
- REM Duration
- Readiness Stars
- Readiness HRV
- Readiness BPM
- Baseline HRV
- Baseline Waking BPM
- Sleep Analysis
- Today Condition Forecast

### 実装上のルール

- コード内部では内部名を使用します。
- Notion 表示名との対応は Workers 内の定数で一元管理します。
- property 名比較は **大文字小文字を区別せず**、さらに **前後空白 / スペース / アンダースコア / ハイフン** の揺れを normalize して解決します。
- normalize 後に複数候補へ曖昧一致した場合は、自動更新せず warning を出して skip します。

## sleep 系が未入力だった場合の挙動

- Health 側に睡眠データがなければ、既存の Daily Log ingest は継続します。
- 個別プロパティが欠損していても、その項目だけ skip します。
- `Sleep Analysis` / `Today Condition Forecast` は既存値の有無に関係なく再生成対象ですが、今日データに睡眠 signal が無ければ gracefully skip します。
- OpenAI 呼び出しに失敗しても diary automation 全体は停止させません。
- 既存の JST 基準 target_date 運用は維持します。

## セットアップ手順

1. Health DB に睡眠内部名の列を追加します。
2. Daily Log に表示名の列を追加します。
3. 既存 env を設定します。
   - `NOTION_TOKEN`
   - `HEALTH_DB_ID`
   - `DAILY_LOG_DB_ID`
   - `TASK_DB_ID`
   - `EXPENSES_DB_ID`
   - `DAILY_LOG_UPSERT_URL`
   - `WORKERS_BEARER_TOKEN`
   - `OPENAI_API_KEY`
   - `PUBLIC_BASE_URL`
   - `MAIL_LINK_SECRET`
4. workflow をそのまま実行します。今回の変更で新規必須 env は増やしていません。

## 動作確認手順

1. Health DB に対象日の睡眠データを 1 件入れます。
2. Phase A を実行し、Daily Log に睡眠値が入ることを確認します。
3. Location Summary Writer を実行します。
4. Phase C を実行し、`Sleep Analysis` / `Today Condition Forecast` が毎回再生成・毎回上書き保存されることを確認します。
5. 同じ Daily Log から diary が生成されることを確認します。
6. Phase B を実行し、メールに以下が表示されることを確認します。
   - 就寝時間
   - 起床時間
   - 睡眠時間
   - Sleep Analysis
   - Today Condition Forecast

## よくある失敗例

- Notion に列だけ追加してコード未対応。
- `DailyLogSummary` を拡張しておらず、read 側に睡眠値が出ない。
- sleep insights は保存されるが read API が旧 property 名を見ていて取得できない。
- property 名の大文字小文字や `_` / `-` / space の不一致で更新できない。
- `Sleep Duration Min` や `Sleep Analysis JP` のような旧名を残してしまう。
- 既存値があるから sleep insights は更新されないと思い込んでしまう。

## 再発防止のための運用ポイント

- 新しい Notion property を追加するときは、**表示名** と **内部名** を必ずセットで管理してください。
- Daily Log write 側だけでなく、read 側 (`DailyLogSummary`) と prompt 入力側 (`build_diary_input_fields`) も同時に確認してください。
- property 参照を増やすときは、Workers の normalize helper を経由させてください。
- docs の対応表と workflow 実行順を更新して、Phase 間の依存関係を明示してください。

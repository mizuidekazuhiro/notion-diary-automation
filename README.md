# notion-diary-automation

Notion Daily Log を中心に、Tasks / Expenses / Meal / Location summary / Publish / Mail の既存フローを維持したまま、実際の Daily Log DB に存在する睡眠系プロパティ名へ合わせて最小差分で運用するためのリポジトリです。

## ワークフロー実行順

GitHub Actions は次の順番で実行されます。

| Order | Workflow name | Trigger / dependency |
| --- | --- | --- |
| 01 | `Daily Diary 01 - Ingest Daily Log` | schedule / manual |
| 02 | `Daily Diary 02 - Generate Location Summary` | `workflow_run` from `Daily Diary 01 - Ingest Daily Log` |
| 03 | `Daily Diary 03 - Generate Diary & Sleep Insights` | `workflow_run` from `Daily Diary 02 - Generate Location Summary` |
| 04 | `Daily Diary 04 - Publish Daily Mail` | `workflow_run` from `Daily Diary 03 - Generate Diary & Sleep Insights` |

## 睡眠系プロパティの正式ルール

### 正式な Notion 表示名

以下を正式名として扱います。

- Baseline HRV
- Baseline Waking BPM
- Deep Duration
- REM Duration
- Readiness BPM
- Readiness HRV
- Readiness Stars
- Sleep Analysis JP
- Sleep Duration
- Sleep End
- Sleep Heart Rate
- Sleep Score
- Sleep Source
- Sleep Start
- Today Condition Forecast JP

### Notion 表示名 ↔ 内部名 対応表

| 内部名 | 正式な Notion 表示名 |
| --- | --- |
| `sleep_start` | Sleep Start |
| `sleep_end` | Sleep End |
| `sleep_duration_min` | Sleep Duration |
| `sleep_score` | Sleep Score |
| `sleep_source` | Sleep Source |
| `sleep_heart_rate` | Sleep Heart Rate |
| `deep_duration_min` | Deep Duration |
| `rem_duration_min` | REM Duration |
| `readiness_stars` | Readiness Stars |
| `readiness_hrv` | Readiness HRV |
| `readiness_bpm` | Readiness BPM |
| `baseline_hrv` | Baseline HRV |
| `baseline_waking_bpm` | Baseline Waking BPM |
| `sleep_analysis_jp` | Sleep Analysis JP |
| `today_condition_forecast_jp` | Today Condition Forecast JP |

### 旧名からの置換

| 旧名 | 現在の扱い |
| --- | --- |
| Sleep Analysis | 読み取り alias は許容、書き込み先は必ず `Sleep Analysis JP` |
| Today Condition Forecast | 読み取り alias は許容、書き込み先は必ず `Today Condition Forecast JP` |
| Baseline Waking BPM | 正式対象。sleep GPT の `today_values` に含める |

## 実装ルール

- property 名比較は case-insensitive です。
- 前後空白、スペース、`_`、`-` の揺れを吸収して比較します。
- 書き込み時は DB に実在する正式プロパティ名を使います。
- normalize 後に複数候補へ一致した場合は warning を出して skip します。
- `Sleep Analysis JP` と `Today Condition Forecast JP` は既存値が入っていても Notify 実行のたびに再生成・上書きします。
- sleep 系入力が本当に無い日は insight 生成だけ gracefully skip します。
- JST 基準の `target_date` 処理は維持します。

## フロー

1. **Phase A (Ingest)**
   - Daily Log を ensure します。
   - Tasks / Expenses / Health を ingest します。
   - Health DB の sleep internal name を Daily Log の正式表示名へ保存します。
2. **Location Summary Writer**
   - 既存どおり `Location summary (GPT)` を更新します。
3. **Phase C (Notify Diary)**
   - Daily Log を読み出します。
   - sleep 入力があれば `Sleep Analysis JP` / `Today Condition Forecast JP` を毎回再生成して保存します。
   - Sleep GPT には「当日値」と「一部項目のみの 7 日平均 / 差分」を渡します。
   - 同じ Daily Log を diary prompt に渡して Diary を生成します。
4. **Phase B (Publish)**
   - メール / publish では `Sleep Start` / `Sleep End` / `Sleep Duration` / `Sleep Analysis JP` / `Today Condition Forecast JP` のうち値があるものだけ表示します。

## 動作確認の要点

1. Health DB に `sleep_start` などの内部名列が入っていることを確認します。
2. Daily Log DB に正式表示名の列があることを確認します。
3. Phase A 実行後、Daily Log に正式表示名の sleep 値が入ることを確認します。
4. Phase C を 2 回実行し、`Sleep Analysis JP` と `Today Condition Forecast JP` が毎回更新されることを確認します。
5. Phase B 実行後、メールに sleep セクションが必要な項目だけ表示されることを確認します。

## Sleep GPT に渡す項目

`scripts/sleep_condition_generator.py` では、「13項目を ingest すること」と、「平均・差分を付与する項目」は別管理です。

### `today_values` に渡す全項目

- `sleep_start`
- `sleep_end`
- `sleep_duration_min`
- `sleep_score`
- `sleep_source`
- `readiness_stars`
- `readiness_hrv`
- `readiness_bpm`
- `baseline_hrv`
- `baseline_waking_bpm`
- `sleep_heart_rate`
- `deep_duration_min`
- `rem_duration_min`

### 直近7日平均 (`*_7d_avg`) を計算する項目

- `sleep_duration_min`
- `sleep_score`
- `readiness_hrv`
- `readiness_bpm`
- `deep_duration_min`
- `rem_duration_min`

### 当日値との差分 (`*_delta_vs_7d`) を計算する項目

- `sleep_duration_min`
- `sleep_score`
- `readiness_hrv`
- `readiness_bpm`

> 補足: README 上の「当日値 + 直近7日平均 + 差分」は、全13項目すべてに平均・差分を付ける意味ではありません。平均・差分は上記の一部項目のみに付与されます。

## 補足

- workflow の Node 24 警告対応として、対象 workflow は `actions/checkout@v5` と `actions/setup-python@v6` を利用します。
- `Daily LogSummary`、Workers schema validation、generate_diary 保存、publish / mail 表示はすべて上記正式名へ揃えてあります。

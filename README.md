# notion-diary-automation

Notion Daily Log を中心に、Tasks / Expenses / Meal / Location summary / Publish / Mail の既存フローを維持しつつ、朝レビュー用の `Today advice`、睡眠分析、Diary 生成を段階的に更新するリポジトリです。

## ワークフロー実行順

GitHub Actions は次の順番で実行されます。

| Order | Workflow name | Trigger / dependency |
| --- | --- | --- |
| 01 | `Daily Diary 01 - Ingest Daily Log` | schedule / manual |
| 02 | `Daily Diary 02 - Generate Location Summary` | `workflow_run` from `Daily Diary 01 - Ingest Daily Log` |
| 03 | `Daily Diary 03 - Generate Diary & Sleep Insights` | `workflow_run` from `Daily Diary 02 - Generate Location Summary` |
| 04 | `Daily Diary 04 - Publish Daily Mail` | `workflow_run` from `Daily Diary 03 - Generate Diary & Sleep Insights` |

## Today advice 機能

- 新しい Daily Log プロパティとして **`Today advice`** を追加して使います。
- 実行タイミングは朝のショートカット起点フローの **Phase C (Notify Diary / 生成系フェーズ)** です。睡眠など必要データが反映された後、当日データがまだ未完成の前提で生成します。
- 参照期間は **過去30日** です。
- Mood は **高評価日=4/5、低評価日=1/2、中間日=3** として扱います。
- 比較は狭い集計だけで閉じず、以下の3層を GPT に渡します。
  - **A. 今日朝の状態**: 今朝の睡眠、直近数日の睡眠推移、昨日までの Done / Drop / 支出 / PFC / Notes 記録状況、当日未完成である旨。
  - **B. 過去30日の構造化比較**: 高評価日数・低評価日数・中間日数、睡眠/Done/Drop/支出/PFC/Notes 記録率などの基本比較。
  - **C. 生データの日次サンプル**: 高評価日5件、低評価日5件をできるだけ偏らせずに抽出し、Notes / Diary / Location summary を含む主要項目を要約せずにそのまま渡します。不足時は取得できた件数でフォールバックします。
- **Pattern B** で実装しています。
  - mini 系モデル: 過去30日の材料整理、差分観察、Notes / Diary / Location summary / 記録状況のシグナル抽出。
  - 上位モデル: mini の整理結果と今朝の状態を読んで、当日に効きそうな論点を選び、自然な日本語の `Today advice` を生成。
- 生成した `Today advice` は **Daily Log の `Today advice` に保存** し、**朝メール本文の一番最初** に表示します。
- 睡眠分析や Diary 生成とは責務を分け、実装は `scripts/mood_advice_generator.py` に切り出しています。

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
- Today advice

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
| `sleep_analysis_jp` | Sleep Analysis JP |
| `today_condition_forecast_jp` | Today Condition Forecast JP |
| `today_advice` | Today advice |

## フロー

1. **Phase A (Ingest)**
   - Daily Log を ensure します。
   - Tasks / Expenses / Health を ingest します。
2. **Location Summary Writer**
   - 既存どおり `Location summary (GPT)` を更新します。
3. **Phase C (Notify Diary / 生成フェーズ)**
   - Daily Log を読み出します。
   - sleep 入力があれば `Sleep Analysis JP` / `Today Condition Forecast JP` を毎回再生成して保存します。
   - `scripts/mood_advice_generator.py` が過去30日の Daily Log を収集し、Mood 正規化、高評価/低評価抽出、mini 向け材料整理、上位モデルによる `Today advice` 生成を実行します。
   - `Today advice` を Daily Log に保存します。
   - 同じ Daily Log を diary prompt に渡して Diary を生成します。
4. **Phase B (Publish)**
   - メール本文では `Today advice` を最初に表示し、その後に既存の Diary / Sleep / Summary などを続けます。

## Today advice 実装メモ

- 実装ファイル: `scripts/mood_advice_generator.py`
- 主な責務:
  - 過去30日の Daily Log 取得
  - Mood の星表現正規化
  - 高評価日 / 低評価日 / 中間日の仕分け
  - 今朝の状態と直近数日の流れの整理
  - mini 向け入力構築と構造化出力取得
  - 上位モデル向け入力構築と `Today advice` 生成
  - Daily Log 更新用返却値の生成
- 記録漏れ(PFC未記録、Notes未記録など)は欠損ではなく生活管理のシグナルとして扱いますが、コード側では因果を断定しません。
- 高評価日/低評価日が5件未満の場合は、取得できた範囲でサンプルを渡します。

## 動作確認の要点

1. Daily Log DB に `Today advice` を追加します。
2. 朝フローの Phase C 実行後、Daily Log に `Today advice` が保存されることを確認します。
3. 同日の Publish 実行後、メール本文冒頭に `Today advice` が出ることを確認します。
4. 睡眠系の既存生成と Diary 生成が従来どおり動くことを確認します。

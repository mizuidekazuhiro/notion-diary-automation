# notion-diary-automation

Notion の Daily Log を中心に、前日のデータを **Phase A: ingest → Phase B: publish source prep → Phase C: generate/notify → Phase D: publish mail** とつなぐ自動化リポジトリです。現在の GitHub Actions では Phase B は `Location summary (GPT)` 更新として実装され、Phase C は sleep insights / Today advice / Diary の生成と通知判定を担当します。既定の `target_date` は JST 前日ですが、Phase C は `--target-date` または `TODAY_ADVICE_TARGET_MODE=TODAY` で当日朝レビューにも切り替えられます。

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
- `scripts/mood_advice_generator.py` の Today advice 入力は **`today_sleep` / `historical_behavior_patterns` / `historical_recording_patterns` / `historical_context`** に役割分離されます。
- Phase C の Today advice は **today sleep only / non-sleep historical only** を守ります。sleep 以外の当日データは Today advice の責務外です。
- Diary は引き続き Daily Log 全体の振り返りを文章化しますが、Today advice は「当日の睡眠コンディション」と「過去実績の行動傾向」を短く接続する専用レイヤです。

#### Today advice の入力ルール
- 当日参照してよいのは **sleep 系のみ** です。
- 当日参照に含めるのは `sleep_analysis_jp` / `today_condition_forecast_jp` / `Sleep Start` / `Sleep End` / `Sleep Duration` / `Sleep Score` / `Sleep Heart Rate` / `Deep Duration` / `REM Duration` / `Readiness Stars` / `Readiness HRV` / `Readiness BPM` / `Baseline HRV` / `Baseline Waking BPM` など、sleep insights 系の構造化データだけです。
- `meal / done / drop / spend / notes / 記録有無 / location summary` は **当日値を使わず**、過去7日・14日・30日や mood 高低日の差分比較などの **過去実績のみ**で扱います。
- good mood / low mood 比較では `done / drop / spend` に加えて、`kcal / protein / fat / carb` の meal 数値、過去 `notes` から抽出した signal、`location summary` 由来の location pattern も見ます。
- diary 本文は Today advice の現在値・過去値ともに使いません。notes は過去履歴のみ使い、当日 notes は使いません。
- 当日の未入力・未完了・ゼロ件は評価対象にしません。
- Today advice は **「今日の睡眠コンディション × 過去の行動実績パターン」** だけで作ります。
- 本文には **必ず直近7日間の行動・記録傾向** を 1 つ以上含めます。sleep の話だけで終わらせません。
- 過去30日を使います。
- 高評価日は mood 4/5、低評価日は 1/2、中間日は 3 です。
- 高評価5件・低評価5件は、可能な限り偏らないように抽出します。不足時はその件数でフォールバックします。
- diary 本文 / 過去 diary 本文は使いません。
- diary 本文 / 過去 diary 本文 / diary 由来要約は使いません。
- `notes` は **過去履歴のみ** で使います。当日 notes は使いません。
- `location summary` は **過去履歴のみ** の構造化コンテキストとして使います。当日 location summary は使いません。
- 当日 `meal / done / drop / spend / notes / location summary` は LLM 入力に含めません。
- 当日 `meal_logged=false` / `spend_total=0` / `done_count=0` / `drop_count=0` / notes 空 / location summary 空 などは、途中経過として扱い、Today advice ではネガティブ評価しません。
- `Diary` / 過去 `Diary` は引き続き入力に含めません。
- mini モデル → 上位モデルの Pattern B を維持しています。
- 「最近の傾向」は当日値ではなく、過去7日・14日・30日の集計と比較から判断します。主軸は 7 日傾向で、14日・30日比較は補助です。
- debug summary には、today_sleep の主要キー、recent_7d / recent_14d / recent_30d の主要集計、good/bad 差分、meal 比較、notes signal 比較、location pattern 比較、evidence_used を要約で出します。full dump は debug ファイルへ保存します。

#### Today advice の出力ルール
- 本文は次の 3 要素を必ずこの順に含めます。
  1. 今日の睡眠状態から見たコンディション
  2. 直近7日間の行動・記録傾向と good/bad day 比較から見える再現パターン
  3. 今日まず取るべき具体行動
- 行動提案は 1〜2 個に絞ります。
- 本文は 220〜380 字程度、3 文構成を基本とし、sleep の話だけで終わらせません。
- recent 7-day trend を最低1つ、good/bad day comparison 由来の示唆を可能なら最低1つ含めます。
- 因果は断定せず、「傾向」「重なり」「示唆」に留めます。
- 一般論は避け、事実 → 解釈 → 今日の行動の順でつなぎます。
- 支出から感情を断定しません。
- 食事未記録から健康状態を断定しません。
- `diary` 本文 / 過去 `diary` 本文は使いません。
- 日本語自由記述として使うのは過去 `notes` のみです。
- `location summary` は過去履歴の構造化コンテキストに限定します。

#### target_date の扱い
- `scripts/daily_job.py` の既定 target date は JST 前日です。
- `--target-date YYYY-MM-DD` を指定すると、全 Phase でその日付を明示利用できます。
- `TODAY_ADVICE_TARGET_MODE=TODAY` を指定した場合、`--phase notify_diary` または `--phase all` の Phase C は JST 当日を target date にします。
- `TODAY_ADVICE_TARGET_MODE` の既定値は `YESTERDAY` です。
- 当日モードでも Today advice のルールは同じで、**today_sleep は当日、non-sleep は historical only** のままです。

#### Phase C の再実行ルール
- sleep insights は従来どおり実行し、保存後に Daily Log を再読込します。
- Today advice は `Today Advice Input Hash` を使って差分判定します。
  - `today_advice` が既に存在していても、入力 hash が前回と異なれば再生成して上書きします。
  - `today_advice` が存在し、かつ入力 hash が同一なら `skip_reason=unchanged_input` でスキップします。
- Diary は `Diary Input Hash` を使って差分判定します。
  - `diary` が既に存在していても、入力 hash が前回と異なれば再生成して上書きします。
  - `diary` が存在し、かつ入力 hash が同一なら `skip_reason=unchanged_input` でスキップします。
- notify の重複防止は別判定です。`already_notified` でも generate 部分は先に動き、notify だけをスキップします。
- Phase C の順序は固定です。Today advice 更新結果を再読込した後に Diary を生成するため、Diary hash には最新の Today advice が反映されます。

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

## Daily Log DB に必要なプロパティ

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
- `Diary Input Hash`
- `Today Advice Input Hash`
- `Diary Generated At`
- `Today Advice Generated At`

推奨型:
- `Diary Input Hash`: `rich_text`
- `Today Advice Input Hash`: `rich_text`
- `Diary Generated At`: `date` または `datetime` 互換の `date`
- `Today Advice Generated At`: `date` または `datetime` 互換の `date`

## 実装メモ

- `publish/read_daily_log.py` は sleep 系・Today advice 系プロパティを `DailyLogSummary` に揃えて返します。
- `scripts/daily_job.py` は Phase C の各保存後に Daily Log を再読込します。
- `scripts/diary_generator.py` の `event_date / done_date` ルールは維持しています。future event を当日実施と誤認しません。
- 現在の設計では **Today advice は diary 本文を現在・過去とも参照しません**。`notes` は過去履歴のみ使い、当日 `notes` は使いません。
- hash は JSON 正規化 + SHA-256 で作ります。キー順固定・余計な空白なし・`None`/空文字/空配列の揺れを吸収して、不要な再生成を抑えます。
- Diary hash には、実際に `scripts/diary_generator.py` に渡す入力一式が入ります。これには `notes` / `done` / `drop` / `expenses` / `meal summary` / `location summary` / sleep 系 / `today_advice` など、Diary 出力に影響する項目が含まれます。
- Today advice hash には、`today_sleep` と historical-only の `historical_behavior_patterns` / `historical_recording_patterns` / `historical_context`、および過去比較サマリが含まれます。当日 non-sleep 値は hash と prompt の責務から除外します。

## Today advice 30日分析パイプライン（新規）

Today advice は従来の Phase C 分離を維持したまま、内部で次の 6 段階に強化しました。

1. **過去30日 Notes 一括ラベル化**  
   `scripts/note_batch_labeler.py` が過去30日 Notes をまとめて GPT へ渡し、日次ラベル JSON（sentiment / fatigue / stress / social_load / achievement / self_care / sleep_issue）を返します。空Notesや JSON パース失敗時は neutral フォールバックです。
2. **日次特徴量テーブル作成**  
   `scripts/today_advice_feature_builder.py` が pandas DataFrame を作り、sleep / mood / task / spending / notes ラベル特徴を 1 日 1 行で統合します。
3. **条件別翌日悪化率（lag1）集計**  
   `scripts/today_advice_pattern_analyzer.py` が `prev_* -> next_day_*` の hit_rate, baseline, delta, confidence を算出し、採用条件を満たすパターンだけを候補化します。
4. **ロジスティック回帰（補助分析）**  
   `scripts/today_advice_regression.py` が翌日 low mood を目的変数に LogisticRegression を実行し、上位リスク特徴と保護特徴を補助情報として出します（成立しない場合はスキップ）。
5. **分析済み JSON 生成**  
   `scripts/today_advice_renderer.py` が `today_sleep_context`, `recent_7d_summary`, `matched_patterns`, `regression_summary`, `risk_level`, `primary_focus` をまとめた分析済み JSON を生成します。
6. **分析済み JSON のみで本文生成**  
   GPT には生の30日 Notes を再投入せず、分析済み JSON のみを渡して Today advice 日本語本文を作ります。GPT 失敗時はルールベース文へフォールバックします。

### Today advice 分析監査ログ（analysis_audit）

Today advice の精度改善より先に、分析過程を追跡できるよう監査ログを追加しています。`scripts/mood_advice_generator.py` は Today advice 生成中に次を段階ログとして出力します。

- A. データ取得 (`[TodayAdvice][Fetch]`): 分析期間、取得件数、欠損件数。
- B. Notes ラベル化 (`[TodayAdvice][Notes]`): 対象件数、API 呼び出し件数、感情/フラグ件数（Notes 本文全文は出力しない）。
- C. 特徴量作成 (`[TodayAdvice][Features]`): DataFrame 行列数、作成列、主要フラグ件数。
- D. lag 分析 (`[TodayAdvice][Lag]`): `pattern_id / target_outcome / sample_size / hit_rate / baseline_rate / delta / adopted / confidence`。
- E. 回帰分析 (`[TodayAdvice][Regression]`): 実行可否、サンプル数、上位特徴量、スキップ理由。
- F. 今日一致判定 (`[TodayAdvice][TodayMatch]`): 当日 sleep 文脈、一致ルール、risk/focus。
- G. GPT 入力分析 JSON (`[TodayAdvice][AnalysisJSON]`)
- H. 最終本文 (`[TodayAdvice][FinalText]`)

`TODAY_ADVICE_DEBUG=true` の場合は、上記サマリに加えて最終的な構造化 JSON 監査ログを1つにまとめて出力します。

- 環境変数: `TODAY_ADVICE_DEBUG=true|false`（既定: `false`）
- 詳細出力キー: `[TodayAdvice][AnalysisAudit] {"analysis_audit": {...}}`

`analysis_audit` の主キー:

- `target_date`
- `fetch`
- `notes_labeling`
- `features`
- `lag_analysis`
- `regression`
- `today_match`
- `analysis_json`
- `final_text`

確認ポイント:

- 「30日分が取れているか」→ `fetch.fetched_count` / `fetch.usable_rows_count`
- 「Notes が分析に入っているか」→ `notes_labeling.non_empty_count` / `notes_labeling.flag_counts`
- 「非 sleep の過去実績が analysis JSON に残るか」→ `analysis_json.recent_7d_summary`, `analysis_json.matched_patterns`, `analysis_json.regression_summary`
- 「どの条件が採用されたか」→ `lag_analysis.patterns[*].adopted`
- 「本文の根拠追跡」→ `analysis_json` と `final_text`

### なぜ1日ずつではなく一括ラベル化するか
- API 呼び出し回数を減らし、コストと待ち時間を削減するため。
- 指示文の重複送信を避け、判定基準の一貫性を上げるため。
- JSON 一括返却で後段の DataFrame/分析処理を単純化するため。

### 追加ライブラリ
- `pandas`: 日次特徴量テーブル作成
- `numpy`: 欠損・数値補助
- `scikit-learn`: LogisticRegression
- `statsmodels`: 回帰分析拡張のための将来互換（現時点は補助）

### ルール再確認
- Today advice は **today sleep only / non-sleep historical only** を維持します。
- 当日 Notes は today advice に使いません（Notes は過去30日の履歴分析のみ）。
- 回帰分析は補助情報で、最終判定は条件別集計（lag パターン）を優先します。
- OpenAI 失敗時も neutral ラベルと本文フォールバックで Phase C を止めません。

## Debug ログの見方

- Phase C のログプレフィックスは `phase_c_sleep_*` / `phase_c_today_advice_*` / `phase_c_diary_*` / `phase_c_notify_*` で統一しています。
- skip 理由は固定語彙で出します。主に `no_daily_log`, `no_sleep_signal`, `unchanged_input`, `missing_page_url`, `already_notified`, `email_disabled` を使います。
- sleep insights debug は `debug/sleep_insights_*_full_YYYY-MM-DD.json` と `debug/sleep_insights_*_summary_YYYY-MM-DD.json` に分かれます。
- Today advice debug は stage ごとに input dump / summary / prompt を出します。
- Phase C の Today advice / Diary ログには `current_input_hash`, `previous_input_hash`, `input_hash_changed`, 入力件数サマリを含めます。
- 代表的な流れ:
  - `phase_c_today_advice_start`
  - `phase_c_today_advice_input_summary ... debug_summary=...`
  - `phase_c_today_advice_skip ... skip_reason=unchanged_input`
  - `phase_c_today_advice_saved updated=false`
  - `phase_c_diary_start`
  - `phase_c_diary_input_summary ... debug_summary=...`
  - `phase_c_diary_saved updated=true/false`
- いずれも secrets は含めません。

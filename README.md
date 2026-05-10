# notion-diary-automation

Notion の Daily Log を中心に、前日のデータを **Phase A: ingest → Phase B: publish source prep → Phase C: generate/update → Phase D: publish mail** とつなぐ自動化リポジトリです。現在の GitHub Actions では Phase B は `Location summary (GPT)` 更新として実装され、Phase C は sleep insights / Today advice / Diary の生成・Notion更新のみを担当します。既定の `target_date` は JST 前日ですが、Phase C は `--target-date` または `TODAY_ADVICE_TARGET_MODE=TODAY` で当日朝レビューにも切り替えられます。

## Workflow 名と依存関係

| Order | Workflow name | Trigger | 実責務 |
| --- | --- | --- | --- |
| 00 | `CI - Test & Requirements Gate` | `push` / `pull_request` | pytest + requirements/workflow contract checks（deployの前提） |
| 01 | `Daily Diary 01 - Ingest Daily Log` | `workflow_dispatch` | Phase A: Daily Log の ensure / ingest |
| 02 | `Daily Diary 02 - Generate Location Summary` | `workflow_run` from 01 / manual | Phase B: `Location summary (GPT)` 更新 |
| 03 | `Daily Diary 03 - Generate Diary & Sleep Insights` | `workflow_run` from 02 / manual | Phase C: sleep insights → Today advice → Diary（生成・Notion更新のみ） |
| 04 | `Daily Diary 04 - Publish Daily Mail` | `workflow_run` from 03 / manual | Phase D: 朝メール配信 |

`workflow_run.workflows` は上記 `name:` と一致しています。README の名称・YAML の `name:`・依存先は同じです。

補足:
- `Deploy Cloudflare Workers` は `main` ブランチで `CI - Test & Requirements Gate` が成功した後にのみ自動実行されます（`workflow_run` 連携）。
- 手動 `workflow_dispatch` でも deploy できますが、通常運用は CI 成功後の自動 deploy を前提にします。

## Phase ごとの最終仕様

> 最新運用メモ（F 関連）:
> - Expense F の正は Expenses DB の `F` チェックボックスで、ユーザー手動付与の実績ラベル（教師データ）です。
> - Daily Log メールでは対象日の `F=true` 支出を Expenses DB 直読で表示します（Expense F 集計の Daily_Log 保存はデフォルト無効）。
> - Expense F 集計を Daily_Log へ保存したい場合のみ `SAVE_EXPENSE_F_SUMMARY_TO_DAILY_LOG=true` を有効化します。
> - F Risk は事後通知ではなく予防アラートで、主状態管理は `automation-state` ブランチ `.state/f_risk_state.json`（ローカル `.runtime/f_risk_state.local.json`）です。
> - F Risk の Daily_Log 保存はデフォルト無効で、保存したい場合のみ `SAVE_F_RISK_TO_DAILY_LOG=true` を有効化します。
> - Notes Label の Daily_Log 保存もデフォルト無効で、保存したい場合のみ `SAVE_NOTES_LABEL_TO_DAILY_LOG=true` を有効化します。

### Phase A: ingest
- Daily Log ページを ensure します。
- Tasks / Health / Expenses を Daily Log に取り込みます。

### Phase B: publish source prep
- `apps/location_summary_writer` が `Location summary (GPT)` を更新します。
- ここでは Today advice / sleep insights / Diary は生成しません。

### Phase C: generate/update
`scripts/daily_job.py --phase notify_diary` は次の順番で**直列実行**します。

1. weather 生成（最新 location 解決 → 天気 API）
2. weather 保存
3. Expenses DB の `F` 日次集計保存
4. sleep insights 生成
5. sleep insights 保存
6. Daily Log 再読込
7. F risk 生成（Today advice と独立）
8. F risk 保存
9. Daily Log 再読込
10. Today advice 生成
11. Today advice 保存
12. Daily Log 再読込
13. Diary 生成
14. Diary 保存
15. Daily Log 再読込
16. 生成結果の更新完了ログ（メール通知は送信しない）

> Expense F 集計（3）は Workers 経由ではなく `scripts/expense_f_aggregator.py` が `NOTION_TOKEN` と `EXPENSES_DB_ID` を使って Notion API を直接参照します。

#### Phase C 実行に必要な secrets / env（最低限）
- `NOTION_TOKEN`
- `EXPENSES_DB_ID`
- `OPENAI_API_KEY`
- `DAILY_LOG_UPSERT_URL`
- `WORKERS_BEARER_TOKEN`
- `LOCATION_LOG_DB_ID`
- `LOCATION_LOG_TIME_PROP`（未設定時 `Time`）
- `LOCATION_LOG_PLACE_PROP`（未設定時 `Place`）

#### Weather の地点解決（確定仕様）
- Location Log DB を `Time` 降順で取得し、**最新1件**を採用します（`LOCATION_LOG_TIME_PROP` 既定値: `Time`）。
- 緯度経度は最新1件の固定プロパティ **`Latitude (raw)` / `Longitude (raw)` を最優先**で参照します。
- 固定名が空のときのみ alias (`latitude/lat/...`, `longitude/lon/lng/...`) を補助利用します。
- lat/lon が未解決のときのみ、同じ最新1件の `Place`（`LOCATION_LOG_PLACE_PROP` 既定値: `Place`）を geocode します。
- geocode 前に住所文字列を正規化します（郵便番号除去・末尾国名整理）。
- fallback 順序は `Location Log latest lat/lon` → `Location Log latest Place geocode` → `Daily Log Place` → `Daily Log Location summary` → `東京都` です。
- ログは `query_status`, `latest_selected_page_id`, `latest_selected_time`, `effective_time_prop`, `effective_place_prop`, `resolved_lat_prop`, `resolved_lon_prop`, `latlon_available`, `geocode_attempted`, `geocode_query`, `fallback_used`, `weather_status` を出します。

#### Expense F の schema 解決方針
- Expense F の日次帰属は **Notion page `created_time` を唯一の基準**にします（`Date` / `Received At` は補助 debug）。
- 主経路の必須解決は `F`, `Merchant`, `Amount` のみです。`Category` は任意です。
- クエリは `timestamp=created_time` の期間 filter + sort で統一します。
- `resolved_props`, `created_time_source`, `date_window_start`, `date_window_end`, `filter_strategy`, `query_exception_class`, `query_exception_message`, `matched_count`, `total_amount` をログへ出します。
- `data_status` は `ok` / `no_results` / `query_failed` / `schema_unresolved` を厳密に使い分けます。

#### Today advice の睡眠 source of truth
- 睡眠は `mood_advice_generator.py` の `selected_sleep_candidate` を唯一の source of truth とします。
- renderer へ `today_sleep_context` を渡し、renderer 側で再判定しません。
- LightGBM の寄与表示は target_date の睡眠値（`sleep_hours` / `sleep_score`）で override した `today_contribution_features` を使い、`feature_row_date_used_as_today` と `lightgbm_explanation_source_row_date` を必ず出します。
- ログは `sleep_candidates` / `selected_sleep_candidate` / `selected_candidate_source` / `renderer_received_sleep_context` / `final_today_sleep_context` / `feature_row_date_used_as_today` を出します。

#### Phase C のメール通知扱い
- `notify_diary` phase では設計上メール送信を行いません。
- Weather / Expense F / Sleep insights / F risk / Today advice / Diary の生成保存は従来どおり実行されます。
- 本体メール送信は Phase D (`--phase publish`) のみで実施します。

#### 役割分離
- `scripts/sleep_condition_generator.py` は **`sleep_analysis_jp` / `today_condition_forecast_jp` の2項目だけ**生成します。
- `scripts/mood_advice_generator.py` は **`today_advice` だけ**生成します。
- `scripts/f_risk_generator.py` は **`F Risk Alert` 系だけ**生成します（Today advice とは独立責務）。
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
- notify の重複防止は別判定です。hash（subject + body）一致時のみ notify をスキップし、generate 部分は先に動きます。
- Phase C の順序は固定です。Today advice 更新結果を再読込した後に Diary を生成しますが、Diary 入力は raw inputs only であり generated field（Today advice / Sleep Analysis JP / Today Condition Forecast JP）は使いません。

#### sleep insights の入力ルール
- `trend_values` を常に構築し、値がなければ `null` のまま扱います。
- 少なくとも 7日平均 / 前日比 / 直近3日トレンド / 直近平均との差分 を含めます。
- sleep prompt には Today advice 向けの文言を入れません。
- sleep debug は full input dump と summary dump を分けて保存します。
- 入力が最低限しかない場合も、そのことが debug summary に残ります。

#### notify フラグ
- `email_disabled` のときは `mark_diary_notified` しません。
- 実際に通知送信が成功したときだけ notified フラグを立てる設計です。
- `missing_page_url` や送信失敗時も notified は更新しません。
- notify_diary phase ではメール通知を送りません（Daily Log 更新のみ）。

### Phase D: publish mail
- `publish/render_mail.py` が payload に weather / `today_advice` / 司法試験 Study / F alert / sleep 系 / Diary を渡します。
- `publish/email_templates.py` は値があるセクションだけ描画します。
- 送信判定はメール本文ではなく Notion 由来の入力データ差分（`Mail Input Hash`）で行います。
- `Mail Input Hash` が前回と同じ場合は送信・送信メタ更新をスキップし、変化時のみ更新版（件名 `【更新版】...`）を送信します。
- メール本文の表示順は次のとおりです。
  1. `Weather`
  2. `Today advice`
  3. `F Risk Alert`（alert がある日だけ表示）
  4. `Sleep Analysis JP`
  5. `Today Condition Forecast JP`
  6. `就寝時間`
  7. `起床時間`
  8. `睡眠時間`
  9. `Diary`
  10. `Summary`
  11. `Expenses / Done / Drop / Meal`

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
- `Study Minutes`
- `Study Sessions`
- `Study Last Used At`
- `Today Condition Forecast JP`
- `Today advice`
- `Diary Input Hash`
- `Diary Notification Hash`
- `Diary Notification Sent At`
- `Diary Notification Version`
- `Today Advice Input Hash`
- `Diary Generated At`
- `Today Advice Generated At`
- `Weather Location`
- `Weather`
- `Weather Summary`
- `Weather Temp Max C`
- `Weather Temp Min C`
- `Weather Precip Probability Max`
- `Weather Code`
- `Weather Retrieved At`
- `Weather Input Hash`
- `Weather Generated At`
- `Mail Input Hash`
- `Mail Input Snapshot`
- `Mail Sent At`
- `Mail Version`
- `Expense F Count`
- `Expense F Total`
- `Expense F Merchants`
- `Expense F Categories`
- `Expense F First Time`
- `Expense F Last Time`
- `Expense F Data Status`
- `F Risk Alert`
- `F Risk Score`
- `F Risk Reason`
- `F Risk Matched Patterns`
- `F Risk Input Hash`
- `F Risk Generated At`

推奨型:
- `Diary Input Hash`: `rich_text`
- `Diary Notification Hash`: `rich_text`
- `Diary Notification Sent At`: `date`
- `Diary Notification Version`: `number`
- `Today Advice Input Hash`: `rich_text`
- `Diary Generated At`: `date` または `datetime` 互換の `date`
- `Today Advice Generated At`: `date` または `datetime` 互換の `date`
- `Weather Temp Max C` / `Weather Temp Min C` / `Weather Precip Probability Max`: `number`
- `Weather Code`: `number`
- `Study Minutes`: `number`
- `Study Sessions`: `number`
- `Study Last Used At`: `date`
- `Expense F Count`: `number`
- `Expense F Total`: `number`
- `Expense F Merchants` / `Expense F Categories` / `F Risk Alert` / `F Risk Reason` / `F Risk Matched Patterns`: `rich_text`
- `F Risk Score`: `number`

## 実装メモ

- `publish/read_daily_log.py` は sleep 系・Today advice 系プロパティを `DailyLogSummary` に揃えて返します。
- weather / Expense F 集計 / F risk 系プロパティも `DailyLogSummary` へ追加し、mail / feature builder / Phase C で再利用します。
- `scripts/daily_job.py` は Phase C の各保存後に Daily Log を再読込します。
- weather の地点解決は、Location Log の最新1件を基準にし、lat/lon がある場合は最優先で使います。lat/lon が無い場合のみ place geocode を使い、届かない場合は `place` / `location_summary` / `東京都` へフォールバックします。
- weather API は key 不要の Open-Meteo（geocoding + forecast）を利用し、失敗時は Phase C 全体を落とさず `phase_c_weather_skip` を出します。
- weather roundtrip compare は「今回保存した非空フィールドのみ」を比較対象にし、未取得・未保存のフィールドは `ignored_fields` に出します。
- F risk は `f_event_flag = Expense F Count > 0` を主ラベルとして機械学習（LightGBM 優先、失敗時 LogisticRegression）を実行し、`insufficient_samples` / `single_class_target` / `no_f_history` などを skip_reason で記録します。
- Today advice と F risk は責務・入力・保存先・ログを分離しています。
- `scripts/diary_generator.py` の `event_date / done_date` ルールは維持しています。future event を当日実施と誤認しません。
- 現在の設計では **Today advice は diary 本文を現在・過去とも参照しません**。`notes` は過去履歴のみ使い、当日 `notes` は使いません。
- hash は JSON 正規化 + SHA-256 で作ります。キー順固定・余計な空白なし・`None`/空文字/空配列の揺れを吸収して、不要な再生成を抑えます。
- Diary hash には、実際に `scripts/diary_generator.py` に渡す raw input 一式のみが入ります。`Today advice` / `Sleep Analysis JP` / `Today Condition Forecast JP` は hash と prompt から除外されます。
- Today advice hash には、`today_sleep` と historical-only の `historical_behavior_patterns` / `historical_recording_patterns` / `historical_context`、および過去比較サマリが含まれます。当日 non-sleep 値は hash と prompt の責務から除外します。

## Today advice 30〜60日分析パイプライン（Python分析 + GPT説明）

Today advice は従来の Phase C 分離を維持したまま、**分析本体を Python、説明文生成のみを GPT** に再設計しました。内部処理は次の段階です。

1. **過去30日 Notes 一括ラベル化**  
   `scripts/note_batch_labeler.py` が過去30日 Notes をまとめて GPT へ渡し、日次ラベル JSON（sentiment / fatigue / stress / social_load / achievement / self_care / sleep_issue）を返します。空Notesや JSON パース失敗時は neutral フォールバックです。
2. **日次特徴量テーブル作成**  
   `scripts/today_advice_feature_builder.py` が pandas DataFrame を作り、sleep / mood / task / spending / notes ラベル特徴を 1 日 1 行で統合します。
3. **探索型分析（単変量 + 組み合わせ）**  
   `scripts/today_advice_pattern_analyzer.py` が 30〜60 日の特徴量を広く見て、`next_day_low_mood_flag` を主目的変数に単変量差分・相関方向・特徴量組み合わせパターン（条件の組）を抽出します。固定 if ルールは主軸にしません。
4. **回帰分析（補助）**  
   `scripts/today_advice_regression.py` が翌日 low mood を目的変数に LogisticRegression を実行し、上位リスク特徴と保護特徴を補助情報として出します（サンプル不足時は自動スキップ）。
5. **LightGBM（探索主力）**  
   `scripts/today_advice_lightgbm.py` が `next_day_low_mood_flag` を目的変数に `LGBMClassifier` を実行し、feature importance / リスク寄与上位 / 保護寄与上位 / 当日予測確率（可能時）を返します。失敗時は `insufficient_samples` / `single_class_target` / `unsupported_dtype` / `too_many_missing_values` / `fit_exception` など具体的 skipped reason を返します。
6. **分析済み JSON 生成**  
   `scripts/today_advice_renderer.py` が `today_sleep_context`, `data_quality`, `exploratory_summary`, `recent_7d_summary`, `regression_summary`, `lightgbm_summary`, `matched_today_conditions` をまとめます。
   - 睡眠時間は `sleep_start/sleep_end` があれば差分（起床時刻 - 就寝時刻）を再計算した値を正とし、`sleep_duration_min` は fallback です。
   - タイムゾーンは Asia/Tokyo で扱い、`sleep_end <= sleep_start` は invalid です。
   - `sleep_valid_flag=false`, `sleep_invalid_reason=zero_duration_and_score_zero|duration_non_positive|end_before_or_equal_start|missing_duration|missing_all_sleep_fields` を付与し、sleep 系分析から除外します。
   - `zero_duration_and_score_zero` は「その候補単体が無効」の意味であり、別の有効候補を潰しません。
   - 当日 sleep 判定は `sleep_start/sleep_end` 由来の有効 duration、または `sleep_score > 0` があれば `today_sleep_context.sleep_available=true` です。
7. **分析済み JSON のみで本文生成**  
   GPT には生の30日 Notes を再投入せず、分析済み JSON のみを渡して Today advice 日本語本文を作ります。GPT 失敗時はルールベース文へフォールバックします。
   - `today_sleep_context.sleep_available=true` の日は、最終 Today advice 本文に睡眠示唆を最低1文含めるガードを入れています。

### 睡眠データの target_date 帰属ルール（共通）

- sleep phase / today_advice phase / 特徴量生成は同じ帰属関数を使います。
- ルールは `target_date = date((sleep_start または sleep_end in JST) - 5時間)` です（05:00 JST 境界）。
- 例: `2026-03-27 01:35-08:17 (+09:00)` は `2026-03-26` に帰属します。

### Today advice 分析監査ログ（analysis_audit）

Today advice の精度改善より先に、分析過程を追跡できるよう監査ログを追加しています。`scripts/mood_advice_generator.py` は Today advice 生成中に次を段階ログとして出力します。

- A. データ取得 (`[TodayAdvice][Fetch]`): 分析期間、取得件数、欠損件数。
- B. Notes ラベル監査 (`[TodayAdvice][Notes]`): `raw -> normalized -> dataframe` の sentiment/flag 件数を段階別に出力（Notes 本文全文は出力しない、代表キーワードのみ）。
- C. 特徴量作成 (`[TodayAdvice][Features]`): DataFrame 行列数、作成列、主要フラグ件数。
- D. exploratory 分析 (`[TodayAdvice][Exploratory]`): 単変量差分、保護/リスク特徴、条件組み合わせ（low/high mood 側）。
- E. 回帰分析 (`[TodayAdvice][Regression]`): 実行可否、サンプル数、上位特徴量、スキップ理由。
- F. LightGBM (`[TodayAdvice][LightGBM]`): 実行可否、サンプル数、feature importance、予測可否、スキップ理由。
- G. 今日一致判定 (`[TodayAdvice][TodayMatch]`): 当日 sleep の有効/欠損理由、一致パターン、risk/focus、根拠配列。
- G2. 睡眠解決詳細 (`[TodayAdvice][SleepResolve]` / `[TodayAdvice][SleepCandidates]` / `[TodayAdvice][SleepSelected]` / `[TodayAdvice][SleepAvailability]`):
  - 候補ごとの `candidate_date / sleep_start / sleep_end / raw_sleep_duration_min / resolved_sleep_duration_min / sleep_score / candidate_valid_flag / invalid_reason / selection_reason / duration_source / candidate_target_date`
  - 最終採用候補
  - `sleep_available` 最終判定理由
  - 保存済み sleep プロパティを使ったかどうか
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
- `exploratory_analysis`
- `regression`
- `lightgbm`
- `today_match`
- `analysis_json`
- `final_text`

確認ポイント:

- 「30日分が取れているか」→ `fetch.fetched_count` / `fetch.usable_rows_count`
- 「Notes が分析に入っているか」→ `notes_labeling.non_empty_count` / `notes_labeling.flag_counts`
- 「非 sleep の過去実績が analysis JSON に残るか」→ `analysis_json.exploratory_summary`, `analysis_json.recent_7d_summary`, `analysis_json.regression_summary`, `analysis_json.lightgbm_summary`
- 「どの条件の組み合わせが採用されたか」→ `analysis_json.matched_today_conditions`, `exploratory_analysis.top_combination_patterns_for_low_mood`
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
- 回帰分析は補助、LightGBM + exploratory_summary（単変量 + 組み合わせ探索）が主軸です。
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

## notify_diary 最新仕様メモ（2026-03 更新）

- Notes解析は sentiment 主体ではなく GPT 構造化抽出主体です（signals + derived flags）。
- Notes 抽出は unknown/low-confidence を保持し、unknown を neutral に丸めません。
- `sleep_duration_min <= 0`（または duration=0 かつ score=0）は睡眠欠損として扱い、睡眠分析文・見通し文は欠損用テンプレートを返します。
- ただし `sleep_start/sleep_end` がある場合は差分再計算結果を優先し、`sleep_duration_min` は fallback です。
- Today advice は固定ルールではなく 30〜60 日の探索型分析結果（exploratory/regression/LightGBM）を根拠に構成します。
- LightGBM を正式依存として導入しています（`requirements.txt` / CI install / verify 対応）。
- メール本文セクション順は `Today advice -> F Risk Alert(ある日だけ) -> Expense F Alert(ある日だけ) -> 司法試験 Study(値がある場合のみ) -> Diary -> Sleep & Condition -> Summary -> Expenses / Done / Drop / Meal / Weather` です。
- Drop 0 件時は `- None` / `- —` のダミー明細を表示しません（件数と本文を一致）。

## Weekly Report（新規）

既存の日次メール基盤を流用して、週次の長文 HTML メールを送信できます。目的は **振り返り + 改善提案 + 異常検知** の同時提供です。

- 対象期間: **前週月曜 05:00 JST 〜 当週日曜 04:59:59 JST**（表示は月曜〜土曜の週次レポート）
- 送信タイミング（自動）: 日曜夜（workflow は 21:00 JST 相当で起動、実送信可否は `WEEKLY_REPORT_SEND_HOUR_JST` で最終判定）
- 送信有効化: `WEEKLY_REPORT_ENABLED` が true 系のときのみ
- 手動実行: Actions → **Weekly Diary Report** → **Run workflow** で `workflow_dispatch` 実行すると、`--force` 付きで実行されるため日曜以外でも送信できます
- `--force` の挙動: `scripts/weekly_report.py` の曜日・時刻判定だけでなく `WEEKLY_REPORT_ENABLED` 判定もバイパスします（`--force` 指定時は `WEEKLY_REPORT_ENABLED=false` / 空でも送信処理に進みます）
- 送信先: 既存 `MAIL_FROM` / `MAIL_TO` を流用（現行実装は `WEEKLY_MAIL_TO` ではなく `MAIL_TO` を使用）
- `MAIL_CC` / `MAIL_BCC` は設定時のみ使用（未設定・空でも正常動作）
- Weight の source of truth: **Daily Log の `Weight` のみ**（Health DB 直接補完なし）
- 欠損時挙動:
  - 中核集計失敗は送信中止
  - 一部欠損は送信継続し本文に不足を明記
  - Weight 記録が 3 日未満なら体重は「記録不足」扱い

### 追加環境変数

- `WEEKLY_REPORT_ENABLED`: `true` / `1` / `yes` / `on` / `enabled` のいずれかで有効化
- `WEEKLY_REPORT_SEND_HOUR_JST`: 0-23。未設定または空文字のときは 21（JST）

- `VOICE_DIARY_NOTES_DB_ID`: Voice Diary Notes連携を有効化するNotion DB ID（未設定時は連携スキップ）
- `VOICE_DIARY_NOTES_MAX_COUNT`: 1日あたり取得するVoice Notes件数上限（既定: 50）
- `VOICE_DIARY_NOTES_MAX_CHARS`: Diary入力に含めるVoice Notes文字数上限（既定: 6000）

### GitHub Secrets（Weekly Report 実行時に必要）

- `MAIL_FROM`
- `MAIL_TO`
- `MAIL_CC`（任意）
- `MAIL_BCC`（任意）
- `GMAIL_APP_PASSWORD`
- `DAILY_LOG_UPSERT_URL`
- `WORKERS_BEARER_TOKEN`
- `OPENAI_API_KEY`
- `WEEKLY_REPORT_ENABLED`
- `WEEKLY_REPORT_SEND_HOUR_JST`（任意。未設定時は 21）

### 週次メール本文の構成（固定順）

1. 週の総括
2. 主要指標サマリー
3. グラフ5本（睡眠 / mood / 支出 / Done&Drop / 体重）
4. 良かった点
5. 注意点・異常検知
6. パターン分析
7. 来週の具体アクション
8. 日別ログ要約

体重グラフは Daily Log `Weight` のみを描画し、欠損補完しません。

## Daily Log canonical resolution (duplicate-safe)

- `Date` is treated as the canonical key for Daily Log resolution.
- `Target Date` is retained for backward compatibility and should be aligned with `Date`.
- `title` is display-only and is used only as fallback matching (`Daily Log｜YYYY-MM-DD` variants).
- Duplicate Daily Log pages are **not auto-deleted**.
- When duplicates are detected, the worker fills only **empty canonical fields** from duplicates.
- The `2026-05-07` duplicate case is covered by automatic canonical merge logic.
- Manual recovery should be used only as a last resort.


## Daily_Log 推奨プロパティ監査
- `scripts/audit_notion_schema.py` で Daily_Log DB の推奨プロパティ/型を監査できます。
- Mail Input / Study / Weather など現DBに存在する主要項目は通常監査対象です。
- Expense F / F Risk / Notes Label の保存用プロパティは、対応する `SAVE_*_TO_DAILY_LOG=true` の場合のみ監査対象です。
- `STRICT_NOTION_SCHEMA_AUDIT=false` (デフォルト): 監査対象の不足/型不一致は WARNING。
- `STRICT_NOTION_SCHEMA_AUDIT=true`: 監査対象になっている項目だけを失敗対象にして exit 1。
- Meal summary は未記録日があり得るため、空でも即エラーではありません。

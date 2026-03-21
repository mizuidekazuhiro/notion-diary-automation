# notion-diary-automation

既存の Notion 連携挙動を維持したまま、Health DB の睡眠 / Readiness データを Daily Log DB に取り込み、GPT による日本語の睡眠分析と今日の体調予測、そして毎朝メールの `Sleep & Condition` セクションを追加できるようにしたリポジトリです。

## 1. この変更で何ができるようになるか

この変更で、以下ができるようになります。

- Health DB に保存された睡眠・Readiness 関連 13 項目を Daily Log DB に ingest できます。
- Daily Log DB から睡眠関連項目を読み出し、当日値 + 直近7日平均 + 差分を使って GPT が日本語の分析文を生成できます。
- GPT 出力として `Sleep Analysis JP` と `Today Condition Forecast JP` を Daily Log DB に保存できます。
- 毎朝メールに `Sleep & Condition` セクションが追加され、睡眠分析・今日の体調予測・就寝時間・起床時間・睡眠時間を表示できます。
- 追加項目が一部しかなくても処理全体は継続し、未入力・空文字・parse不能値はその項目だけスキップされます。

## 2. 概要

データの流れは次の通りです。

1. iPhone ショートカットや Health 系入力が Health DB に保存されます。
2. ingest が Health DB を読み、Daily Log DB に睡眠13項目を反映します。
3. プログラムが Daily Log から睡眠データを読み出します。
4. GPT が `Sleep Analysis JP` / `Today Condition Forecast JP` を生成します。
5. 毎朝メールに `Sleep & Condition` セクションが表示されます。

## 3. 追加する Daily Log DB の列一覧

### 3-1. 既存列

このリポジトリは既存列も引き続き利用します。例: `Date`, `Target Date`, `Activity Summary`, `Diary`, `Mail ID`, `Source`, `Meal summary`, `Weight`。

### 3-2. 今回追加する Daily Log DB 列

列名は **完全一致** で作成してください。

| 列名 | 型 | 用途 |
| --- | --- | --- |
| Sleep Start | Date | 就寝開始日時 |
| Sleep End | Date | 起床日時 |
| Sleep Duration Min | Number | 睡眠時間（分） |
| Sleep Score | Number | 睡眠スコア |
| Sleep Source | Rich text または Select | 睡眠データのソース |
| Readiness Stars | Number | Readiness の星評価 |
| Readiness HRV | Number | Readiness HRV |
| Readiness BPM | Number | Readiness BPM |
| Baseline HRV | Number | ベースラインHRV |
| Baseline Waking BPM | Number | ベースライン起床時BPM |
| Sleep Heart Rate | Number | 睡眠時心拍 |
| Deep Duration Min | Number | 深睡眠時間（分） |
| REM Duration Min | Number | REM時間（分） |
| Sleep Analysis JP | Rich text | GPT が生成する昨夜の睡眠分析 |
| Today Condition Forecast JP | Rich text | GPT が生成する今日の体調予測 |

## 4. Health DB 側で必要な列一覧

Health DB 側も列名は **完全一致** で作成してください。

| Health DB 列名 | 型 | Daily Log DB 列 |
| --- | --- | --- |
| sleep_start | Date | Sleep Start |
| sleep_end | Date | Sleep End |
| sleep_duration_min | Number | Sleep Duration Min |
| sleep_score | Number | Sleep Score |
| sleep_source | Rich text または Select | Sleep Source |
| readiness_stars | Number | Readiness Stars |
| readiness_hrv | Number | Readiness HRV |
| readiness_bpm | Number | Readiness BPM |
| baseline_hrv | Number | Baseline HRV |
| baseline_waking_bpm | Number | Baseline Waking BPM |
| sleep_heart_rate | Number | Sleep Heart Rate |
| deep_duration_min | Number | Deep Duration Min |
| rem_duration_min | Number | REM Duration Min |

## 5. GPT 出力列の説明

### Sleep Analysis JP

- 型: Rich text
- 内容: 昨夜の睡眠の要約
- 文章数: 2〜4文
- 参照候補: 睡眠時間、睡眠スコア、深睡眠、REM、睡眠時心拍 など
- 注意: 過剰な断定を避けます

### Today Condition Forecast JP

- 型: Rich text
- 内容: 今日の体調・集中力・疲労感・判断力の見通し
- 文章数: 2〜4文
- 参照候補: 当日値に加えて直近7日平均との差分
- 注意: 医療断定を避け、軽い行動提案を含むことがあります

## 6. 毎朝メールに何が追加されるか

毎朝メール本文に `Sleep & Condition` セクションが追加されます。

表示順は以下です。

1. Sleep Analysis JP
2. Today Condition Forecast JP
3. 就寝時間
4. 起床時間
5. 睡眠時間

表示ルール:

- `Sleep Start` / `Sleep End` は `22:53` / `08:33` のように時刻だけを見やすく表示します。
- `Sleep Duration Min` は `9時間45分` のように整形して表示します。
- 項目が空なら、その項目だけ非表示です。
- セクション内に表示できる項目が1つもない場合は、セクションごと非表示です。

## 7. データの流れ

```text
Health DB
  ↓
ingest_health
  ↓
Daily Log DB に睡眠13項目を保存
  ↓
Daily Log read API で読み出し
  ↓
Python で当日値 + 直近7日平均 + 差分を計算
  ↓
GPT が Sleep Analysis JP / Today Condition Forecast JP を生成
  ↓
Daily Log DB に保存
  ↓
毎朝メールの Sleep & Condition セクションへ表示
```

## 8. 設定方法

非エンジニアの方でも追いやすいよう、順番に設定してください。

### Step 1: Health DB に列を追加

Health DB に、前述の13列を追加します。

ポイント:

- 列名は README の表と **完全一致** にしてください。
- `sleep_start` / `sleep_end` は `Date` 型です。
- `sleep_source` は `Rich text` でも `Select` でも動作します。
- それ以外は `Number` 型です。

### Step 2: Daily Log DB に列を追加

Daily Log DB に、前述の15列を追加します。

ポイント:

- `Sleep Source` は `Rich text` または `Select` のどちらでも動作します。
- `Sleep Analysis JP` と `Today Condition Forecast JP` は `Rich text` 型にしてください。
- 既存列は削除・改名しないでください。

### Step 3: 環境変数を確認

今回の変更では、**既存の DB ID / 環境変数名は変更していません**。

最低限、従来どおり以下を確認してください。

- `NOTION_TOKEN`
- `DAILY_LOG_DB_ID`
- `HEALTH_DB_ID`
- `DAILY_LOG_UPSERT_URL`
- `WORKERS_BEARER_TOKEN`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`（未指定時は既定値）
- メール送信を使う場合: `MAIL_FROM`, `MAIL_TO`, `GMAIL_APP_PASSWORD`, `PUBLIC_BASE_URL`, `MAIL_LINK_SECRET`

### Step 4: ingest を実行

通常どおり ingest を実行します。Health DB に対象日のレコードがあれば、Daily Log DB に睡眠13項目が反映されます。

### Step 5: Daily Log に睡眠項目が入ることを確認

Daily Log の対象日ページを開き、以下のように値が入っていることを確認します。

- Sleep Start
- Sleep End
- Sleep Duration Min
- Sleep Score
- Readiness HRV
- Deep Duration Min
- REM Duration Min

### Step 6: GPT 出力 2 項目が入ることを確認

`notify_diary` 相当の処理が走ると、以下が保存されます。

- Sleep Analysis JP
- Today Condition Forecast JP

### Step 7: 毎朝メールに表示されることを確認

対象日のメールで、`Sleep & Condition` セクションが表示されていることを確認してください。

## 9. サンプル

### 9-1. Daily Log のサンプル値

- Sleep Start: `2026-03-20T22:53:00+09:00`
- Sleep End: `2026-03-21T08:33:00+09:00`
- Sleep Duration Min: `585`
- Sleep Score: `82`
- Readiness HRV: `58`
- Sleep Analysis JP: `昨夜は睡眠時間をしっかり確保でき、全体として落ち着いた睡眠だった可能性があります。睡眠スコアと深睡眠の値を見る限り、回復感は比較的安定していそうです。`
- Today Condition Forecast JP: `直近7日平均と比べて睡眠時間が長めなら、午前中は集中しやすい可能性があります。一方で心拍やReadinessが平常並みでない場合は、序盤はペースを上げすぎず進めるのが無難です。`

### 9-2. 毎朝メールの表示例

```text
Sleep & Condition
- Sleep Analysis JP: 昨夜は睡眠時間をしっかり確保でき、全体として落ち着いた睡眠だった可能性があります。
- Today Condition Forecast JP: 午前は比較的安定して動けそうですが、午後はこまめに休憩を入れると良さそうです。
- 就寝時間: 22:53
- 起床時間: 08:33
- 睡眠時間: 9時間45分
```

## 10. トラブルシューティング

### Notion の列名が一致していない

列名が1文字でも違うと値が入らないことがあります。README の表と完全一致しているか確認してください。

### 型が違う

- `Sleep Start` / `Sleep End` は `Date`
- 数値系は `Number`
- `Sleep Analysis JP` / `Today Condition Forecast JP` は `Rich text`
- `Sleep Source` / `sleep_source` は `Rich text` または `Select`

### 睡眠項目が一部空でも正常です

今回の13項目はすべて任意項目です。一部しかなくても処理全体は成功します。

### 7日平均が出ない

過去7日分の Daily Log に十分なデータがない可能性があります。取得できた範囲で平均を使い、データがなければその派生値は未設定のままです。

### メールに出ない

まず Daily Log に以下のいずれかが入っているか確認してください。

- Sleep Analysis JP
- Today Condition Forecast JP
- Sleep Start
- Sleep End
- Sleep Duration Min

すべて空の場合、`Sleep & Condition` セクションは表示されません。

## 11. Migration note

### 追加した列一覧

Daily Log DB:

- Sleep Start
- Sleep End
- Sleep Duration Min
- Sleep Score
- Sleep Source
- Readiness Stars
- Readiness HRV
- Readiness BPM
- Baseline HRV
- Baseline Waking BPM
- Sleep Heart Rate
- Deep Duration Min
- REM Duration Min
- Sleep Analysis JP
- Today Condition Forecast JP

Health DB:

- sleep_start
- sleep_end
- sleep_duration_min
- sleep_score
- sleep_source
- readiness_stars
- readiness_hrv
- readiness_bpm
- baseline_hrv
- baseline_waking_bpm
- sleep_heart_rate
- deep_duration_min
- rem_duration_min

### 既存環境への影響

- 既存の DB ID / 環境変数名 / API リクエスト / レスポンス構造 / 日付計算 / 既存機能は維持しています。
- 新しい列が未作成でも、既存処理が壊れないように追加項目は任意として扱います。
- 新機能をフルに使うには、Health DB と Daily Log DB の列追加が必要です。

## 12. テスト

```bash
cd workers && npm test
python -m pytest tests/test_sleep_condition.py
```

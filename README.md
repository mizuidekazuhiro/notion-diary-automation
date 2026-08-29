# notion-diary-automation

Notion `Daily Log` を中心に、日次データの ingest、分析、日記生成、朝メール配信までを GitHub Actions で自動化するリポジトリです。

## 現在のワークフロー

| Order | Workflow | Trigger | 主責務 |
| --- | --- | --- | --- |
| 00 | `CI - Test & Requirements Gate` | push / pull_request | pytest + requirements/workflow contract checks |
| 01 | `Daily Diary 01 - Ingest Daily Log` | schedule / manual | Daily Log ensure + ingest |
| 02 | `Daily Diary 02 - Generate Location Summary` | workflow_run from 01 / manual | Location summary 更新 |
| 03 | `Daily Diary 03 - Generate Diary & Sleep Insights` | workflow_run from 02 / manual | Weather / Expense F / Sleep / F Risk / Today advice / Diary |
| 04 | `Daily Diary 04 - Publish Daily Mail` | workflow_run from 03 / manual | 朝メール配信 |

`Daily Diary 01` の schedule は `03:00 UTC = 12:00 JST` です。通常は 01 → 02 → 03 → 04 の順で連鎖します。

Cloudflare Workers の deploy は別 workflow `Deploy Cloudflare Workers` で管理し、`main` の CI 成功後に実行します。

## Daily Diary の処理方針

### Phase A: ingest

- Daily Log ページを ensure
- Tasks / Health / Expenses 等を ingest

### Phase B: location summary

- `apps/location_summary_writer` が `Location summary (GPT)` を更新

### Phase C: generate/update

`scripts/daily_job.py --phase notify_diary` が Weather、Expense F、Sleep、F Risk、Today advice、Diary を生成・更新します。

Expense F と F Risk は Daily Log に永続化する値を source of truth とせず、`Expenses DB` の read と実行時状態を利用します。F Risk の継続状態は `automation-state` ブランチの state file を使用します。

### Phase D: publish

`scripts/daily_job.py --phase publish` が Daily Log を読み、メールを生成・送信します。

メール送信系 workflow は次の secrets を使用します。

- `MAIL_FROM`
- `MAIL_TO`
- `GMAIL_APP_PASSWORD`
- `DAILY_LOG_UPSERT_URL`
- `WORKERS_BEARER_TOKEN`
- `NOTION_TOKEN`
- `EXPENSES_DB_ID`
- `OPENAI_API_KEY`

Secrets はコード・README・ログに書かないでください。

---

# Expense F

## Source of truth

Expense F 集計は `scripts/expense_f_aggregator.py` が `NOTION_TOKEN` と `EXPENSES_DB_ID` を使って `Expenses DB` を直接 read します。

必須プロパティ:

- `F`
- `Merchant`
- `Amount`

任意プロパティ:

- `Currency`
- `Category`
- `Date`
- `Received At`
- `FamilyCard`

プロパティ名は env で明示するか schema alias から解決します。

## 日付の解決順

Expense F の日次帰属は、利用できる schema に応じて次の順で決定します。

1. `Date` が date property なら `Date`
2. それ以外で `Received At` が date property なら `Received At` を JST に変換
3. どちらも使えない場合は Notion page `created_time`

debug には `filter_strategy` と `created_time_source` を残します。

## 多通貨対応 — 重要

`Expenses DB` の `Amount` は**元通貨額**です。JPY と TWD など、異なる通貨をそのまま足してはいけません。

2026-08-29 以降の Expense F 集計は `Currency` を認識します。

例:

```text
JPY 5,000
TWD   300
```

この2件を `5,300円` とは扱いません。

### 集計ルール

- `Currency` は select / text から取得し、大文字の通貨コードへ正規化
- `Currency` property が schema に無い、または値が空の legacy row は JPY として扱う
- 通貨別金額は `ExpenseFAggregate.currency_totals` と debug `currency_totals` に保持
- `mixed_currency=true` で複数通貨日を識別
- FX 換算は行わない

### 後方互換の `total`

既存コードには `Expense F Total` / `aggregate.total` を円として扱う箇所があります。そのため、**`aggregate.total` は JPY 分だけ**を返します。

例:

```text
JPY 5,000 + TWD 300
=> total = 5000
=> currency_totals = {"JPY": 5000, "TWD": 300}
```

TWD だけの日:

```text
TWD 300
=> total = 0
=> currency_totals = {"TWD": 300}
```

これにより、TWD 300 を誤って `300円` と表示・学習する事故を防ぎます。

### Fイベント判定

Fイベント自体は **`Expense F Count > 0`** で判定するため、TWD / KRW / USD 等の外貨支出も Fイベントとして認識されます。

金額を異通貨間で比較する必要がある将来機能では、為替レート、換算日時、換算後通貨を明示した別レイヤを追加してください。現在の実装では暗黙の円換算をしません。

### debug fields

Expense F 集計は主に以下を出します。

- `resolved_props`
- `created_time_source`
- `date_window_start`
- `date_window_end`
- `filter_strategy`
- `matched_count`
- `total_amount` — JPY only
- `currency_totals`
- `currency_prop_present`
- `mixed_currency`
- `query_exception_class`
- `query_exception_message`

`data_status`:

- `ok`
- `no_results`
- `query_failed`
- `schema_unresolved`

---

# F Risk

F Risk は Expense F の履歴を `Expenses DB` から再構築します。

- event label: `Expense F Count > 0`
- prediction 時に当日 `expense_f_count` / `expense_f_total` をリークさせない
- recurrence features は過去 F event のみから生成
- LightGBM / fallback rule / case similarity を組み合わせる
- insufficient data は skip reason を明示

外貨行も F event count に含まれます。`expense_f_total` は JPY-only のため、異通貨の金額を同じスケールで誤比較しません。

---

# Today advice

Today advice は **today sleep only / non-sleep historical only** を原則にします。

- 当日参照してよい主データは sleep 系
- Notes / spending / tasks / meal / location は historical pattern として利用
- Diary 本文は Today advice の入力に使用しない
- 30〜60日の feature analysis、exploratory analysis、regression、LightGBM を使う
- LLM は分析済み JSON を日本語へ整形する役割を中心とする

詳細は既存 docs を参照してください。

---

# Daily Log reliability / repair

通常処理と過去日修復を分離しています。

- 通常: 01 → 02 → 03 → 04
- repair: `Daily Log Repair`
- repair は Phase A → B → C を必要な日だけ再実行
- historical repair では現在地点の Weather を過去日に誤適用しない
- Phase D は manual opt-in を除き repair では送信しない

関連ドキュメント:

- `docs/daily-automation-reliability.md`
- `docs/notion-dataflow.md`
- `docs/health-recovery.md`
- `docs/daily_mail_quality_review.md`
- `docs/historical_mail_backfill.md`

---

# Anki PC automatic study tracking

Windows Anki の `revlog.time` を集計し、Daily Log の以下へ反映します。

- `Study Minutes`
- `Study Sessions`
- `Study Last Used At`

詳細:

- `docs/anki_pc_automatic_study_tracking.md`

---

# Weekly Report

日次基盤を利用して weekly report を送信できます。

- 主要指標 summary
- sleep / mood / spending / tasks / weight
- 良かった点・注意点
- pattern analysis
- 来週の具体 action

Weight の source of truth は Daily Log `Weight` です。

---

# Cloudflare Workers deployment

この repo 自身の Workers は `.github/workflows/deploy_workers.yml` から deploy します。

使用する Repository Secrets:

- `CF_API_TOKEN`
- `CF_ACCOUNT_ID`

これは `Daily_Log_Expenses` repo の deploy secrets (`CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID`) とは**別repo・別secret名**です。

今回の `Daily_Log_Expenses` の多通貨対応や Cloudflare deploy secret 更新によって、この repo の `CF_API_TOKEN` / `CF_ACCOUNT_ID` が変更されることはありません。

---

# Test

```bash
python -m pip install -r requirements.txt
pytest
python scripts/verify_requirements.py
```

Expense F の currency regression tests は `tests/test_expense_f_filter_strategy.py` にあります。

最低限確認するケース:

- legacy row with no Currency -> JPY
- JPY only
- TWD only
- JPY + TWD mixed day
- Date / Received At / created_time fallback
- FamilyCard filter
- Notion query error sanitation

---

# Operational safety rules

1. 異なる通貨の `Amount` を単純合算しない。
2. FX換算する場合は rate / timestamp / target currency を明示する。
3. `Expense F Count` と `Expense F Total` の意味を混同しない。
4. Secrets を repo / log / artifact に出さない。
5. CI が失敗した変更を main に入れない。
6. repair と通常メール配信を混同しない。
7. Cloudflare account / token は repo ごとに独立して扱う。

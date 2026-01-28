# notion-diary-automation

Notion中心の「日記自動化MVP」を Cloudflare Workers + Python + GitHub Actions で構築するためのリポジトリです。

## 構成

- **Cloudflare Workers (TypeScript)**: Notion APIの問い合わせ、DBスキーマ検証、Daily_Logのensure/upsert/read。
- **Python**: Phase A/B を分離して実行（Ingest → Publish）。
- **GitHub Actions**: Phase A/B を別ワークフローで実行。

## システム全体像（Phase A/B）

- **Phase A (Ingest)**: `target_date = JSTの昨日`
  - **A-0** `ensure_daily_log_page(target_date)`  
    Daily_Logのページを「存在保証」するだけ。Tasks取得などは一切しない。
  - **A-1** `ingest_sources(target_date, daily_log_page_id)`  
    コネクタ群（現時点はTasksのみ）を順に実行し、Daily_Logへ追記/更新する。
- **Phase B (Publish)**: `target_date = JSTの昨日`
  - Daily_Logを読み取り、メールを生成・送信する。
  - Tasks/Inbox等は再取得しない（Daily_Logのみが情報源）。

## スケジュール（JST/UTC）

- **Phase A (Ingest)**: 01:00 JST = 16:00 UTC（前日）
- **Phase B (Publish)**: 07:00 JST = 22:00 UTC（前日）

## メール仕様（昨日の成果に集中）

- メールには **昨日 Done / 昨日 Drop** のみを含めます。
- 表示は「タスク名 + Priority」のみ（日時など詳細は表示しない）。
- Tasks DB の `Done date` / `Drop date` を使って集計します（プロパティ名は環境変数で変更可）。
- JST基準で「昨日の0:00〜翌日0:00」の範囲で集計します（`start <= date < end`）。
- `Done date` / `Drop date` は**時刻つき**で保存されるため、`target_date` のJSTレンジで判定します。
  - `start = ${target_date}T00:00:00+09:00`
  - `end = ${next_date}T00:00:00+09:00`（`end`は排他）
- `before end` を使う理由は、`24:00` 表記を作らずに「翌日0:00」を排他境界として扱うためです。
- `toISOString()` / `new Date("YYYY-MM-DD")` のUTC変換は**1日ズレる**ので使いません。
- `Done date` / `Drop date` が空のタスクは除外されます（Tasks DBの当該Dateのみが根拠）。
- `TASK_STATUS_DONE` / `TASK_STATUS_DROP_VALUE` で Done/Drop の判定値を調整できます。
- `Notes` は仕様として一切書き込みません。

## Notion DBの必須プロパティ

> **各DBのTitleプロパティ名が異なる**ため、プロパティ名は完全一致で管理します。

### Tasks DB (`TASK_DB_ID`)

- `Status` (select) : 値 `Do` / `Done` / `Drop` が存在すること（名称は環境変数で変更可）
- `Since Do` (date)
- `Priority` (select)
- `名前` (title)
- `Done date` (date)
- `Drop date` (date)

> **Status / Done date / Drop date のプロパティ名はNotion側と完全一致である必要があります。**  
> 変更する場合は `TASK_STATUS_PROPERTY_NAME` / `TASK_DONE_DATE_PROPERTY_NAME` / `TASK_DROP_DATE_PROPERTY_NAME` を設定してください。  
> `Someday` (checkbox) と `DoneAt` は現在のDBに無いため、コード側でも参照/更新しません（Someday状態は `Status` の値で表現します）。

### Inbox DB (`INBOX_DB_ID`)

- `Name` (title)

> 現在のPhase A/BではInbox DBは未使用（将来拡張用のため保持）。

### Daily_Log DB (`DAILY_LOG_DB_ID`)

- `名前` (title)
- `Date` (date) : その日のページの日付
- `Target Date` (date) ← **Upsert判定キー**
- `Activity Summary` (rich_text)
- `Diary` (rich_text)
- `Expenses total` (number)
- `Location summary` (rich_text)
- `Meal summary` (rich_text)
- `Mail ID` (rich_text)
- `Mood` (select)
- `Source` (select)
- `Weight` (number)
- `Done Tasks` (relation -> Tasks DB)
- `Drop Tasks` (relation -> Tasks DB)
- `Done Count` (rollup: Done Tasks の `名前` を Count all)
- `Drop Count` (rollup: Drop Tasks の `名前` を Count all)

MVPでは最低限 `Target Date` / `Activity Summary` / `Mail ID` / `Source` を埋めればOKです。
`Notes` は仕様として **一切書き込まない** 方針です（DBに存在していても更新対象にしません）。

### Daily_Logの保存内容（推奨）

- **SummaryText**: `Activity Summary` に保存（メール本文テキスト）
- **SummaryHtml**: `Diary` に保存（メールHTML本文）
- **Sources**: `Source` に保存（現時点は `automation`）
- **Raw**: `data_json` をDaily_Logページの子ブロックに分割保存

> Notionの `rich_text` は1ブロック2000文字制限があるため、コード側で自動分割します。  
> `Notes` プロパティに巨大テキストやJSONを入れるのは禁止です。

## セキュリティ設計（2段階更新）

- **GET `/confirm/...`**: 確認のみ（更新禁止）
- **POST `/execute/...`**: 更新実行のみ

メールのリンクが自動踏みされる可能性があるため、更新は必ずPOSTでのみ実行します。

## Cloudflare Workers

### デプロイ（GitHub Actions・ブラウザのみ）

このリポジトリは `workers/` にCloudflare Workersプロジェクトを配置しています。ローカルCLIは不要で、GitHub Actionsから `wrangler` を実行して自動デプロイします。

1. **Cloudflare API Token を作成**
   - Cloudflare Dashboard → **My Profile** → **API Tokens** → **Create Token**。
   - テンプレートは **Custom Token** を選択し、以下を許可します。
     - Account: `Account Settings` = Read
     - Workers: `Workers Scripts` = Edit
   - 作成したトークンを控える（後で `CF_API_TOKEN` に使います）。
2. **GitHub Secrets に追加**
   - GitHub → Settings → Secrets and variables → Actions → New repository secret。
   - `CF_API_TOKEN` と `CF_ACCOUNT_ID` を追加（Account IDはCloudflare Dashboardの右側に表示）。
3. **GitHub Actionsでデプロイ**
   - GitHub → Actions → **Deploy Cloudflare Workers** → Run workflow。
   - `workers/wrangler.toml` を参照して、`workers/src/index.ts` をビルド＆デプロイします。
4. **WorkersのSecrets/Variablesを設定**
   - Cloudflare Dashboard → Workers & Pages → 対象Workers → **Settings** → **Variables and Secrets**。
   - `NOTION_TOKEN` / `INBOX_DB_ID` / `TASK_DB_ID` / `DAILY_LOG_DB_ID` などを**ここにのみ**設定します。

### Secrets

Workers環境変数（Secrets）に以下を設定します。

- `NOTION_TOKEN`
- `INBOX_DB_ID`
- `TASK_DB_ID`
- `DAILY_LOG_DB_ID`
- `WORKERS_BEARER_TOKEN` (任意: Bearer認証用)
- `TASK_STATUS_DO` (任意: `Do` がデフォルト)
- `TASK_STATUS_DONE` (任意: `Done` がデフォルト)
- `TASK_STATUS_DROPPED` (任意: `Drop` がデフォルト)
- `TASK_STATUS_DROP_VALUE` (任意: `Drop` がデフォルト、`TASK_STATUS_DROPPED` の代替)
- `TASK_STATUS_SOMEDAY` (任意: Someday判定に使うStatus値がある場合に設定)
- `REQUIRE_STATUS_EXTRA_OPTIONS` (任意: `true` の場合は Status の `Drop` / `Someday` も必須オプションとして検証)
- `TASK_STATUS_PROPERTY_NAME` (任意: `Status` がデフォルト)
- `TASK_DONE_DATE_PROPERTY_NAME` (任意: `Done date` がデフォルト)
- `TASK_DROP_DATE_PROPERTY_NAME` (任意: `Drop date` がデフォルト)

> **NotionトークンとDB IDはWorkers側のSecretsのみ**に置き、GitHub Actionsには置きません。

### エンドポイント

| Method | Path | 説明 |
| --- | --- | --- |
| GET | `/health` | 簡易ヘルスチェック（JSONで `{ "status": "ok" }`） |
| GET | `/api/inbox` | Inbox DB の一覧取得 |
| GET | `/api/tasks` | Tasks DB の Status = "Do" と Status = "Someday" を取得 |
|  |  | ※Status = "Someday" のタスクは `confirm_promote_url` 付きで返却 |
| GET | `/api/tasks/closed?date=YYYY-MM-DD` | Tasks DB から「昨日Done/Drop」を取得（date未指定ならJSTの昨日） |
| GET | `/api/daily_log?date=YYYY-MM-DD` | Daily_Log のSummary取得（メール生成に利用） |
| GET | `/confirm/daily_log/upsert` | Daily_Log Upsert 確認ページ |
| POST | `/execute/api/daily_log/ensure` | Daily_Log ページ作成（存在保証） |
| POST | `/execute/api/daily_log/upsert` | Daily_Log Upsert 実行 |
| GET | `/confirm/tasks/promote?id=...` | Someday → Do 昇格の確認 |
| POST | `/execute/tasks/promote` | Someday → Do 昇格 実行 |

### GitHub Actions用URLの対応表

- `INBOX_JSON_URL`: `/api/inbox`
- `TASKS_JSON_URL`: `/api/tasks`
- `TASKS_CLOSED_URL`: `/api/tasks/closed`
- `DAILY_LOG_UPSERT_URL`: `/execute/api/daily_log/upsert`
  - `DAILY_LOG_UPSERT_URL` の同一ホストを使って以下も派生します:
    - `/execute/api/daily_log/ensure`
    - `/api/daily_log`

### ルーティング簡易チェック

`/health` または `/api/tasks` が返ることを確認すると、**NotFound回避の確認**ができます。

```bash
curl "https://<worker>.workers.dev/health"
```

```bash
curl -H "Authorization: Bearer $WORKERS_BEARER_TOKEN" \
  "https://<worker>.workers.dev/api/tasks"
```

> `WORKERS_BEARER_TOKEN` を設定していない場合は `Authorization` ヘッダ無しでも動作します。

> `WORKERS_BEARER_TOKEN` を有効化する場合は、**GitHub ActionsのSecretsとWorkersの環境変数に同じ値**を設定してください。

### 404/401/500/Notionエラー の切り分け（よくある原因）

| ステータス | 主な原因 | 確認ポイント |
| --- | --- | --- |
| 404 Not Found | ルートのパス誤り / デプロイ先のURL誤り | `/health` のURLが正しいか |
| 401 Unauthorized | `WORKERS_BEARER_TOKEN` が設定済みなのにヘッダ未指定 | `Authorization: Bearer ...` を付ける |
| 500 Internal Server Error | 予期せぬ内部エラー | Workersログのスタックトレース |
| 4xx/5xx (Notion) | Notion APIエラー | 返却されたJSONの `status` / `code` / `message` / `request_id` を確認 |

### 401/405/4xx/5xx が出たときの診断手順（会社PCで手動POSTできない前提）

1. **GitHub Actionsログを確認**し、HTTPステータスとレスポンスJSONを把握する。
2. **Cloudflare Workersログ（Tail）を確認**し、`Notion API error` や `WORKERS_BEARER_TOKEN is not set; auth is disabled` などの警告を確認する。
3. ステータス別の切り分け:
   - `401`: `WORKERS_BEARER_TOKEN` の値が Actions と Workers で一致しているか。
   - `405`: URLが `/execute/api/daily_log/upsert` になっているか、POSTで送信しているか。
   - `4xx/5xx`: Notion APIエラー。レスポンスJSONの `status` / `code` / `message` / `request_id` を確認する。

#### Bearer認証の例

```bash
curl -H "Authorization: Bearer $WORKERS_BEARER_TOKEN" \
  "https://xxxx.workers.dev/api/tasks/closed?date=2024-01-01"
```

> `TASK_DB_ID` を再利用するため、Secrets追加は不要です。

### Daily_Log Upsert

- **検索条件**: `Target Date` が `YYYY-MM-DD` で一致するページを検索
- 存在すれば更新 / 無ければ作成
- `Date` も `Target Date` と同じ日付で更新されます
- `Notes` は更新しません（DBに存在していても無視）
- 先に `POST /execute/api/daily_log/ensure` でページだけ作成する運用を推奨

Workersへのリクエスト例（Pythonから送信）:

```json
{
  "target_date": "YYYY-MM-DD",
  "title": "Daily Log｜YYYY-MM-DD",
  "summary_text": "string",
  "summary_html": "string(任意)",
  "mail_id": "string",
  "source": "automation",
  "data_json": "string(任意)"
}
```

#### curlでの再現例

```bash
curl -X POST "https://<worker>.workers.dev/execute/api/daily_log/upsert" \
  -H "Authorization: Bearer $WORKERS_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "target_date": "2024-01-01",
    "title": "Daily Log｜2024-01-01",
    "summary_text": "summary text",
    "summary_html": "<p>summary html</p>",
    "mail_id": "mail-id-123",
    "source": "automation",
    "data_json": "{\"example\":true}",
    "update_task_relations": true
  }'
```

> `WORKERS_BEARER_TOKEN` を設定していない場合は `Authorization` ヘッダ無しでも動作します。

## Python

- Phase A (Ingest):
  - `/execute/api/daily_log/ensure` で Daily_Log ページを先に用意（Tasks取得とは完全分離）
  - `/api/tasks/closed` で昨日のDone/Dropを取得し、SummaryText/Htmlを生成
  - `/execute/api/daily_log/upsert` にPOSTしてDaily_Logへ保存
- Phase B (Publish):
  - `/api/daily_log` でDaily_LogのSummaryを読み取り、メール送信
  - Tasks/Inboxなどは再取得しない（Daily_Logのみが情報源）
- HTMLメール（multipart/alternative）で text/plain と text/html を送信
- `MAIL_TO` はカンマ区切りで複数対応
- SMTP送信に失敗しても処理は継続（ログにエラーを出力）
- HTML本文はインラインCSS中心でレンダリング（Gmail/iPhoneの崩れ対策）
- Notionに `Diary` / `Expenses total` / `Location summary` / `Mood` / `Weight` を追加すると、
  Daily Logの値がメールのSummaryセクションに自動反映されます（未入力は “—” 表示）

## GitHub Actions

- 実行タイミング:
  - Phase A: JST 01:00（UTC 16:00）に `ingest_daily_log.yml` を直接スケジュール実行
  - Phase B: JST 07:00（UTC 22:00）に `publish_daily_mail.yml` を直接スケジュール実行
  - 手動実行: Phase A / Phase B それぞれのワークフローを `workflow_dispatch` で実行
- ワークフロー:
  - `Daily Notion Diary - Phase A (Ingest)` → `.github/workflows/ingest_daily_log.yml`
  - `Daily Notion Diary - Phase B (Publish)` → `.github/workflows/publish_daily_mail.yml`
  - `Deploy Cloudflare Workers` → `.github/workflows/deploy_workers.yml`
- Schedulerを廃止して **run増殖が発生しない** 構成にしています（毎日2回の定期実行のみ）。
- Secrets:
  - `MAIL_FROM`
  - `MAIL_TO`
  - `GMAIL_APP_PASSWORD`
  - `INBOX_JSON_URL`
  - `TASKS_JSON_URL`
  - `TASKS_CLOSED_URL`
  - `DAILY_LOG_UPSERT_URL`
  - `WORKERS_BEARER_TOKEN` (任意)
  - `CF_API_TOKEN` (Workersデプロイ用)
  - `CF_ACCOUNT_ID` (Workersデプロイ用)

`INBOX_JSON_URL`/`TASKS_JSON_URL`/`TASKS_CLOSED_URL`/`DAILY_LOG_UPSERT_URL` はWorkersのURLをセットしてください。
Notionトークン/DB IDは**GitHub Secretsに入れず**、Cloudflare側のSecretsのみを使用します。

### GitHub Actionsで使うWorkers URLの例

- `INBOX_JSON_URL = https://<worker>.workers.dev/api/inbox`
- `TASKS_JSON_URL = https://<worker>.workers.dev/api/tasks`
- `TASKS_CLOSED_URL = https://<worker>.workers.dev/api/tasks/closed`
- `DAILY_LOG_UPSERT_URL = https://<worker>.workers.dev/execute/api/daily_log/upsert`
- `DAILY_LOG_UPSERT_URL` と同じホストで以下を使用します:
  - `/execute/api/daily_log/ensure`
  - `/api/daily_log`
- `WORKERS_BEARER_TOKEN` は任意ですが、有効化する場合は **Cloudflare側のVariables/Secretsにも同じ値** を入れてください。

## 手動実行

### GitHub Actions から実行する場合

1. GitHub → Actions を開く
2. `Daily Notion Diary - Phase A (Ingest)` または `Daily Notion Diary - Phase B (Publish)` を選択
3. **Run workflow** から `workflow_dispatch` を実行

### ローカルで実行する場合

```bash
python scripts/daily_job.py --phase ingest
python scripts/daily_job.py --phase publish
python scripts/daily_job.py --phase all
```

## ファイル間の連関（どのファイルが何を呼ぶか）

- `.github/workflows/ingest_daily_log.yml` → `scripts/daily_job.py --phase ingest`
- `scripts/daily_job.py` → `ingest/ensure_daily_log_page.py`
- `scripts/daily_job.py` → `ingest/ingest_sources.py` → `connectors/tasks.py`
- `.github/workflows/publish_daily_mail.yml` → `scripts/daily_job.py --phase publish`
- `scripts/daily_job.py` → `publish/read_daily_log.py` → `publish/render_mail.py` → `publish/send_mail.py`

## コネクタ追加手順

1. `connectors/` に新しいファイルを追加する（例: `connectors/weather.py`）。
2. そのコネクタに以下のIFを実装する:
   - `id: str`
   - `fetch(target_date) -> result`
   - `render(result) -> { summary_blocks, raw_payload }`
3. `ingest/ingest_sources.py` の `connectors = [...]` に追加するだけでOK。

## トラブルシュート

- **Notion rich_text 2000文字制限**  
  long textは自動で分割保存します。`Notes` には巨大JSONを入れないでください。
- **PublishがDaily_Logを見つけられない**  
  対象日のDaily_Logが無い場合は送信をスキップし、ログに理由を残して正常終了します。

## メール送信の設定（初心者向け）

必要な環境変数（GitHub Actions Secrets）:

- `MAIL_FROM`: 送信元メールアドレス（Gmail）
- `MAIL_TO`: 送信先（カンマ区切りで複数可）
- `GMAIL_APP_PASSWORD`: Gmailのアプリパスワード
- `INBOX_JSON_URL`
- `TASKS_JSON_URL`
- `TASKS_CLOSED_URL`
- `DAILY_LOG_UPSERT_URL`
- `WORKERS_BEARER_TOKEN`（任意）

## HTMLメールの崩れを防ぐチェックリスト

1. **multipart/alternative になっているか**
   - `text/plain` と `text/html` の両方が含まれていること
2. **HTMLがエスケープされていないか**
   - `&lt;div&gt;` のような表示になっている場合はエスケープされている可能性
3. **charsetがUTF-8か**
   - `Content-Type: text/html; charset=utf-8` になっているか
4. **CSSはインラインのみか**
   - Gmail/iPhoneメールで崩れないために `style=""` を使用
5. **styleタグやflexに依存していないか**
   - Gmailは `<style>` を削ることがあるためインライン推奨
   - flexは最小限にしてtableレイアウト併用

## MIME出力の簡易テスト

multipart/alternative になっているか確認できます:

```bash
python scripts/test_email_mime.py
```

## まず最初に必ずやる設定（初心者向け）

> **この項目を終えてから** 次の「セットアップ手順（概要）」に進んでください。

1. **Notionの統合（Integration）を作成**
   - Notionの「設定とメンバー」→「インテグレーション」→「新規作成」。
   - 生成された **Internal Integration Token** を控える（後で `NOTION_TOKEN` として使います）。
2. **Notionデータベースを共有**
   - `Inbox / Tasks / Daily_Log` の各DBを開き、右上の「共有」から **作成したIntegrationを招待**。
   - これをしないとNotion APIがDBを読めません。
3. **Notion DB ID を取得**
   - 各DBのURLを開き、URL内の長いIDを控える（`INBOX_DB_ID` / `TASK_DB_ID` / `DAILY_LOG_DB_ID`）。
4. **メール送信に使うGmailアプリパスワードを作成**
   - Googleアカウントで **2段階認証を有効化** → アプリパスワードを生成。
   - 生成したパスワードを控える（後で `GMAIL_APP_PASSWORD` として使います）。
5. **Cloudflare WorkersのURLを確認**
   - Workersをデプロイ後、 `/api/tasks/closed` などにアクセスできるURLを控える。
   - 後で `INBOX_JSON_URL` / `TASKS_JSON_URL` / `TASKS_CLOSED_URL` / `DAILY_LOG_UPSERT_URL` に使います。

## セットアップ手順（概要）

1. **Workersデプロイ**
   - GitHub Actionsの **Deploy Cloudflare Workers** を実行
   - Cloudflare WorkersのVariables/Secretsを設定
2. **Notion DBプロパティ確認**
   - 上記の必須プロパティが完全一致で存在することを確認
3. **GitHub Actions Secrets設定**
   - メール/WorkersのURLをSecretsに登録
4. **テスト**
   - `workflow_dispatch` で手動実行（Phase A / Phase B をそれぞれ実行）
   - `/confirm/...` で確認 → `/execute/...` で更新が実行されることを確認

## 動作確認手順（昨日Done/Drop）

1. Notionで「昨日」の日付を `Done date` に入れたタスクを1件作る
2. GitHub Actionsの `workflow_dispatch` で手動実行
3. Phase A 実行後に `GET /api/daily_log?date=YYYY-MM-DD` が返ることを確認
4. Phase B 実行でメールに「昨日完了したこと」が出ることを確認

## Daily Log に Done/Drop タスクを Relation で記録する設定

1. **Tasks DB の設定**
   - `Status` に `Do` / `Done` / `Drop` を用意（名称を変える場合は Workers の `TASK_STATUS_DO` / `TASK_STATUS_DONE` / `TASK_STATUS_DROPPED` を変更）。
   - `Done date` / `Drop date` (date) を追加し、完了日/取り下げ日を入れる。
2. **Daily_Log DB の設定**
   - `Date` (date) を作成し、日付を保存する。
   - `Done Tasks` / `Drop Tasks` を Tasks DB への Relation で作成。
   - `Done Count` / `Drop Count` を Rollup で作成（`名前` を Count all）。
3. **実行**
   - Phase A (Ingest) が、前日分の `Done date` / `Drop date` を集計して当日の Daily Log に Relation をセットします。
   - `Done date` / `Drop date` が空のタスクは除外されます。

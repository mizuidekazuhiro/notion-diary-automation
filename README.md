# notion-diary-automation

Notion中心の「日記自動化MVP」を Cloudflare Workers + Python + GitHub Actions で構築するためのリポジトリです。

## 構成

- **Cloudflare Workers (TypeScript)**: Notion APIの問い合わせ、DBスキーマ検証、Daily_LogのUpsert。
- **Python**: Workers経由でInbox/Tasks/昨日のDone・Dropを取得 → メール送信 → Daily_Log Upsert。
- **GitHub Actions**: JST 07:00 でジョブ実行（UTC 22:00）。

## 新機能: 毎朝メールに「昨日の成果」を追加

- 毎朝のメールに **昨日 Done / 昨日 Drop** を追加します。
- JST基準で「昨日（00:00〜24:00）」の範囲で集計します。
- 全件表示ですが、メールが長くならないよう `<details>` の折りたたみ表示を使います。

## Notion DBの必須プロパティ

> **各DBのTitleプロパティ名が異なる**ため、プロパティ名は完全一致で管理します。

### Tasks DB (`TASK_DB_ID`)

- `Status` (select) : 値 `Do` / `Done` / `Drop` が存在すること（名称は環境変数で変更可）
- `Since Do` (date)
- `Priority` (select)
- `名前` (title)
- `Done date` (date)
- `Drop date` (date)

> **プロパティ名は完全一致で固定です**。`Someday` (checkbox) と `DoneAt` は現在のDBに無いため、コード側でも参照/更新しません（Someday状態は `Status` の値で表現します）。

### Inbox DB (`INBOX_DB_ID`)

- `Name` (title)

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
- `Notes` (rich_text)
- `Source` (select)
- `Weight` (number)
- `Done Tasks` (relation -> Tasks DB)
- `Drop Tasks` (relation -> Tasks DB)
- `Done Count` (rollup: Done Tasks の `名前` を Count all)
- `Drop Count` (rollup: Drop Tasks の `名前` を Count all)

MVPでは最低限 `Target Date` / `Activity Summary` / `Mail ID` / `Source` を埋めればOKです。
`Notes` は Notion の rich_text 制限で **2000文字まで**のため、長文は Notes に短文（サマリ/冒頭）を保存し、全文は Daily_Log のページ本文（children blocks）に分割して保存します。

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
- `TASK_STATUS_SOMEDAY` (任意: Someday判定に使うStatus値がある場合に設定)
- `REQUIRE_STATUS_EXTRA_OPTIONS` (任意: `true` の場合は Status の `Drop` / `Someday` も必須オプションとして検証)

> **NotionトークンとDB IDはWorkers側のSecretsのみ**に置き、GitHub Actionsには置きません。

### エンドポイント

| Method | Path | 説明 |
| --- | --- | --- |
| GET | `/health` | 簡易ヘルスチェック（JSONで `{ "status": "ok" }`） |
| GET | `/api/inbox` | Inbox DB の一覧取得 |
| GET | `/api/tasks` | Tasks DB の Status = "Do" と Status = "Someday" を取得 |
|  |  | ※Status = "Someday" のタスクは `confirm_promote_url` 付きで返却 |
| GET | `/api/tasks/closed?date=YYYY-MM-DD` | Tasks DB から「昨日Done/Drop」を取得（date未指定ならJSTの昨日） |
| GET | `/confirm/daily_log/upsert` | Daily_Log Upsert 確認ページ |
| POST | `/execute/api/daily_log/upsert` | Daily_Log Upsert 実行 |
| GET | `/confirm/tasks/promote?id=...` | Someday → Do 昇格の確認 |
| POST | `/execute/tasks/promote` | Someday → Do 昇格 実行 |

### GitHub Actions用URLの対応表

- `INBOX_JSON_URL`: `/api/inbox`
- `TASKS_JSON_URL`: `/api/tasks`
- `TASKS_CLOSED_URL`: `/api/tasks/closed`
- `DAILY_LOG_UPSERT_URL`: `/execute/api/daily_log/upsert`

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

### 404/401/500/502 の切り分け（よくある原因）

| ステータス | 主な原因 | 確認ポイント |
| --- | --- | --- |
| 404 Not Found | ルートのパス誤り / デプロイ先のURL誤り | `/health` のURLが正しいか |
| 401 Unauthorized | `WORKERS_BEARER_TOKEN` が設定済みなのにヘッダ未指定 | `Authorization: Bearer ...` を付ける |
| 500 Internal Server Error | 予期せぬ内部エラー | Workersログのスタックトレース |
| 502 Bad Gateway | Notion APIエラー | 返却されたJSONの `status` / `body` を確認 |

### 401/405/502 が出たときの診断手順（会社PCで手動POSTできない前提）

1. **GitHub Actionsログを確認**し、HTTPステータスとレスポンスJSONを把握する。
2. **Cloudflare Workersログ（Tail）を確認**し、`Notion API error` や `WORKERS_BEARER_TOKEN is not set; auth is disabled` などの警告を確認する。
3. ステータス別の切り分け:
   - `401`: `WORKERS_BEARER_TOKEN` の値が Actions と Workers で一致しているか。
   - `405`: URLが `/execute/api/daily_log/upsert` になっているか、POSTで送信しているか。
   - `502`: Notion APIエラー。レスポンスJSONの `status` / `body` を確認する。

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

Workersへのリクエスト例（Pythonから送信）:

```json
{
  "target_date": "YYYY-MM-DD",
  "title": "YYYY-MM-DD Daily Log",
  "activity_summary": "string",
  "mail_id": "string",
  "source": "automation",
  "data_json": "string(任意)"
}
```

## Python

- Workersの `/api/tasks` / `/api/inbox` を取得
- Workersの `/api/tasks/closed` を取得して「昨日の成果」をメールに追加
- HTMLメールを生成してSMTP送信
- 同じ実行内で `/execute/api/daily_log/upsert` にPOSTしてDaily_Logを作成/更新
- **UTF-8 / MIME** 対応済み
- `MAIL_TO` はカンマ区切りで複数対応

## GitHub Actions

- 実行タイミング: JST 07:00（UTC 22:00） + 手動実行
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
- `WORKERS_BEARER_TOKEN` は任意ですが、有効化する場合は **Cloudflare側のVariables/Secretsにも同じ値** を入れてください。

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
   - Workersをデプロイ後、 `/api/inbox` などにアクセスできるURLを控える。
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
   - `workflow_dispatch` で手動実行
   - `/confirm/...` で確認 → `/execute/...` で更新が実行されることを確認

## 動作確認手順（昨日Done/Drop）

1. Notionで「昨日」の日付を `Done date` に入れたタスクを1件作る
2. GitHub Actionsの `workflow_dispatch` で手動実行
3. メールに「昨日完了したこと」が出ることを確認
4. `GET /api/tasks/closed?date=YYYY-MM-DD` を叩いてJSONが返ることを確認

## Daily Log に Done/Drop タスクを Relation で記録する設定

1. **Tasks DB の設定**
   - `Status` に `Do` / `Done` / `Drop` を用意（名称を変える場合は Workers の `TASK_STATUS_DO` / `TASK_STATUS_DONE` / `TASK_STATUS_DROPPED` を変更）。
   - `Done date` / `Drop date` (date) を追加し、完了日/取り下げ日を入れる。
2. **Daily_Log DB の設定**
   - `Date` (date) を作成し、日付を保存する。
   - `Done Tasks` / `Drop Tasks` を Tasks DB への Relation で作成。
   - `Done Count` / `Drop Count` を Rollup で作成（`名前` を Count all）。
3. **実行**
   - GitHub Actions の日次ジョブが、前日分の `Done date` / `Drop date` を集計して当日の Daily Log に Relation をセットします。

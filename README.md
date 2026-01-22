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

### Tasks DB (`TASK_DB_ID`)

- `Status` (select) : 値 `Do` が存在すること
- `Since Do` (date)
- `Priority` (select)
- `Someday` (checkbox)
- `名前` (title)
- `Done date` (date)
- `Drop date` (date)

> **プロパティ名は完全一致で固定です**。

### Inbox DB (`INBOX_DB_ID`)

- `Title` (title)

### Daily_Log DB (`DAILY_LOG_DB_ID`)

- `Title` (title)
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

MVPでは最低限 `Target Date` / `Activity Summary` / `Mail ID` / `Source` を埋めればOKです。

## セキュリティ設計（2段階更新）

- **GET `/confirm/...`**: 確認のみ（更新禁止）
- **POST `/execute/...`**: 更新実行のみ

メールのリンクが自動踏みされる可能性があるため、更新は必ずPOSTでのみ実行します。

## Cloudflare Workers

### Secrets

Workers環境変数（Secrets）に以下を設定します。

- `NOTION_TOKEN`
- `INBOX_DB_ID`
- `TASK_DB_ID`
- `DAILY_LOG_DB_ID`
- `WORKERS_BEARER_TOKEN` (任意: Bearer認証用)

> **NotionトークンとDB IDはWorkers側のSecretsのみ**に置き、GitHub Actionsには置きません。

### エンドポイント

| Method | Path | 説明 |
| --- | --- | --- |
| GET | `/api/inbox` | Inbox DB の一覧取得 |
| GET | `/api/tasks` | Tasks DB の Status = "Do" と Someday = true を取得 |
|  |  | ※Someday = true のタスクは `confirm_promote_url` 付きで返却 |
| GET | `/api/tasks/closed?date=YYYY-MM-DD` | Tasks DB から「昨日Done/Drop」を取得（date未指定ならJSTの昨日） |
| GET | `/confirm/daily_log/upsert` | Daily_Log Upsert 確認ページ |
| POST | `/execute/api/daily_log/upsert` | Daily_Log Upsert 実行 |
| GET | `/confirm/tasks/promote?id=...` | Someday → Do 昇格の確認 |
| POST | `/execute/tasks/promote` | Someday → Do 昇格 実行 |

#### Bearer認証の例

```bash
curl -H "Authorization: Bearer $WORKERS_BEARER_TOKEN" \
  "https://xxxx.workers.dev/api/tasks/closed?date=2024-01-01"
```

> `TASK_DB_ID` を再利用するため、Secrets追加は不要です。

### Daily_Log Upsert

- **検索条件**: `Target Date` が `YYYY-MM-DD` で一致するページを検索
- 存在すれば更新 / 無ければ作成

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

`INBOX_JSON_URL`/`TASKS_JSON_URL`/`TASKS_CLOSED_URL`/`DAILY_LOG_UPSERT_URL` はWorkersのURLをセットしてください。

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
   - `workers/src/index.ts` をWorkersに配置
   - Secretsを設定
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

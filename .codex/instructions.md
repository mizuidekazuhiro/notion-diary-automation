# Codex恒久指示（notion-diary-automation）

- PR本文・Issue本文・レビューコメントは**日本語**で記述する。
- Notion API の `validation_error` は、まず **DBプロパティ名・型・環境変数名の不一致**を疑う。
- Notionプロパティ型（`title` / `rich_text` / `number` / `date` / `checkbox` / `select` / `multi_select` / `url` / `email` / `phone_number` / `formula` / `rollup`）の差異に注意する。
- `xxx is not a property that exists` は、コード側プロパティ名と Notion DB 側列名不一致を最優先で確認する。
- `expected to be rich_text` / `should be a valid ISO 8601 date string` は、値の型変換・空値処理・日付形式を確認する。
- Cloudflare Workers の Secrets / Vars は、既存README・既存コード・既存workflowを優先して判断する。
- 環境変数名を勝手に変更しない。
- 新しい必須環境変数を追加する場合は、README・`.env.example`・GitHub Actions設定例を同時更新する。
- 外部副作用のある処理（本番 deploy / Notion 書き込み / メール送信など）を CI 内で勝手に実行しない。
- メール送信処理は重複送信防止を常に意識する。
- 日付処理は JST 基準が多いため UTC/JST 変換を明示的に扱う。
- 修正は小さく、テスト可能にする。
- 既存テストがある場合は必ず実行する。
- テストがない場合は、最低限の回帰テスト追加可否を検討する。

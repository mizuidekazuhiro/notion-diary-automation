# notion-diary-automation

既存の Notion 連携挙動を変更せず、構造を理解しやすく整理したリポジトリです。

## 1. システム全体像

- 実行基盤: Cloudflare Workers (TypeScript) + Python 補助スクリプト
- 中核フロー: Notion 各DBを読み取り、`Daily Log DB` に日次集約結果を書き戻す
- 重要方針: **DB ID・環境変数名・APIリクエスト/レスポンス構造・日付計算・レート制限・出力フォーマットは変更しない**

## 2. 新ディレクトリ構造

```text
workers/src/
  domain/                # 純粋ロジック（Notion依存なし）
    daily_log_ingest.ts
    location_summary.ts
    meal_summary.ts
  application/           # ユースケース（フロー制御）
    daily_log_task_relations.ts
  infrastructure/
    notion/
      client.ts          # Notion APIアクセス共通化
  config/
    task_property_names.ts
    title_properties.ts
  utils/
    date_utils.ts
  index.ts               # エントリーポイント

tests/
  domain/
    daily_log_ingest.test.ts
    location_summary.test.ts
    meal_summary.test.ts
  application/
  infrastructure/

docs/
  notion-dataflow.md
```

## 3. データフロー図

```text
Tasks / Health / Expenses / Location Log (Notion DB)
  ↓ query
Workers application flow
  ↓ domain集約処理
Daily Log DB へ update/create
```

詳細なDB別・プロパティ別マッピングは `docs/notion-dataflow.md` を参照してください。

## 4. Notion DB構成説明

主要DB:
- `TASK_DB_ID`
- `DAILY_LOG_DB_ID`
- `INBOX_DB_ID`
- `HEALTH_DB_ID`
- `EXPENSES_DB_ID`
- `LOCATION_LOG_DB_ID`

既存のプロパティ名（`Date`, `Target Date`, `Activity Summary`, `Done Tasks`, `Drop Tasks` など）をそのまま利用します。

> 補足: `Location summary` は別システムが更新する項目です。本リポジトリの ingest/ensure/upsert では
> `Location summary` を必須プロパティとして要求せず、値の更新もしません。
> `/execute/api/daily_log/ingest_location` は内部集計を行いますが、Daily Log の `Location summary` は書き込みません。

既存設定をそのまま利用します。代表例:
- 認証/接続: `NOTION_TOKEN`
- DB: `INBOX_DB_ID`, `TASK_DB_ID`, `DAILY_LOG_DB_ID`, `HEALTH_DB_ID`, `EXPENSES_DB_ID`, `LOCATION_LOG_DB_ID`
- Tasks関連: `TASK_STATUS_PROPERTY_NAME`, `TASK_DONE_DATE_PROPERTY_NAME`, `TASK_DROP_DATE_PROPERTY_NAME`
- Health関連: `HEALTH_*_PROPERTY_NAME`
- Daily Log関連: `DAILY_LOG_*_PROPERTY_NAME`
- Expenses関連: `EXPENSES_*_PROPERTY_NAME`, `EXPENSES_DAY_START_HOUR`

## 6. 拡張手順

1. 純粋計算ロジックは `domain/` へ追加
2. ユースケースは `application/` へ追加
3. Notion連携は `infrastructure/notion/client.ts` のラッパー経由で利用
4. `index.ts` はルーティングとユースケース呼び出しのみに保つ

## 7. よく壊れるポイント

- Notionプロパティ名の不一致
- JST境界（`start <= date < end`）
- `YYYY-MM-DD` をUTC変換してしまう日付ズレ
- relation型更新時のID配列フォーマット崩れ

## 8. 実行フロー図

```text
HTTP endpoint
  ↓
index.ts
  ↓
application/*
  ↓
domain/*
  ↓
infrastructure/notion/client.ts
  ↓
Notion API
```

## テスト

```bash
cd workers
npm test
```

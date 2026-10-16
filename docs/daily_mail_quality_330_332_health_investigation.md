# Daily Mail Quality #330–#332: Health / today sleep

調査日: 2026-10-16 (JST)。このリポジトリのmainは
`521613afa2e793d07a9477b69c84a7e4accc9b1c`、上流
`mizuidekazuhiro/Daily_Log_Healthcare` のmainは
`7ec1b500b8f0ed87bdd2e3e004f36c7cdc187b1f`。参照時点のコードと本番に配置されたコードが同じかは未確認。

## 結論と証拠の限界

対象3日とも、Phase Aが読むHealthページの主要8項目は空である。
最新の独立Notion canaryも同じ欠測を観測した。Phase Cでは有効な当日睡眠候補を選べず、Phase Dは2種類の警告を残しつつ正常なメール生成を継続している。
読み取り失敗やメール表示不具合を、この2警告の原因とする証拠はない。

上流Health APIには、実測値なしのpayloadでもページを作り成功を返すコード上の不備がある。
これはモックNotionで再現した。ただし、実際の送信経路、直近の送信payload/HTTP応答、Worker受信ログ、本番設定は取得できていない。
この不備が対象3日のページを作った原因だとは確定していない。

`last_valid_at` は既存の最大50件検索内で主要項目を持つレコードの編集時刻であり、
送信元の最終実行時刻・データの測定時刻・全履歴の最終有効日ではない。

## 実行結果

| 対象日 (JST) | Issue | Phase A | 診断 |
| --- | --- | --- | --- |
| 2026-10-12 | [#330](https://github.com/mizuidekazuhiro/notion-diary-automation/issues/330) | [38382556326](https://github.com/mizuidekazuhiro/notion-diary-automation/actions/runs/38382556326) | `no_data / major_fields_empty`, `data_date=2026-10-12` |
| 2026-10-13 | [#331](https://github.com/mizuidekazuhiro/notion-diary-automation/issues/331) | [38535740099](https://github.com/mizuidekazuhiro/notion-diary-automation/actions/runs/38535740099) | `no_data / major_fields_empty`, `data_date=2026-10-13` |
| 2026-10-14 | [#332](https://github.com/mizuidekazuhiro/notion-diary-automation/issues/332) | [38703348519](https://github.com/mizuidekazuhiro/notion-diary-automation/actions/runs/38703348519) | `no_data / major_fields_empty`, `data_date=2026-10-14` |

3件とも `available_fields=[]`, `completeness=0`,
`last_valid_at=2026-09-09T14:40:00.000Z`。Phase A成功は仕様上の非ブロッキング欠測であり、実測値の取得成功を意味しない。

10月15日実行の独立[canary 38718186696](https://github.com/mizuidekazuhiro/notion-diary-automation/actions/runs/38718186696)
ではHealth schema 33列の取得に成功し、`page_found=true`, `data_date=2026-10-14`、同じ欠測を確認。
ExpensesとDaily Logの読み取りは成功し、canaryだけが欠測を理由に失敗した。

[Phase C 38703468545](https://github.com/mizuidekazuhiro/notion-diary-automation/actions/runs/38703468545)
では8候補から `selected=false / no_valid_candidate`。最終当日睡眠contextは
`sleep_available=false`, `sleep_hours=null`, `sleep_score=null`, `sleep_should_mention=false`。
30日履歴の読み取り失敗・欠落は0件だった。

[Phase D 38703949447](https://github.com/mizuidekazuhiro/notion-diary-automation/actions/runs/38703949447)
はErrors 0 / Warnings 2、6セクションすべて存在、Daily Log重複0件。
残るコードは `health_no_data` と `today_sleep_no_data`。
PR #326の文字数問題は対象外として再調査・変更していない。

## 入力からDaily Logまで

| 段階 | 最新コードで確認した責務 | 本番での確認範囲 |
| --- | --- | --- |
| 端末/送信元 | 上流READMEはiOS Shortcutを例示 | 実際のショートカット名、実行履歴、HealthKit権限、payloadは未取得 |
| 上流API | `Daily_Log_Healthcare/workers/src/legacy.ts`: `POST /api/health/daily` → 正規化 → 検証 → `upsertHealthDailyPage` | 本番受信・HTTP応答・デプロイ版は未取得 |
| 認証 | 上流 `workers/src/utils/auth.ts` は `HEALTH_API_KEY` | Secretの実値は読んでいない |
| 元データ保存 | `legacy_health_daily_service.ts` は `HEALTH_DB_ID` 優先、未設定時 `DAILY_LOG_DB_ID`、Dateでquery/upsert | 送信側と読取側DB IDの一致は未確認 |
| 別の空ページ作成経路 | `supplement_intakes_post.ts` → `daily_health_service.ts` がrelation用に日付/タイトルだけのHealthページを作れる | これが欠測ページを作ったか未確認。未マージPR [upstream #16](https://github.com/mizuidekazuhiro/Daily_Log_Healthcare/pull/16)にも関連する既存提案あり |
| 下流Worker | `workers/src/index.ts:handleDailyLogHealthIngest` は `HEALTH_DB_ID` を対象日で読み、Daily Logへ有効項目のみ転記 | Phase Aの対象日一致・空欄を確認 |
| 下流認証 | GitHub Actionsの `WORKERS_BEARER_TOKEN` | 上流の `HEALTH_API_KEY` とは別の契約 |
| Daily Log生成 | Phase A → B → C → D。Health `no_data/stale` は転記せず後続を継続 | 実行ログで後続生成と品質警告を確認 |

上流のcronは学習集計、GitHub Actionsは食事写真処理であり、Health実測値を自動取得するジョブは確認できない。
Health APIへの外部POSTが必要。食事写真workflowの成功はHealth送信の成功を意味しない。

上流の保存名 `Sleep Duration Min`, `Sleep Score`, `Readiness HRV/BPM`,
`Kcal/Protein/Fat/Carb` と下流のプロパティ解決は整合している。
現在の主要項目欠測を、静的な名前不一致だけで説明する証拠はない。

## 再現と最小修正

上流mainに、架空のテスト日付・Source・空の睡眠分数・parse不能の睡眠スコアを渡すと、
Notion queryとcreateが呼ばれ、`{ok:true, action:"created"}` が返った。
実サービスや実データへの書き込みは行っていない。

修正候補は [上流Draft PR #41](https://github.com/mizuidekazuhiro/Daily_Log_Healthcare/pull/41) に作成した。
上流のupsert直前で、数値実測項目も有効な睡眠開始/終了時刻もない場合に
Notionへアクセスせず `422 / health_measurements_empty` を返す。
ログには日付と理由コードのみを残す。
有効な部分入力（体重だけ、栄養だけ、追加睡眠項目だけ、睡眠時刻だけ）と明示的な数値0は維持する。
欠測項目を空欄や0で上書きしない。

これは不正な成功扱いを止める修正であり、HealthKit取得や失われた実測値を回復する修正ではない。
Supplement Intake由来の空ページ作成は変更しない。ページの存在をHealth成功とみなさない監視を維持する。
Daily Logの生成・品質判定コードには変更が必要な証拠がないため変更していない。

## 検証

- 上流Health回帰: `node --test tests/legacy_health_daily_service.test.mjs`: 19件PASS（既存5件+追加14件）。空入力4形態はNotion呼出し0件、有効部分入力5種類はcreate/update両方を確認。
- モックHTTP経路のsmoke: 実際の `handleLegacyRoute` から空入力422・Notion呼出し0件、認証不一致401、正常入力200を確認。実サービスには送信していない。
- 上流全体: 70件中68件PASS、学習ログ03:00/04:00境界の2件FAIL。修正前も同じ2件FAIL（56件中54件PASS）。今回の変更とは独立。
- 上流サービスのstrict TypeScript検査: 修正前後とも既存の `scalePercent` union型エラー1件。同じエラーのみであり、型検査全体PASSとは主張しない。
- 下流: `test_health_freshness`, `test_ingest_health_quality_gate`, `test_notion_readonly_canary`, `test_sleep_resolution`, `test_daily_mail_quality`, `test_enforce_daily_quality_gate`, `test_workflow_contracts`: 合計39件PASS。
- `git diff --check`: PASS。下流全体のローカル再実行はしていない。今回は下流実装に変更なし。PR作成後のGitHub CI結果はPRのChecksを参照。

## 外部で必要な操作と判定

1. iPhoneのショートカットAppで、URL末尾が `/api/health/daily` の送信処理を開く。
   自動化の有効状態、対象日のHealth検索期間、睡眠/HRV/栄養の読取権限を確認し、送信直前の辞書の項目名・型・空欄だけを確認する。値がないなら送信成功にしない。
2. Cloudflare Dashboard → Workers & Pages → 実際の送信URLに対応するWorker → Observability/Logsを開く。
   過去ログが残っていなければ、現時点の実測値で送信を1回だけ実行し、その時刻・パス・HTTP statusと端末応答を照合する。
   このリポジトリ上のWorker名候補は `daily-log-healthcare` だが、本番URLは未確認。
3. `401` なら上流WorkerのSettings → Variables and Secretsの `HEALTH_API_KEY` と端末Bearerを照合する。
   `400` はdate/型/日時検証、修正配置後の `422 / health_measurements_empty` は取得値皆無、`502` はNotion API/configを確認する。
   200でも送信フィールドとNotion対象日の実測値を確認する。ログへトークン・実測値全文を貼らない。
4. 上流と下流それぞれの `HEALTH_DB_ID` が同じHealth condition DBを指すか、Notion接続権限とDate/各数値列の型を確認する。
   上流で `HEALTH_DB_ID` 未設定なら別の `DAILY_LOG_DB_ID` に書くため、fallbackの発生を `HEALTH_DAILY_DB_CONFIG.selectedDatabaseEnvKey` で確認する。
   同じDateに複数ページがある場合は全件を確認する。下流は最新created_timeを選ぶため、空の新規ページだけを確認して有効な別ページを見落とさない。
5. 有効な同日の主要値と睡眠を保存できた後、Daily Diary 01を実行し、A→B→C→Dと独立canaryを確認する。
   通常チェーンの再実行はメール送信を伴い得るため、本調査では実行していない。
   古い警告日の解消を装うために別日の実測値を送らない。過去Issueも閉じていない。

本調査では本番デプロイ、端末変更、Secret変更、Notion書込、修復workflow実行、メール再送、警告無効化、自動マージは行っていない。

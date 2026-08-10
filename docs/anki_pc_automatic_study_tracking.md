# Anki PC Automatic Study Tracking

## 目的と構成

Ankiの学習時間はアプリを開いていた時間ではなく、Ankiのレビュー履歴 `revlog.time`（カード表示から回答ボタンを押すまでの記録時間）を使います。

```text
iPhone Anki → AnkiWeb同期 → Windows PC版Anki + AnkiConnect
→ revlogレビュー履歴 → scripts/anki_revlog_sync.py
→ Worker /execute/api/study/anki-daily
→ Notion App Usage Sessions（日次1ページ）
→ Daily Logの既存Study 3項目 → 既存の日記・メール
```

日記の「司法試験 Study」の位置・見出し・表示順は変更しません。既存の次の値だけを更新します。

- 勉強時間: `Study Minutes`
- セッション数: `Study Sessions`
- 最終利用: `Study Last Used At`

Ankiの公式マニュアルは `revlog.id` をレビュー時刻（Unix epochミリ秒）、`revlog.time` を回答までの時間（ミリ秒）と定義しています。AnkiConnect v6の `sync` と `cardReviews` を第一選択にし、利用できない場合だけSQLite backup APIでWALを含む一貫したスナップショットを読みます。

- [Anki Manual: Statistics / Manual Analysis](https://docs.ankiweb.net/stats.html#manual-analysis)
- [Anki revlog schema](https://github.com/ankitects/anki/blob/main/rslib/src/storage/schema11.sql)
- [AnkiConnect API](https://git.sr.ht/~foosoft/anki-connect)

## 集計ルール

- タイムゾーンはPC設定に依存せず、固定で `Asia/Tokyo`（UTC+09:00）です。
- 学習日は午前4時区切りです。`2026-08-10` は `2026-08-10 04:00:00` 以上、`2026-08-11 04:00:00` 未満です。
- `Study Minutes` は対象レビューの `revlog.time` 合計です。
- 前回レビューから10分以上空いたレビューを新しいセッションとします。
- 10分は `ANKI_SESSION_GAP_MINUTES` または暗号化設定ファイルで変更できます。
- `revlog.time >= 300000` の件数と割合をPCログへ記録します。日記には表示しません。
- 直近7学習日を毎回再送するため、一時的な停止は次回実行時に自動補完されます。

## 二重計上を防ぐ正本ルール

PC revlogは1日1ページにupsertします。

```text
Session ID: anki-revlog:2026-08-10
App: Anki
Device: Windows PC
Source: anki_revlog
Target Date: 2026-08-10
```

同じ日のPC revlogページがある場合、そのページをAnkiの正本とし、旧iPhoneショートカット由来の `App=Anki / Device=iPhone / Source=shortcut` はDaily Log集計から除外します。旧ページ自体は削除しません。ItojukuなどAnki以外の学習は従来どおり加算します。

同じ日を何度送っても同じ `Session ID` のページを更新するため、学習時間は倍増しません。Daily Logがまだない日はApp Usage Sessionsだけを保存し、PCの定期backfillまたはDaily Log作成直後のreconcileで後から反映します。

## Anki側で一度だけ確認する設定

PC版Ankiのデッキオプションで、タイマーの **Maximum answer seconds** を **300秒** に設定してください。60秒のままだと、論文・条文操作カードで60秒を超えた長考が `revlog.time` に記録されません。

このスクリプトはAnkiの設定を勝手に変更しません。300秒到達レビュー数はログに残るため、到達率が継続的に高い場合だけ設定見直しを検討できます。

## AnkiConnect

1. PC版Ankiを起動します。
2. Tools → Add-ons → Get Add-ons からAnkiConnectを導入します。
3. Ankiを再起動します。
4. 既定の `127.0.0.1:8765` を使用します。外部ネットワークへbindする必要はありません。
5. API keyを設定した場合だけ、セットアップ前に `ANKI_CONNECT_KEY` 環境変数へ一時的に設定します。

AnkiConnectはAnki GUIとログイン中ユーザーセッションを必要とします。PCがロック中でもログインセッションとAnkiが動作していれば実行できますが、サインアウト中は実行できません。登録タスクはAnkiが閉じていれば自動起動を試みます。

## Windows初回セットアップ（1回だけ）

Worker変更がmainへ反映・deployされた後、PowerShellでリポジトリ直下から次を1回実行します。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_anki_sync_task.ps1
```

入力するものは既存Worker URL（`DAILY_LOG_UPSERT_URL` と同じURLで可）と `WORKERS_BEARER_TOKEN` です。トークンはWindows DPAPIで現在のWindowsユーザーだけが復号できる形式にし、次へ保存します。

```text
%LOCALAPPDATA%\AnkiNotionSync\config.json
```

セットアップはPython 3.11以上、Anki、AnkiConnect v6、active profile、Worker `/health`、Task Scheduler登録を自動診断します。実行タイミングはログオン1分後、1時間ごと、毎日04:10です。ネットワーク復帰後の取りこぼしは `StartWhenAvailable` と直近7日backfillで回復します。

## ログ確認

```text
%LOCALAPPDATA%\AnkiNotionSync\logs\anki_revlog_sync.log
```

対象日、取得元、合計分、セッション数、レビュー数、300秒到達件数・率、Worker upsert結果を記録します。トークンは出力しません。最大2MB、5世代でローテーションします。

## 手動実行とbackfill

```powershell
# 通常実行
py -3 .\scripts\anki_revlog_sync.py --start-anki

# 送信せず集計だけ確認
py -3 .\scripts\anki_revlog_sync.py --start-anki --dry-run --verbose

# 特定日を再集計
py -3 .\scripts\anki_revlog_sync.py --start-anki --target-date 2026-08-10

# 直近14日を補完
py -3 .\scripts\anki_revlog_sync.py --start-anki --backfill-days 14
```

## トラブルシューティング

- `AnkiConnect is unavailable`: Ankiを起動し、AnkiConnectが有効か確認します。
- `valid api key must be provided`: `ANKI_CONNECT_KEY` を一時設定してセットアップを再実行します。
- `configured profile is not active`: Ankiで対象profileを開くか、暗号化設定のprofile指定を空にします。
- Worker `401`: `WORKERS_BEARER_TOKEN` を確認し、セットアップを再実行します。
- Worker `404`: Workerが最新mainからdeploy済みか確認します。
- SQLite fallback失敗: Ankiを開いた状態で再実行します。DBを単純コピーして読む処理は行いません。
- Daily Log未作成: エラーではありません。App Usage Sessionsへ保存され、次回backfillまたはDaily Log ingest時のreconcileで反映されます。

## iPhone側で変更すること

OFFにするもの:

- Ankiアプリの開始・終了を検知してNotionへ利用時間を送るショートカットautomation

残すもの:

- AnkiWebへの同期（AnkiMobileの自動同期、または既存の同期用automation）
- ItojukuなどAnki以外の学習時間記録ショートカット

## アンインストール

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_anki_sync_task.ps1 -Uninstall
```

Task Scheduler登録と暗号化設定を削除します。調査用ログは自動削除しません。

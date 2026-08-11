# Obsolete and compatibility inventory

最終確認日: 2026-08-11

「コードから参照されない」と「Notionから削除してよい」は同じではありません。この棚卸しでは、直ちに削除できる設定、互換期間が必要な項目、現状未使用だが所有者確認が必要な項目を分けます。このPRでは本番Notionスキーマを削除しません。

## 削除候補が確定した設定

| 項目 | 現状 | 方針 |
| --- | --- | --- |
| `HEALTH_SOURCE_VALUE` | productionのHealth queryはSourceで絞らず、テスト以外で未使用 | 設定例から削除後、Worker Env型から削除 |
| `HEALTH_SOURCE_PROPERTY_NAME` | Source DB列は有用だが、この設定値はquery/updateに未使用 | Source列は維持し、上書き設定だけ廃止候補 |
| `SAVE_EXPENSE_F_SUMMARY_TO_DAILY_LOG` | schema auditの対象切替にしか使われず、保存機能の実体を制御しない | 誤解を招くためaudit再設計時に削除 |
| `SAVE_F_RISK_TO_DAILY_LOG` | 同上 | Daily Signals移行PRで置換 |
| `SAVE_NOTES_LABEL_TO_DAILY_LOG` | 同上 | Daily Signals移行PRで置換 |

## read compatibilityとして維持する項目

| 旧項目 | 正式項目 | 現在の扱い | 削除条件 |
| --- | --- | --- | --- |
| `Sleep Analysis` | `Sleep Analysis JP` | 読取aliasのみ | 過去データに値がないことを確認後 |
| `Today Condition Forecast` | `Today Condition Forecast JP` | 読取aliasのみ | 同上 |
| `Location summary` | `Location summary (GPT)` | 読取fallback、Location writer互換 | writerと過去ページの移行後 |
| `Weather` | `Weather Summary` + 数値群 | メール・read fallback | 互換利用が7日以上ゼロになった後 |

これらは新規書き込み先として増やしません。削除は別PRでreadbackと過去データ移行を伴って行います。

## Health DBに存在するがDaily automationで未使用の項目

`Deep Percent`、`Heart Rate Percent`、`In Bed Duration Min`、`REM Percent`、`Readiness Label`、`Sleep Awakenings`、`Sleep Percent`、`Supplement Intake Log DB`、`Supplement Intakes`、`Supplements` は、現在のDaily automationのHealth転記・品質判定には使いません。

これらはダッシュボードや外部送信元で使われている可能性があるため、obsoleteとは断定しません。Notionのview、formula/rollup、ショートカット参照を確認するまで削除禁止です。

## 現役のため削除しない項目

- `Baseline Waking BPM` はHealth DBに存在し、Workerの転記・履歴読取で使います。
- `Source` と `Sleep Source` は送信元の診断に必要です。
- `Diary Notification *` と `Mail *` は名前が近いものの用途が異なり、通知とDaily mailの重複防止に使います。
- `automation-state` はF Riskの現在の正本です。Daily Signalsへのdual-write、7日間の照合、切替、バックアップ完了前に削除しません。

## 安全な廃止手順

1. コード、workflow、Notion view、formula/rollup、外部ショートカットの参照を確認する。
2. 新しい正式項目へdual-writeする。
3. 7日以上readbackを比較する。
4. readerを正式項目へ切り替える。
5. 旧項目へのwriteを止める。
6. バックアップ後、別PRまたは明示承認でスキーマを削除する。

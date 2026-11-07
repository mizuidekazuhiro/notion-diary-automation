# 日記処理の責務と互換性

2026-11-07の事実精度・欠損・配信修正を基準に、計算や送信の意味を変えずに整理した。HTTP入口、Notion項目、認証、配信先、日付境界、Anki上限は維持する。

## 主要な構成

| 責務 | 整理前 | 整理後 |
|---|---|---|
| 全体制御、外部取得、保存の順序 | daily_job / daily_job_phase_c | 同じ入口を維持。取得・保存はここから呼ぶ |
| 日記の入力射影、欠損診断、入力ハッシュ | daily_job内 | diary_inputs。DiaryInputFieldsは名前付きtupleで既存の展開・添字も維持 |
| モデル・prompt版の解決 | AI共通設定＋workflowに既定値を重複 | ai_modelsが唯一の既定値表。workflowは同名GitHub変数の値だけを渡す |
| 助言の履歴取得 | mood_advice_generator | 取得facadeを維持。generation_context指定時は再取得しない |
| 助言の履歴比較・入力型 | mood_advice_generator内 | today_advice_context。振り返り日・助言対象日・睡眠候補を区別 |
| 分類結果の品質と隔離 | generate_today_advice内162行 | notes_quality。ラベルと品質計測をNotesQualityResultで返す |
| 生成状態JSONの結合・参照・読戻し比較 | daily_jobとphase_cに分散 | generation_state。取得そのものは呼出側に残す |
| WorkerのNotion名・型解決 | index.ts内 | infrastructure/notion/property_resolver。schema取得とcacheは移さない |
| WorkerのNotion値変換 | index.ts内 | infrastructure/notion/property_values。0/欠損、分割長、formula順序を保持 |
| 位置要約の整形・保存形式 | 位置writerのmain内 | text_output。取得やAI呼出しと、内容を失わない表示整形・項目変換を分離 |
| メール描画、品質、送信、受付記録 | 既存のpublish / mail_preflight / delivery_journal | 既に分離されているため維持 |

Healthcare側はlegacy_health_daily_pureへ正規化・検証・共有field table・Notion項目変換・測定有無判定を集約。legacy_health_daily_serviceは保存I/Oと旧exportのfacadeを担当する。app usage、voice、photoは既存の単位を維持する。

## 設定と結果の契約

- モデルは、用途専用の非空環境変数、ai_models.ROLE_DEFAULTSの順。共通OPENAI_MODELは用途別設定へ影響しない。workflow変数名は変更しない。位置要約の正式workflowも同じregistryを使う。
- OpenAIのHTTP、timeout、Retry-After、使用量は既存openai_chat_utilsを使う。メッセージ組立ても重複実装を除き、旧関数名で再公開する。
- ModelRole、RequestSettings、GenerationSignature、TodayAdviceContext、AdviceJudgment、GenerationDetails、NotesQualityMetrics等で主要な受け渡しを明示する。生成成功、データ不足、縮退、失敗の値を変えない。
- 品質閾値0.8/0.8/0.4/0.5/0.9は名前を付けただけで変更しない。統計の資格条件も今回緩めない。
- 新しいcontext/qualityモジュールを助言の生成署名に含める。版が変わった直後の再生成はあり得るが、同じ版・入力の正常結果は再利用する。
- diary_inputsのpolicyは呼出側から注入する。純粋な入力構築のために環境変数やファイルを追加取得しない。

## 検証

- 保存済みの欠損・出張記録・深夜・タスク整理・学習/運動の5事例で、新旧の入力・hash・履歴比較・対象日contextを照合。外部呼出0、履歴loaderは各版1回。
- 独立レビューでWorker移動39宣言・Healthcare既存21宣言のtoken一致を確認。Healthcareの保存105ケース、metadata境界136ケース、Notes品質185ケースの新旧比較で差0。
- Workerの0/欠損、alias曖昧、formula、分割長、file保持と、Healthcareの0/空/部分入力を実関数で検査。メモリ内の意図的変異19種を新規試験で検出。
- ファイル内の実装文字列を照合していた天気試験1件は、Worker.fetch経由の3ケースへ置換した。正確な項目優先、値の代替、日時の取得、書込0を検査する。検査条件を緩めるための削除ではない。
- Python全回帰、Worker全試験、Healthcare全試験、strict TypeScript、CIを最終コードで実行する。本番適用後も書込・送信なしで版・実効モデル・保存日記を検査する。

## 今回維持した部分

- Worker全handlerの全面分割は行わない。HTTP、Notion schema cache、同じ日付のupsert/解決の順序を一度に変えないため。
- Study独自のtext.content fallback、Dropbox URLの重複判定、用途別04/05時境界は意味が異なるため統合しない。
- 旧prompt構築関数・定数等はrepo内の静的参照がないものもあるが、外部CLI/import利用を否定できないため削除しない。互換専用であることを明示する。
- legacy Worker位置生成入口は既存APIとして保持する。正式な位置要約workflowの用途別設定と混同しない。
- 無関係な機能の整形、依存更新、新framework導入は行わない。睡眠送信元の未確定原因を、整理で解決したとは扱わない。

## 比較で確認した追加不具合（整理と別コミット）

- eeaac99: 滞在しか根拠がない「働きました」「勤務しました」等の活用形が品質検査を通過した。場所の滞在と実働を分ける検査を補強し、本人メモの仕事記録は引き続き受理する。失敗する回帰を先に確認してから修正した。
- 5986363: 位置AI出力は保存直前に先頭6文、さらにNotion項目構築時に先頭1900文字へ切られていた。正規化は表示だけを整え、保存形式は全量を既存の項目サイズで分割する。後半の滞在と翌日表示、1900文字を跨ぐ内容、title/rich_text両方を検査する。
- モデル評価もraw出力だけでなく実際の正規化・保存形式を通し、saved_textを非公開の暗号化artifactへ保持する。保存相当の結果をモデル比較へ使う。
- bb55e3e: 上記の動作変更後に、表示整形とNotion項目変換をtext_outputへ移動する純粋な整理を独立コミットにした。

# Open PR disposition

調査日: 2026-08-11

現在の `main` を正本として、Open PR 20件を全件確認しました。古いbranchは直接mergeしません。判定は `main` との差分、patch equivalence、変更対象、現在のテストと実装への包含状況に基づきます。

## 結論

- 17件: 現在のmainに包含済み、または後続実装で置換済み。close候補。
- 2件: 独立した未実装機能。必要なら現在のmainへ小さく再実装。
- 1件: 権限と運用設計を見直すまでmergeしない。

## 全件分類

| PR | 判定 | 理由 |
| --- | --- | --- |
| [#225](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/225) | close: 置換済み | F Risk input hash拡張の意図は現mainで再実装済み。raw Notesをログへ出さない現方式を維持する。 |
| [#220](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/220) | keep/再実装候補 | ANA Pay転送guardは独立機能で現mainに未包含。64 commits遅れているためbranch mergeではなく、入口と転送先の要件確認後に再実装する。 |
| [#193](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/193) | close: 置換済み | Expense date優先とfallbackの意図は現行の支出日付処理・テストで置換済み。 |
| [#190](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/190) | close: 置換済み | Expense F、F Risk、Phase C statusはPR #267を含む現mainでより厳格に再実装済み。 |
| [#189](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/189) | close: 重複 | #186〜#190の同系列。現mainがschema auditとstep statusを包含。 |
| [#188](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/188) | close: 重複 | #189と実質同系列で、現mainに置換済み。 |
| [#187](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/187) | close: 重複 | #186〜#190の重複branch。 |
| [#186](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/186) | close: 置換済み | schema audit、optional-step logging、Expense F処理は現mainで更新済み。 |
| [#184](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/184) | close: patch包含 | `git cherry`で固有patchなし。Mail metadata、Study fieldsはmainに存在。 |
| [#183](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/183) | close: 後続置換 | Mail metadata readback/retryは現mainの送信重複防止と品質ゲートに置換済み。 |
| [#182](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/182) | close: 後続置換 | Mail Input SnapshotとExpense F alert連携は現mainに包含。 |
| [#173](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/173) | close: 後続置換 | Daily Log canonical page選択とduplicate mergeは現在のresolverとテストに置換済み。 |
| [#171](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/171) | hold/merge禁止 | CI失敗から自動修正するworkflowが `contents: write` 等の広い権限を持つ。110 commits遅れており、脅威モデル、承認境界、token権限を再設計するまでmergeしない。 |
| [#125](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/125) | close: patch包含 | 固有patchなし。Notes signals、prompt schema、監査はmainに包含。 |
| [#123](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/123) | close: patch包含 | 固有patchなし。05:00 sleep帰属とToday advice連携はmainに包含。 |
| [#120](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/120) | close: patch包含 | 固有patchなし。Notes/sleep/model/renderingの後続実装がmainに存在。 |
| [#117](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/117) | close: patch包含 | 固有patchなし。Today advice分析pipelineはmainに包含。 |
| [#95](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/95) | close: テスト意図包含 | done taskの `event_date` シナリオは現在のtask detail/Diary回帰テスト群で扱う。古い単体scriptはmergeしない。 |
| [#73](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/73) | keep/再設計候補 | Prompt DBからLocation promptを読む機能は現mainに未包含。330 commits遅れのため、現Notion APIとfallback要件で再実装する。 |
| [#45](https://github.com/mizuidekazuhiro/notion-diary-automation/pull/45) | close: patch包含 | 固有patchなし。mood notes link rendering修正はmainに包含。 |

## 実施方針

この文書は分類のみで、PRのclose、label変更、branch削除は行いません。#220と#73を実装する場合は、それぞれ別Issueまたは別PRとして現在のmainから作り直します。#171は自動書き込み権限を縮小し、人の承認を必須にする設計ができるまで保留します。

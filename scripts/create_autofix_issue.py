#!/usr/bin/env python3
import os, subprocess
workflow = os.getenv('TARGET_WORKFLOW_NAME','(unknown workflow)')
run_id = os.getenv('TARGET_RUN_ID','')
pr = os.getenv('TARGET_PR_NUMBER','')
branch = os.getenv('TARGET_BRANCH','')
summary = os.getenv('FAILURE_SUMMARY','失敗ログの要約を取得できませんでした。')
history = os.getenv('FIX_HISTORY','自動修正履歴の収集に失敗しました。')
body = f'''## 概要
自動修正が上限回数に到達したため停止しました。

- workflow名: {workflow}
- run_id: {run_id}
- PR番号: {pr}
- 対象ブランチ: {branch}

## 最新の失敗ログ要約
{summary}

## Codexが実施した修正
{history}

## 未解決の推定原因
- 外部設定（Notion / Cloudflare / GitHub Secrets / 外部API）または設計不整合の可能性があります。

## 人間が確認すべき設定
- Notion DB プロパティ名・型
- Cloudflare Workers Secrets/Vars
- GitHub Secrets と workflow permissions
- 外部APIの認証・レート制限・利用可否

## 次に取るべき行動
1. 失敗ジョブログを確認
2. 外部設定差分を確認
3. 必要なら手動修正PRを作成
'''
subprocess.check_call(['gh','issue','create','--title',f'[Codex停止] CI失敗が自動修正上限に達しました: {workflow}','--body',body])

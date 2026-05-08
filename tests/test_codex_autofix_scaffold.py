from pathlib import Path
import scripts.codex_autofix_guard as guard


def test_guard_stops_on_third_attempt(monkeypatch):
    monkeypatch.setattr(guard, "count_from_comments", lambda pr, repo: 2)
    code = guard.run_guard(2, "1", "", "pr-1", "o/r", None)
    assert code == 2


def test_workflow_has_guard_gating():
    text = Path(".github/workflows/codex-auto-fix.yml").read_text(encoding="utf-8")
    assert "steps.guard.outputs.can_fix == 'true'" in text
    assert "steps.guard.outputs.can_fix == 'false'" in text


def test_workflow_avoids_empty_pr_comment_on_stop():
    text = Path(".github/workflows/codex-auto-fix.yml").read_text(encoding="utf-8")
    assert "if [ -n \"${{ steps.meta.outputs.pr }}\" ]; then" in text


def test_workflow_resolves_head_sha_branch_for_events():
    text = Path(".github/workflows/codex-auto-fix.yml").read_text(encoding="utf-8")
    assert "workflow_run.head_sha" in text
    assert "pull_request.head.sha" in text
    assert "pull.data.head.sha" in text


def test_explain_and_plan_do_not_push_branch():
    text = Path(".github/workflows/codex-auto-fix.yml").read_text(encoding="utf-8")
    assert "steps.meta.outputs.mode == 'explain'" in text
    assert "steps.meta.outputs.mode == 'plan'" in text
    assert "steps.meta.outputs.mode == 'fix'" in text


def test_workflow_dispatch_pr_number_fallback_exists():
    text = Path(".github/workflows/codex-auto-fix.yml").read_text(encoding="utf-8")
    assert "const inputBranch = core.getInput('target_branch')" in text
    assert "if (pr) {" in text
    assert "github.rest.pulls.get" in text
    assert "branch = inputBranch || pull.data.head.ref" in text
    assert "headSha = inputSha || pull.data.head.sha" in text


def test_readme_mentions_fix_is_scaffold_only():
    text = Path("README.md").read_text(encoding="utf-8")
    assert "/codex fix" in text
    assert "Scaffoldブランチ更新" in text
    assert "コード自動修正は未実装" in text

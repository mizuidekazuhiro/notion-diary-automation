import pytest

from scripts import ai_models


@pytest.mark.parametrize("role", list(ai_models.ROLE_DEFAULTS))
def test_role_defaults_and_overrides_are_isolated_from_generic_model(monkeypatch, role):
    for variable, _default in ai_models.ROLE_DEFAULTS.values():
        monkeypatch.setenv(variable, "")
    monkeypatch.setenv("OPENAI_MODEL", "generic-model-must-not-override")
    variable, default = ai_models.ROLE_DEFAULTS[role]
    assert ai_models.model_for(role) == default
    monkeypatch.setenv(variable, "  explicit-role-model  ")
    assert ai_models.model_for(role) == "explicit-role-model"
    for other_role, (_variable, other_default) in ai_models.ROLE_DEFAULTS.items():
        if other_role != role:
            assert ai_models.model_for(other_role) == other_default


def test_extracted_policy_asset_and_model_changes_invalidate_identity(monkeypatch, tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    asset = scripts / "today_advice_context.py"
    asset.write_text("original policy", encoding="utf-8")
    monkeypatch.setattr(ai_models, "__file__", str(scripts / "ai_models.py"))
    monkeypatch.setenv("TODAY_ADVICE_FINAL_MODEL", "gpt-6.1-sol")
    filenames = ("scripts/today_advice_context.py",)
    first = ai_models.generation_signature("advice", filenames)
    assert first == ai_models.generation_signature("advice", filenames)
    asset.write_text("changed policy", encoding="utf-8")
    changed = ai_models.generation_signature("advice", filenames)
    assert first != changed
    monkeypatch.setenv("TODAY_ADVICE_FINAL_MODEL", "another-role-model")
    assert changed != ai_models.generation_signature("advice", filenames)

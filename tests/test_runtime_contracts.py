import json
from types import SimpleNamespace
import pytest
from scripts import verify_deployed_diary, export_daily_mail_artifacts
from scripts.ai_models import model_for, request_settings
from scripts import note_batch_labeler
from scripts.location_features import extract_location_flags
from scripts.diary_generator import validate_sentences


def test_no_inspected_diary_cannot_pass_production_verification(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_SHA', 'checked-sha')
    monkeypatch.setattr(verify_deployed_diary, 'load_config', lambda **kw: SimpleNamespace(daily_log_read_url='https://example.com/api/daily_log', bearer_token='test'))
    calls=[]
    def fetch(url, token):
        calls.append(url)
        if url.endswith('/health'):
            return {'version': 'checked-sha', 'status': 'ok'}
        return {'items': [{'target_date': '2026-11-05', 'page_id': 'p', 'diary': '旧日記'}]}
    monkeypatch.setattr(verify_deployed_diary, 'fetch_json', fetch)
    with pytest.raises(RuntimeError, match='no_persisted_diaries_verified'):
        verify_deployed_diary.main()
    assert len([url for url in calls if '/history?' in url]) == 1


def test_quality_export_reuses_inspected_sent_snapshot_without_refetch(monkeypatch, tmp_path):
    report={'run_id':'same-run','inspection_stage':'before_send','status':'warning','error_count':0,
            'warning_count':1,'issues':[],'delivery_status':'sent','metadata_status':'failed'}
    path=tmp_path/'quality_report.json'
    path.write_text(json.dumps(report), encoding='utf-8')
    monkeypatch.setenv('GITHUB_RUN_ID','same-run')
    monkeypatch.setattr('sys.argv',['export','--target-date','2026-11-05','--artifact-dir',str(tmp_path)])
    monkeypatch.setattr(export_daily_mail_artifacts,'load_config',lambda **kw: pytest.fail('must reuse exact pre-send snapshot'))
    assert export_daily_mail_artifacts.main() == 0
    assert json.loads(path.read_text(encoding='utf-8')) == report


def test_dedicated_models_and_prompt_changes_are_cache_identity(monkeypatch):
    monkeypatch.setenv('OPENAI_MODEL','gpt-4.1-mini')
    for role, expected in [('notes','gpt-6-luna'),('diary','gpt-6.1-terra'),('advice','gpt-6.1-sol')]:
        monkeypatch.delenv({'notes':'NOTES_LABEL_MODEL','diary':'DIARY_MODEL','advice':'TODAY_ADVICE_FINAL_MODEL'}[role],raising=False)
        assert model_for(role)==expected
    assert request_settings('gpt-6.1-sol')['reasoning_effort']=='low'
    assert 'temperature' not in request_settings('gpt-6.1-sol')
    monkeypatch.setattr(note_batch_labeler,'load_notes_prompt_assets',lambda:{'system_prompt':'first'})
    first=note_batch_labeler.build_notes_label_input_hash('同じメモ',model='gpt-6-luna')
    assert first != note_batch_labeler.build_notes_label_input_hash('同じメモ',model='gpt-4.1-mini')
    monkeypatch.setattr(note_batch_labeler,'load_notes_prompt_assets',lambda:{'system_prompt':'changed'})
    assert first != note_batch_labeler.build_notes_label_input_hash('同じメモ',model='gpt-6-luna')


def test_location_stay_does_not_establish_work():
    sources={'Location Stays': {'value':'勤務先に滞在した記録','certainty':'recorded'}}
    def reasons(text):
        return validate_sentences([{'text':text,'sources':['Location Stays'],'certainty':'recorded'}],sources)
    assert 'unsupported_activity_from_location' in reasons('勤務先で仕事をした。')
    assert reasons('勤務先に滞在した記録がある。') == []

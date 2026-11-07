import json
import logging
from types import SimpleNamespace
from scripts import sleep_condition_generator as sleep
from scripts.expense_f_aggregator import _notion_error_summary
from scripts import mood_advice_generator as mood


def test_sleep_generation_retains_diagnostics_without_private_logs_or_files(monkeypatch,tmp_path,caplog,capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-key')
    monkeypatch.setenv('OPENAI_MODEL','gpt-4.1-mini')
    context=sleep.SleepInsightContext({'canonical_sleep_duration_min':450,'canonical_sleep_duration_text':'7時間30分'}, {}, {'notes':'PRIVATE_NOTES_SENTINEL'})
    calls=[]
    def chat(**kwargs):
        calls.append(kwargs)
        return json.dumps({'sleep_analysis_jp':'4時間28分でした。','today_condition_forecast_jp':'4時間28分の影響があります。'},ensure_ascii=False)
    monkeypatch.setattr(sleep,'chat_completion',chat)
    metadata={}
    with caplog.at_level(logging.INFO):
        result=sleep.generate_sleep_insights(target_date='2000-01-01',context=context,diagnostics=metadata)
    assert len(calls)==2 and all(row['model']=='gpt-6.1-sol' for row in calls)
    assert metadata['fallback_used'] is True and metadata['status']=='degraded'
    assert '7時間30分' in result['sleep_analysis_jp']
    public=caplog.text+capsys.readouterr().out
    assert 'PRIVATE_NOTES_SENTINEL' not in public and '4時間28分' not in public and '7時間30分' not in public
    assert not (tmp_path/'debug').exists()


def test_input_dependent_upstream_errors_are_not_public_diagnostics(monkeypatch,caplog):
    class Response:
        status_code=400
        def json(self):return {'code':'validation_error','message':'PRIVATE_UPSTREAM_SENTINEL'}
    details=_notion_error_summary(Response(),filter_strategy='synthetic')
    assert details['notion_error_code']=='validation_error'
    assert 'PRIVATE_UPSTREAM_SENTINEL' not in json.dumps(details)
    monkeypatch.setattr(mood,'read_daily_log',lambda **kw:(_ for _ in ()).throw(RuntimeError('PRIVATE_UPSTREAM_SENTINEL')))
    with caplog.at_level(logging.WARNING):
        result=mood._safe_read_daily_log_for_advice(daily_log_read_url='read',bearer_token=None,day='2000-01-01')
    assert result.failed is True
    assert 'PRIVATE_UPSTREAM_SENTINEL' not in caplog.text


def test_valid_sleep_generation_failure_is_persisted_and_not_success(monkeypatch):
    from dataclasses import replace
    import pytest
    from scripts import daily_job
    from tests.test_today_advice_30d_pipeline import _summary
    state={'row':_summary(20,sleep_start=None,sleep_end=None,sleep_duration_min=420,
                          sleep_analysis_jp='旧い解説',today_condition_forecast_jp='旧い見通し')}
    saved=[]
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-key')
    monkeypatch.setattr(sleep,'chat_completion',lambda **kw:'invalid JSON')
    monkeypatch.setattr(daily_job,'load_recent_daily_logs',lambda **kw:[])
    monkeypatch.setattr(daily_job,'_refresh_daily_log_summary',lambda *a,**kw:state['row'])
    def save(config,*,target_date,payload):
        saved.append(payload)
        state['row']=replace(state['row'],**payload)
        return {'updated':True}
    monkeypatch.setattr(daily_job,'_save_daily_log_fields',save)
    with pytest.raises(daily_job.PhaseSemanticDegradation,match='sleep_generation_failed'):
        daily_job._generate_and_save_sleep_insights(SimpleNamespace(daily_log_read_url='read',bearer_token=None),summary=state['row'],run_id='test')
    assert len(saved)==1 and set(saved[0])=={'generation_metadata'}
    assert json.loads(state['row'].generation_metadata)['sleep']['status']=='failed'
    from scripts.data_status import mask_unavailable_summary
    masked=mask_unavailable_summary(state['row'])
    assert masked.sleep_analysis_jp is None and masked.sleep_duration_min==420


def test_sleep_degraded_and_save_failure_do_not_return_success(monkeypatch):
    import pytest
    from dataclasses import replace
    from scripts import daily_job
    from tests.test_today_advice_30d_pipeline import _summary
    row=_summary(20,sleep_start=None,sleep_end=None,sleep_duration_min=420)
    config=SimpleNamespace(daily_log_read_url='read',bearer_token=None)
    monkeypatch.setattr(daily_job,'load_recent_daily_logs',lambda **kw:[])
    for mode in ('degraded','save_failed'):
        state={'row':row}
        def generate(**kw):
            kw['diagnostics'].update(status='degraded' if mode=='degraded' else 'success',fallback_used=mode=='degraded')
            return {'sleep_analysis_jp':'7時間0分の記録。','today_condition_forecast_jp':'見通しは判定できません。'}
        def save(config,*,target_date,payload):
            if mode=='degraded': state['row']=replace(state['row'],**payload)
            return {'updated':mode=='degraded'}
        monkeypatch.setattr(daily_job,'maybe_generate_sleep_insights',generate)
        monkeypatch.setattr(daily_job,'_save_daily_log_fields',save)
        monkeypatch.setattr(daily_job,'_refresh_daily_log_summary',lambda *a,**kw:state['row'])
        expected=daily_job.PhaseSemanticDegradation if mode=='degraded' else RuntimeError
        with pytest.raises(expected,match='sleep_generation_degraded|sleep_generation_save_unconfirmed'):
            daily_job._generate_and_save_sleep_insights(config,summary=row,run_id='test')

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

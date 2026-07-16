from types import SimpleNamespace
import pytest
from scripts import backfill_missing_diaries as b

def s(**kw):
    base=dict(target_date='2026-07-15',date='2026-07-15',target_date_value='2026-07-15',activity_summary='a',mail_id='m',today_advice='t',diary='d',today_advice_generated_at='ta',diary_generated_at='dg',page_id='p')
    base.update(kw); return SimpleNamespace(**base)

def test_classify_missing(): assert b.classify_daily_log(None)[0]=='missing'
@pytest.mark.parametrize('field', b.REQUIRED_PHASE_ABC_FIELDS)
def test_required_field_missing_incomplete(field):
    x=s(**{field:''}); assert b.classify_daily_log(x)[0]=='incomplete'
def test_optional_fields_do_not_make_incomplete():
    x=s(location_summary='',weather_summary='',mail_sent_at='')
    assert b.classify_daily_log(x)[0]=='complete'
def test_dry_run_no_commands(monkeypatch):
    monkeypatch.setattr(b,'load_config',lambda **k: SimpleNamespace(daily_log_read_url='u',bearer_token='t'))
    monkeypatch.setattr(b,'read_daily_log',lambda **k: s(today_advice=''))
    monkeypatch.setattr(b,'_repair_day',lambda d: (_ for _ in ()).throw(AssertionError('called')))
    stats=b.run_backfill(days=1,end_date='2026-07-15',dry_run=True)
    assert stats.incomplete_count==1 and stats.dry_run_count==1

def test_failure_continues(monkeypatch):
    vals=[None,s()]
    monkeypatch.setattr(b,'load_config',lambda **k: SimpleNamespace(daily_log_read_url='u',bearer_token='t'))
    monkeypatch.setattr(b,'read_daily_log',lambda **k: vals.pop(0))
    monkeypatch.setattr(b,'_repair_day',lambda d: (_ for _ in ()).throw(RuntimeError('boom')))
    stats=b.run_backfill(days=2,end_date='2026-07-15',dry_run=False)
    assert stats.failed_count==1 and stats.complete_count==1

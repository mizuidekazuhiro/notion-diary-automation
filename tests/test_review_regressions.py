from dataclasses import replace
import hashlib
import json

import pandas as pd
import pytest

from tests.test_mail_preflight_flow import setup_publish
from tests.test_today_advice_30d_pipeline import _summary
from scripts.mail_preflight import preflight_report
from scripts.mail_dedupe import build_mail_input_snapshot
from scripts.location_features import extract_location_flags
from scripts.sleep_utils import resolve_sleep_for_target_date
from scripts.today_advice_feature_builder import build_daily_feature_table
from publish.render_mail import render_mail


def test_checked_diary_cannot_be_replaced_or_redated(monkeypatch):
    row, _, expense, risk = setup_publish(monkeypatch)
    text = row.target_date + '\n完了に変更したタスクは2件。'
    evidence = {'target_date': row.target_date, 'input_hash': 'input-v1',
        'text_hash': hashlib.sha256(text.encode()).hexdigest(),
        'inputs': {'Done Count': {'value': '2', 'certainty': 'status_change'}},
        'claims': [{'text': '完了に変更したタスクは2件。', 'sources': ['Done Count'], 'certainty': 'status_change'}]}
    row = replace(row, diary=text, diary_input_hash='input-v1', diary_evidence_json=json.dumps(evidence))
    def codes(candidate):
        mail = render_mail(candidate, expense_f_alert=expense, f_risk_alert=risk)
        report = preflight_report(candidate, mail, expense_f=expense, f_risk=risk, run_id='test')
        return {issue['code'] for issue in report['issues'] if issue['severity'] == 'error'}
    assert codes(row) == set()
    assert 'diary_evidence_mismatch' in codes(replace(row, diary='2026-03-19\n面談した。'))
    assert 'diary_claim_text_mismatch' in codes(replace(row, diary='2026-03-20\n協議して大きな成果を得た。'))
    assert 'diary_evidence_mismatch' in codes(replace(row, diary_input_hash='different-input'))


def test_source_status_change_invalidates_mail_but_timestamp_does_not():
    row = _summary(20, study_minutes=0)
    def snapshot(status, checked_at):
        state = json.dumps({'study': {'status': status, 'checked_at': checked_at}})
        return build_mail_input_snapshot(replace(row, data_status_json=state), expense_f_alert={}, f_risk_alert={})
    assert snapshot('query_failed', 'a') != snapshot('confirmed_zero', 'a')
    assert snapshot('query_failed', 'a') == snapshot('query_failed', 'b')


def test_previous_day_sleep_is_never_selected_for_next_day():
    yesterday = _summary(5, target_date='2026-11-05', sleep_duration_min=300, sleep_start=None, sleep_end=None)
    today = _summary(6, target_date='2026-11-06', sleep_duration_min=480, sleep_start=None, sleep_end=None)
    _, selected, _ = resolve_sleep_for_target_date(target_date='2026-11-06', today_summary=yesterday, history_summaries=[yesterday, today])
    assert selected['candidate_target_date'] == '2026-11-06'
    assert selected['resolved_sleep_duration_min'] == 480
    _, missing, _ = resolve_sleep_for_target_date(target_date='2026-11-07', today_summary=yesterday, history_summaries=[yesterday])
    assert missing is None


def test_missing_sources_remain_unknown_in_aliases_and_weather_date():
    row = _summary(20, sleep_start=None, sleep_end=None, sleep_duration_min=None, expenses_total=None,
                   weather_code=63, weather_precip_probability_max=None)
    features = build_daily_feature_table([row], {})
    assert features[['sleep_lt_6h_flag', 'sleep_short_flag', 'spending_high_flag', 'weather_bad_flag']].isna().all().all()
    aligned = replace(row, generation_metadata=json.dumps({'weather': {'target_date': row.target_date}}))
    assert build_daily_feature_table([aligned], {}).iloc[0]['weather_bad_flag'] == 1


@pytest.mark.parametrize('text, expected', [
    ('23時50分から深夜まで外出していたという位置記録。', True),
    ('深夜の外出はなかった。', False), ('深夜の外出は不明。', None),
    ('深夜に外出したとは確認できない。', None), ('自宅で過ごした。', None),
    ('終日在宅、深夜外出なし。', False),
])
def test_japanese_location_requires_affirmative_or_negative_evidence(text, expected):
    assert extract_location_flags(text)['late_outing_flag'] is expected


def test_unverified_workout_and_probability_are_not_delivered_as_facts(monkeypatch):
    row, _, expense, risk = setup_publish(monkeypatch)
    row = replace(row, workout_done=True, workout_duration_min=42,
        data_status_json='{"workout":{"status":"query_failed"}}', today_advice='今日の低気分リスクは80%です。')
    mail = render_mail(row, expense_f_alert=expense, f_risk_alert=risk)
    assert '42分' not in mail.plain_text and '42分' not in mail.html_body
    notice = '実施なしとは判断できません'
    assert mail.html_body.index(notice) < mail.html_body.index('</body>')
    report = preflight_report(row, mail, expense_f=expense, f_risk=risk, run_id='test')
    assert any(issue['code'] == 'uncalibrated_personal_probability' for issue in report['issues'])


@pytest.mark.parametrize('status',['query_failed','stale','missing'])
def test_unavailable_sleep_and_nutrition_cannot_reuse_old_explanations(monkeypatch,status):
    from scripts.diary_generator import safe_diary, diary_sources
    row,_,expense,risk=setup_publish(monkeypatch)
    state=json.dumps({'sleep':{'status':status},'nutrition':{'status':status}})
    row=replace(row,sleep_duration_min=420,sleep_score=80,sleep_start=None,sleep_end=None,
        resolved_sleep_duration_text='7時間0分',sleep_analysis_jp='古い睡眠解説',
        today_condition_forecast_jp='古い体調予測',kcal=2000,meal_summary='古い栄養要約',data_status_json=state)
    _,selected,_=resolve_sleep_for_target_date(target_date=row.target_date,today_summary=row,history_summaries=[row])
    assert selected is None
    mail=render_mail(row,expense_f_alert=expense,f_risk_alert=risk)
    for old in ('古い睡眠解説','古い体調予測','古い栄養要約','7時間0分'):
        assert old not in mail.plain_text and old not in mail.html_body
    fields={'Data Status':state,'Sleep Duration Text':'7時間0分','Meal summary':'古い栄養要約'}
    assert '7時間0分' not in safe_diary(fields,row.target_date)
    assert 'Meal summary' not in diary_sources(fields)

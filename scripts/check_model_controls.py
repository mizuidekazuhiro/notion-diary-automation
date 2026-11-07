"""Small synthetic positive/negative controls through production interfaces."""
import json
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from publish.read_daily_log import parse_daily_log_payload
from scripts.note_batch_labeler import label_notes_in_batches
from scripts.openai_chat_utils import chat_completion
from scripts.sleep_condition_generator import build_sleep_insight_context, generate_sleep_insights
from scripts.note_outcome_validator import validate_outcome_candidates
from scripts.ai_models import model_for


def main():
    rows = [parse_daily_log_payload({'found': True, 'page_id': 'synthetic', 'target_date': day, 'notes': text}, target_date=day)
        for day, text in [('2000-01-01','疲れていてストレスを感じた。昨夜は眠れなかった。'),
                          ('2000-01-02','コーヒーを飲んだ。協議タスクを完了に変更したが、実施日は未確認。'),
                          ('2000-01-03','ジムで筋トレをした。')]]
    report = []
    for model in dict.fromkeys(('gpt-4.1-mini', model_for('notes'))):
        os.environ['NOTES_LABEL_CACHE_DIR'] = f'.runtime/control-cache/{model}'
        usage=[]; audit={}
        def chat(**kw):
            start = len(usage)
            result = chat_completion(**kw, usage_sink=usage)
            for record in usage[start:]:
                record['requested_model'] = kw['model']
            return result
        labels=label_notes_in_batches(rows, chat_completion=chat, outcome_chat_completion=chat, model=model, audit=audit)
        assertions = {'fatigue': labels['2000-01-01'].fatigue_flag,
                      'stress': labels['2000-01-01'].stress_flag,
                      'sleep_issue': labels['2000-01-01'].sleep_issue_flag,
                      'state_change_is_not_achievement': not labels['2000-01-02'].achievement_flag,
                      'exercise': any(s.get('tag') in {'exercise','gym'} for s in labels['2000-01-03'].signals)}
        report.append({'role': 'notes', 'model': model, 'selected':model==model_for('notes'), 'assertions': assertions, 'usage': usage,
                       'source_grounding_rejections': audit.get('source_grounding_rejections', {}),
                       'before_source_grounding_flags': audit.get('raw_flag_counts', {}),
                       'normalized_flags': audit.get('normalized_flag_counts', {}),
                       'attempted_calls': audit.get('api_calls'), 'passed': all(assertions.values())})
    outcome_rows = [
        {'id':'state','text':'協議タスクを完了にできた。実施日は未確認。','candidate_tags':['achievement']},
        {'id':'negated','text':'資料が完成したわけではない。','candidate_tags':['achievement']},
        {'id':'uncertain','text':'資料が完成したかもしれない。','candidate_tags':['achievement']},
        {'id':'explicit','text':'資料を完成させて、達成感を覚えた。','candidate_tags':['achievement']},
    ]
    usage=[]
    def outcome_chat(**kw):
        start=len(usage)
        result=chat_completion(**kw,usage_sink=usage)
        for record in usage[start:]:
            record['requested_model']=kw['model']
        return result
    outcome=validate_outcome_candidates(outcome_rows,chat_completion=outcome_chat)
    assertions={row['id']:bool(outcome.accepted_quotes.get(row['id'])) == (row['id']=='explicit') for row in outcome_rows}
    report.append({'role':'outcome_grounding_synthetic_control','model':outcome.model,'selected':True,
                   'assertions':assertions,'validation_status':outcome.status,
                   'passed':outcome.status=='success' and all(assertions.values()),'usage':usage})
    for model in dict.fromkeys(('gpt-4.1-mini',model_for('consistency'))):
        for expected, claim in [(True,'本人のメモには「コーヒーを飲んだ」とある。'), (False,'コーヒーを飲んでリラックスした。')]:
            usage=[]
            raw=chat_completion(model=model, system_prompt='資料から確認できる文だけgrounded=trueにする。感情や目的を追加してはいけない。JSONでgroundedとviolationsを返してください。',
                user_prompt=json.dumps({'sources':{'Notes':'コーヒーを飲んだ。'},'claim':claim},ensure_ascii=False),
                response_format={'type':'json_object'}, usage_sink=usage)
            decision=json.loads(raw)
            report.append({'role':'consistency','model':model,'selected':model==model_for('consistency'),'control_expected_grounded':expected,
                           'passed':decision.get('grounded') is expected, 'usage':usage})
    output=Path('artifacts/model_controls');output.mkdir(parents=True,exist_ok=True)
    sleep_row=parse_daily_log_payload({'found':True,'page_id':'synthetic-sleep','sleep_start':'2000-01-03T23:00:00+09:00',
        'sleep_end':'2000-01-04T07:00:00+09:00'},target_date='2000-01-03')
    sleep_metadata={}
    sleep_result=generate_sleep_insights(target_date='2000-01-03',
        context=build_sleep_insight_context(today_summary=sleep_row,history_summaries=[]),diagnostics=sleep_metadata)
    report.append({'role':'sleep_synthetic_control','model':sleep_metadata.get('model'),'selected':True,
        'passed':sleep_metadata.get('status')=='success' and set(sleep_result)=={'sleep_analysis_jp','today_condition_forecast_jp'},
        'usage':sleep_metadata.get('usage',[])})
    (output/'report.json').write_text(json.dumps({'results':report,'source_mutations':0,'mails_sent':0},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'results':[{k:v for k,v in row.items() if k!='usage'} for row in report]}))
    if not all(row['passed'] for row in report if row.get('selected')):
        raise RuntimeError('selected_model_control_failed')


if __name__=='__main__':
    main()

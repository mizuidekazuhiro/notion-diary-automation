from __future__ import annotations
import os, sys, requests
from typing import Any

NOTION_VERSION='2022-06-28'
STRICT = str(os.getenv('STRICT_NOTION_SCHEMA_AUDIT','false')).lower()=='true'

EXPECTED = {
'A. Mail metadata': {
'Mail Input Hash': {'rich_text'}, 'Mail Input Snapshot': {'rich_text'}, 'Mail Sent At': {'date'}, 'Mail Version': {'number'},
'Diary Notification Sent': {'checkbox'}, 'Diary Notification Hash': {'rich_text'}, 'Diary Notification Sent At': {'date'}, 'Diary Notification Version': {'number'}},
'B. Study': {'Study Minutes': {'number'}, 'Study Sessions': {'number'}, 'Study Last Used At': {'date'}},
'C. F Risk': {'F Risk Alert': {'rich_text'}, 'F Risk Score': {'number'}, 'F Risk Reason': {'rich_text'}, 'F Risk Matched Patterns': {'rich_text'}, 'F Risk Input Hash': {'rich_text'}, 'F Risk Generated At': {'date'}},
'D. Notes Label': {'Notes Label Input Hash': {'rich_text'}, 'Notes Label Generated At': {'date'}, 'Notes Label Model': {'rich_text'}, 'Notes Sentiment Label': {'select','rich_text'}, 'Notes Sentiment Score': {'number'}, 'Notes Stress Flag': {'checkbox'}, 'Notes Fatigue Flag': {'checkbox'}, 'Notes Social Load Flag': {'checkbox'}, 'Notes Sleep Issue Flag': {'checkbox'}, 'Notes Flags JSON': {'rich_text'}, 'Notes Tags JSON': {'rich_text'}},
'E. Expense F': {'Expense F Count': {'number'}, 'Expense F Total': {'number'}, 'Expense F Merchants': {'rich_text'}, 'Expense F Categories': {'rich_text'}, 'Expense F First Time': {'date'}, 'Expense F Last Time': {'date'}, 'Expense F Data Status': {'select','rich_text'}},
'F. Weather': {'Weather': {'select','rich_text'}, 'Weather Summary': {'rich_text'}, 'Weather Location': {'rich_text'}, 'Weather Temp Max C': {'number'}, 'Weather Temp Min C': {'number'}, 'Weather Precip Probability Max': {'number'}, 'Weather Code': {'number'}, 'Weather Input Hash': {'rich_text'}, 'Weather Retrieved At': {'date'}, 'Weather Generated At': {'date'}},
}

def main()->int:
    token=os.getenv('NOTION_TOKEN','').strip(); db_id=os.getenv('DAILY_LOG_DB_ID','').strip()
    if not token or not db_id:
        print('ERROR: NOTION_TOKEN and DAILY_LOG_DB_ID are required')
        return 1
    r=requests.get(f'https://api.notion.com/v1/databases/{db_id}',headers={'Authorization':f'Bearer {token}','Notion-Version':NOTION_VERSION},timeout=20)
    r.raise_for_status()
    props=(r.json() or {}).get('properties',{})
    has_err=False
    for cat, items in EXPECTED.items():
        print(f'\n[{cat}]')
        for name, allowed in items.items():
            got=props.get(name)
            if not got:
                has_err=True
                print(f'- MISSING: {name} expected={sorted(allowed)}')
                continue
            ptype=got.get('type')
            if ptype not in allowed:
                has_err=True
                print(f'- TYPE_MISMATCH: {name} expected={sorted(allowed)} actual={ptype}')
            else:
                print(f'- OK: {name} ({ptype})')
    if has_err:
        print('\nSUMMARY: missing/type mismatch found')
        if STRICT:
            print('STRICT_NOTION_SCHEMA_AUDIT=true -> exit 1')
            return 1
        print('STRICT_NOTION_SCHEMA_AUDIT=false -> warning only')
    else:
        print('\nSUMMARY: all required properties look good')
    return 0

if __name__=='__main__':
    raise SystemExit(main())

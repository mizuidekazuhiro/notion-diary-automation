import assert from "node:assert/strict";
import worker from "./index";
const rich=(text:string)=>({rich_text:[{plain_text:text}]});
const source=[
  {id:"history-one",properties:{Date:{date:{start:"2000-01-01"}},"Target Date":{date:{start:"2000-01-01"}},Diary:rich("checked diary"),"Diary Input Hash":rich("input-v1"),"Diary Evidence JSON":rich('{"target_date":"2000-01-01"}'),"Today advice":rich("翌日への助言")}},
  {id:"history-two",properties:{Date:{date:{start:"2000-01-02"}},"Study Minutes":{number:18.25}}},
];
function matches(page:any, filter:any):boolean {
  if(filter.and) return filter.and.every((f:any)=>matches(page,f));
  if(filter.or) return filter.or.some((f:any)=>matches(page,f));
  const date=page.properties[filter.property]?.date?.start;
  if(filter.date.is_empty) return !date;
  return Boolean(date && (!filter.date.on_or_after || date>=filter.date.on_or_after) && (!filter.date.on_or_before || date<=filter.date.on_or_before));
}
let queries=0;
const original=globalThis.fetch;
globalThis.fetch=async(url,init)=>{
  assert.equal(init?.method,"POST");
  assert.ok(String(url).endsWith("/query"));
  queries++;
  const body=JSON.parse(String(init?.body));
  return Response.json({results:source.filter(page=>matches(page,body.filter)),has_more:false});
};
try {
  const response=await worker.fetch(new Request("https://worker.test/api/daily_log/history?start=2000-01-01&end=2000-01-02",{headers:{authorization:"Bearer worker"}}),
    {WORKERS_BEARER_TOKEN:"worker",NOTION_TOKEN:"notion",DAILY_LOG_DB_ID:"history-contract"} as any);
  assert.equal(response.status,200);
  const data=await response.json() as any;
  assert.equal(data.items.length,2);
  assert.equal(data.items.find((x:any)=>x.target_date==="2000-01-02").study_minutes,18.25);
  const checked=data.items.find((x:any)=>x.target_date==="2000-01-01");
  assert.equal(checked.diary,"checked diary");
  assert.equal(checked.diary_input_hash,"input-v1");
  assert.equal(JSON.parse(checked.diary_evidence_json).target_date,"2000-01-01");
  assert.equal(checked.today_advice,"翌日への助言");
  assert.equal(queries,1);
}finally{globalThis.fetch=original;}

import { parseStudyDayStartHour } from "./study_session";

// Kept as plain browser JavaScript so the Worker needs no asset pipeline or dependencies.
export const studyEntryScript = String.raw`
function buildSession(input) {
  const {date, time, minutes} = input;
  const device = input.device.trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}$/.test(time)) throw new Error('日付と開始時刻を入力してください。');
  const start = Date.parse(date + 'T' + time + ':00+09:00');
  if (!Number.isFinite(start) || new Date(start + 9*3600000).toISOString().slice(0,16) !== date+'T'+time) throw new Error('日付または時刻が正しくありません。');
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) throw new Error('学習時間は1〜1440分で入力してください。');
  if (!device || device.length > 100) throw new Error('Deviceは1〜100文字で入力してください。');
  const end = start + minutes*60000;
  return {
    session_id: 'itojuku-manual:' + start + ':' + end + ':' + device,
    started_at: new Date(start).toISOString(), ended_at: new Date(end).toISOString(),
    app: 'Itojuku', device, source: 'manual'
  };
}
// UI initialization
const form = document.querySelector('form');
const field = (id) => document.getElementById(id);
const status = field('status');
let sending = false;
let completedId = '';
const now = new Date(Date.now()+9*3600000).toISOString();
field('date').value = now.slice(0,10);
field('time').value = now.slice(11,16);
const values = () => ({date: field('date').value, time: field('time').value, minutes: Number(field('minutes').value), device: field('device').value});
function preview() {
  try {
    const session = buildSession(values());
    const end = Date.parse(session.ended_at);
    const endJst = new Date(end+9*3600000).toISOString();
    const target = new Date(end+(9-Number(form.dataset.boundary))*3600000).toISOString().slice(0,10);
    field('preview').textContent = '終了：'+endJst.slice(0,10)+' '+endJst.slice(11,16)+' JST ／ 学習日：'+target;
  } catch (error) { field('preview').textContent = error.message; }
}
for (const minutes of [15,30,45,60,90,120]) {
  const button = document.createElement('button');
  button.type = 'button'; button.textContent = minutes+'分';
  button.onclick = () => { field('minutes').value = minutes; preview(); };
  field('quick').append(button);
}
for (const delta of [-5,5]) {
  field(delta < 0 ? 'minus' : 'plus').onclick = () => {
    field('minutes').value = Math.max(1, Math.min(1440, Number(field('minutes').value)+delta)); preview();
  };
}
form.oninput = preview;
form.onsubmit = async (event) => {
  event.preventDefault();
  if (sending) return;
  let session;
  try {
    session = buildSession(values());
    if (!field('token').value.trim()) throw new Error('登録用トークンを入力してください。');
    if (session.session_id === completedId) { status.textContent = 'この内容は登録済みです。'; return; }
  } catch (error) { status.textContent = error.message; return; }
  sending = true; field('inputs').disabled = true;
  status.textContent = '登録しています…';
  try {
    const response = await fetch('/execute/api/study/session', {
      method: 'POST', headers: {'content-type':'application/json', authorization:'Bearer '+field('token').value.trim()},
      body: JSON.stringify(session)
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(response.status === 401 ? '登録用トークンを確認してください。' : '登録結果を確認できませんでした。同じ内容で再送してください。');
    completedId = session.session_id;
    status.textContent = (result.duplicate ? '登録済みの記録を確認しました。' : '登録しました。') + ' 学習日：'+result.target_date+' ／ '+result.duration_min+'分 ／ 当日の合計：'+result.daily_totals.study_minutes+'分。' + (result.daily_log_updated ? '' : ' Daily Log作成後に自動反映されます。');
  } catch (error) {
    status.textContent = error.message + ' 内容を変えずに再送すれば重複登録を防げます。';
  } finally { sending = false; field('inputs').disabled = false; }
};
preview();
`;

export function handleStudyEntry(request: Request, env: {
  STUDY_DAY_START_HOUR?: string; CANONICAL_DAY_BOUNDARY_HOUR?: string;
}): Response {
  if (request.method !== "GET") return new Response("Method not allowed", { status: 405, headers: { Allow: "GET" } });
  const boundary = parseStudyDayStartHour(env.STUDY_DAY_START_HOUR, env.CANONICAL_DAY_BOUNDARY_HOUR);
  const nonce = crypto.randomUUID();
  return new Response(`<!doctype html>
<html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>伊藤塾 学習記録</title>
<style nonce="${nonce}">
*{box-sizing:border-box}body{margin:0;background:#f4f5f0;color:#172b29;font-family:system-ui,sans-serif;line-height:1.6}
main{max-width:560px;margin:32px auto;padding:24px;background:white;border-radius:20px;box-shadow:0 6px 28px #173c3010}
h1{font-size:1.65rem;margin:0 0 6px}p{color:#52635c}fieldset{border:0;padding:0;margin:0}label{display:block;font-weight:600;margin-top:16px}
input{display:block;width:100%;min-width:0;margin-top:6px;padding:12px;border:1px solid #869b93;border-radius:8px;font:inherit}
button{min-height:44px;padding:10px 14px;border:1px solid #869b93;border-radius:8px;background:#f8faf7;color:#172b29;font:inherit;cursor:pointer}
button:focus-visible,input:focus-visible{outline:3px solid #297568;outline-offset:2px}button:disabled{opacity:.6;cursor:wait}
.row{display:flex;gap:10px}.row>label{flex:1;min-width:0}#quick{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}
.adjust{display:flex;gap:8px;align-items:center}.adjust input{flex:1}#submit{width:100%;margin-top:20px;background:#176451;color:white;border:0;font-weight:700}
#preview,#status{font-size:.9rem;padding:12px;background:#edf4ef;border-radius:8px;overflow-wrap:anywhere}#status:empty{display:none}
small{display:block;color:#52635c;margin-top:6px}@media(max-width:600px){main{margin:0;border-radius:0;min-height:100vh;padding:22px 18px}}
@media(max-width:380px){.row{display:block}}
</style><main><h1>伊藤塾 学習記録</h1><p>今日の積み重ねを、いつもの学習記録へ。</p>
<form data-boundary="${boundary}"><fieldset id="inputs">
<div class="row"><label for="date">開始日（JST）<input id="date" type="date" required></label><label for="time">開始時刻（JST）<input id="time" type="time" required></label></div>
<label for="minutes">学習時間（分）</label><div id="quick" aria-label="学習時間のクイック選択"></div>
<div class="adjust"><button id="minus" type="button" aria-label="5分減らす">−5分</button><input id="minutes" type="number" min="1" max="1440" step="1" value="30" required><button id="plus" type="button" aria-label="5分増やす">＋5分</button></div>
<label for="device">Device<input id="device" list="devices" value="Windows PC" maxlength="100" required></label><datalist id="devices"><option value="Windows PC"><option value="iPhone"><option value="iPad"><option value="Mac"><option value="Android"></datalist>
<p id="preview" aria-live="polite"></p><small>終了時刻を基準に、午前${boundary}時で学習日を区切ります。日付をまたぐ学習もそのまま入力できます。</small>
<label for="token">登録用トークン<input id="token" type="password" autocomplete="off" required></label><small>既存のWORKERS_BEARER_TOKENを使用します。この画面では保存しません。</small>
<button id="submit" type="submit">学習時間を登録</button></fieldset></form><p id="status" role="status" aria-live="polite"></p>
<noscript>登録にはJavaScriptを有効にしてください。</noscript></main><script nonce="${nonce}">${studyEntryScript}</script></html>`, {
    headers: {
      "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
      "content-security-policy": `default-src 'none'; script-src 'nonce-${nonce}'; style-src 'nonce-${nonce}'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`,
      "referrer-policy": "no-referrer", "x-content-type-options": "nosniff",
    },
  });
}

export type NotionFileEntry = Record<string, any>;
export type NotionPage = { id: string; created_time?: string; last_edited_time?: string; properties?: Record<string, any>; url?: string };
const TITLE_REGEX = /^Daily\s*Log\s*(?:｜|\|)?\s*(\d{4}-\d{2}-\d{2})$/i;
export const DUPLICATE_MERGE_FIELDS = ["Location summary (GPT)","Meal Photos","Meal summary","Mood","Notes","Activity Summary","Weather","Weather Summary","Weather Location","Weather Temp Max C","Weather Temp Min C","Weather Code","Sleep Analysis JP","Today Condition Forecast JP","Sleep Start","Sleep End","Sleep Duration","Study Minutes","Study Sessions","Study Last Used At"];
export function extractDailyLogDateFromTitle(title?: string|null){const m=(title??"").trim().match(TITLE_REGEX);return m?m[1]:null;}
export function getTitleFromPage(page:NotionPage){const p=page.properties??{};const t=(p["名前"]??p["Name"]??p["title"])?.title;return Array.isArray(t)&&t.length?((t[0]?.plain_text??t[0]?.text?.content??"").trim()):"";}
const getDate=(p:NotionPage,k:string)=>{const s=p.properties?.[k]?.date?.start;return typeof s==="string"&&s?s.slice(0,10):null};
const richText=(v:any)=>Array.isArray(v?.rich_text)?v.rich_text:[];
const empty=(v:any)=>v==null||v===""||v==="—"||(Array.isArray(v)&&v.length===0);
export function isPropertyEmpty(prop:any){if(!prop)return true; if(Array.isArray(prop.rich_text)) return prop.rich_text.length===0||!prop.rich_text.some((x:any)=>(x?.plain_text??"").trim()); if(Array.isArray(prop.files)) return prop.files.length===0; if(prop.select!==undefined) return !prop.select?.name; if(prop.number!==undefined) return prop.number==null; if(prop.date!==undefined) return !prop.date?.start; return empty(prop);}
function hasText(page:NotionPage,k:string){return richText(page.properties?.[k]).some((x:any)=>(x?.plain_text??"").trim());}
function hasFiles(page:NotionPage,k:string){const f=page.properties?.[k]?.files;return Array.isArray(f)&&f.length>0;}
function hasSelect(page:NotionPage,k:string){return !!page.properties?.[k]?.select?.name;}
export function isPageMatchedByDateOrTitle(page:NotionPage,targetDate:string){return getDate(page,"Date")===targetDate||getDate(page,"Target Date")===targetDate||extractDailyLogDateFromTitle(getTitleFromPage(page))===targetDate;}
export function chooseCanonicalDailyLogPage(pages:NotionPage[],targetDate:string){if(!pages.length)return null;return [...pages].sort((a,b)=>{const sa=[getDate(a,"Date")===targetDate&&getDate(a,"Target Date")===targetDate?1:0,["Diary","Today advice","Weather","Mail ID"].some(k=>hasText(a,k))?1:0,(hasText(a,"Location summary (GPT)")||hasFiles(a,"Meal Photos")||hasSelect(a,"Mood")||hasText(a,"Notes"))?1:0];const sb=[getDate(b,"Date")===targetDate&&getDate(b,"Target Date")===targetDate?1:0,["Diary","Today advice","Weather","Mail ID"].some(k=>hasText(b,k))?1:0,(hasText(b,"Location summary (GPT)")||hasFiles(b,"Meal Photos")||hasSelect(b,"Mood")||hasText(b,"Notes"))?1:0];for(let i=0;i<3;i++){if(sa[i]!==sb[i])return sb[i]-sa[i];}const ae=new Date(a.last_edited_time??0).getTime(),be=new Date(b.last_edited_time??0).getTime();if(ae!==be)return be-ae;return new Date(a.created_time??0).getTime()-new Date(b.created_time??0).getTime();})[0];}
function normalizeDropbox(url:string){if(!/dropbox\.com/i.test(url)) return url; const u=new URL(url);u.searchParams.delete("dl");u.searchParams.delete("raw");u.searchParams.set("raw","1");return u.toString();}
function extract(entry:any):string|null{if(typeof entry==="string"){if(entry.startsWith("file://")){try{const dec=decodeURIComponent(entry.slice(7));const obj=JSON.parse(dec);return extract(obj);}catch{return null;}}if(/^https?:\/\//.test(entry)) return normalizeDropbox(entry);return null;}if(!entry||typeof entry!=="object")return null;return extract(entry?.external?.url)||extract(entry?.file?.url)||extract(entry?.source)||extract(entry?.url)||extract(entry?.source_url)||null;}

export function toNotionUpdateProperty(prop:any): any | null {
  if (!prop || typeof prop !== "object") return null;
  if (Array.isArray(prop.rich_text)) return { rich_text: prop.rich_text };
  if (Array.isArray(prop.files)) return { files: prop.files };
  if (prop.select !== undefined) return { select: prop.select };
  if (prop.date !== undefined) return { date: prop.date };
  if (prop.number !== undefined) return { number: prop.number };
  if (prop.checkbox !== undefined) return { checkbox: prop.checkbox };
  return null;
}

export function mergeNotionFilesDedup(a:any[]=[],b:any[]=[]){const out:any[]=[];const seen=new Set<string>();for(const item of [...a,...b]){const k=extract(item);const key=k?`u:${k}`:`j:${JSON.stringify(item)}`;if(seen.has(key))continue;seen.add(key);out.push(item);}return out;}
export function buildDuplicateMergePatch(canonical:NotionPage,dups:NotionPage[]){const properties:Record<string,any>={};const mergedFields:string[]=[];for(const field of DUPLICATE_MERGE_FIELDS){const c=canonical.properties?.[field];if(field==="Meal Photos"){const cFiles=Array.isArray(c?.files)?c.files:[];const dFiles=dups.flatMap(d=>Array.isArray(d.properties?.[field]?.files)?d.properties?.[field]?.files:[]);const merged=mergeNotionFilesDedup(cFiles,dFiles);if(merged.length!==cFiles.length){properties[field]={files:merged};mergedFields.push(field);}continue;}if(!isPropertyEmpty(c)) continue;const src=dups.map(d=>d.properties?.[field]).find(p=>!isPropertyEmpty(p));if(src){const normalized=toNotionUpdateProperty(src);if(normalized){properties[field]=normalized;mergedFields.push(field);}}}
return {properties,mergedFields,hasChanges:Object.keys(properties).length>0,duplicateFieldsPresent:{location_summary:dups.some(d=>!isPropertyEmpty(d.properties?.["Location summary (GPT)"])),meal_photos:dups.some(d=>!isPropertyEmpty(d.properties?.["Meal Photos"])),mood:dups.some(d=>!isPropertyEmpty(d.properties?.["Mood"])),notes:dups.some(d=>!isPropertyEmpty(d.properties?.["Notes"]))}};}

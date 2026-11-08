// Interface messages only. Prompts, protocol values and fixed model triggers stay intact.
export const locales = [
  {code:'en', name:'English'}, {code:'nb', name:'Norsk bokmål'},
  {code:'pl', name:'Polski'}, {code:'ja', name:'日本語'},
  {code:'zh', name:'简体中文'}, {code:'es', name:'Español'},
];
export const storageKey = 'flux2-klein-studio-locale-v1';
let current = 'en', messages = {}, revision = 0;
const catalogs = new Map(), originals = new Map();
export function locale() {return current;}
async function catalog(code) {
  if (catalogs.has(code)) return catalogs.get(code);
  const response = await fetch(`/static/locales/${code}.json`, {cache:'no-cache'});
  if (!response.ok) throw new Error('Could not load interface language.');
  const value = await response.json(); catalogs.set(code,value); return value;
}
function interpolate(source, values) {
  const template = typeof messages[source] === 'string' ? messages[source] : source;
  return template.replace(/\{(\d+)\}/g, (match,index) => index < values.length ? String(values[index]) : match);
}
export function msg(source, ...values) {
  const key = typeof source === 'string' ? source : source.reduce((text,part,index) => text + (index ? `{${index-1}}` : '') + part,'');
  const result = interpolate(key,values);
  // Remember fixed UI messages only, never interpolated prompts or user content.
  if (!values.length && Object.hasOwn(messages,key)) originals.set(result,key);
  return result;
}
export function retranslate(value) {return msg(originals.get(value) || value);}
export function replaceOptions(select, ...options) {
  const value = select.value;
  select.replaceChildren(...options);
  if ([...select.options].some(option=>option.value===value)) select.value=value;
}
export function applyTranslations(root = document) {
  root.querySelectorAll('[data-i18n]').forEach(node => {node.textContent = msg(node.dataset.i18n);});
  for (const attribute of ['title','aria-label','placeholder','alt']) {
    root.querySelectorAll(`[data-i18n-${attribute}]`).forEach(node => {
      node.setAttribute(attribute,msg(node.getAttribute(`data-i18n-${attribute}`)));
    });
  }
}
export async function setLocale(code) {
  if (!locales.some(item=>item.code===code)) return;
  const request = ++revision;
  const [english, translated] = await Promise.all([catalog('en'),code==='en' ? {} : catalog(code)]);
  if (request!==revision) return;
  messages = {...english,...Object.fromEntries(Object.entries(translated).filter(([,value])=>typeof value==='string'&&value.trim()))};
  current = code;
  document.documentElement.lang = code; document.documentElement.dir = 'ltr';
  try {localStorage.setItem(storageKey,code);} catch { /* Storage is optional. */ }
  history.replaceState(null,'',`/${code}${location.search}${location.hash}`);
  applyTranslations();
  const picker = document.getElementById('ui-locale'); if (picker) picker.value=code;
  window.dispatchEvent(new CustomEvent('ui-locale-changed'));
}
export async function initializeI18n() {
  const explicit = location.pathname.match(/^\/([a-z]{2})\/?$/)?.[1];
  let saved; try {saved=localStorage.getItem(storageKey);} catch { /* Storage is optional. */ }
  const code = locales.some(item=>item.code===explicit) ? explicit : locales.some(item=>item.code===saved) ? saved : 'en';
  const picker = document.getElementById('ui-locale');
  if (picker) {
    picker.replaceChildren(...locales.map(item=>new Option(item.name,item.code)));
    picker.disabled=true;
    picker.onchange=async()=>{
      picker.disabled=true;
      try {await setLocale(picker.value);}
      catch {window.dispatchEvent(new CustomEvent('ui-locale-error',{detail:msg('Could not change interface language. Check the connection and try again.')}));}
      finally {picker.value=current;picker.disabled=false;}
    };
  }
  try {await setLocale(code);}
  catch {if(code!=='en') {try {await setLocale('en');} catch { /* English HTML remains usable offline. */ }}}
  finally {if(picker){picker.value=current;picker.disabled=false;}}
}

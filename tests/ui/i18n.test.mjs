import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import {pathToFileURL} from 'node:url';
import path from 'node:path';

const staticRoot = path.resolve('src/flux2_klein_studio/static');
const catalogs = Object.fromEntries(await Promise.all(['en','nb','pl','ja','zh','es'].map(async code => [code,JSON.parse(await readFile(path.join(staticRoot,`locales/${code}.json`),'utf8'))])));
let saved = null, writes = [], events = [], routes = [], picker = null;
globalThis.document = {documentElement:{},querySelectorAll:()=>[],getElementById:()=>picker};
globalThis.window = {dispatchEvent:event=>events.push(event.type)};
globalThis.CustomEvent = class {constructor(type,options){this.type=type;this.detail=options?.detail;}};
globalThis.localStorage = {getItem:()=>saved,setItem:(key,value)=>{saved=value;writes.push([key,value]);}};
globalThis.location = {pathname:'/',search:'?test=1',hash:'#refine'};
globalThis.history = {replaceState:(_state,_title,url)=>routes.push(url)};
globalThis.fetch = async url => ({ok:true,json:async()=>structuredClone(catalogs[url.match(/\/([a-z]+)\.json$/)[1]])});
const i18n = await import(pathToFileURL(path.join(staticRoot,'i18n.js')));

test('root uses saved language; explicit URLs override it; unknown preference uses English',async()=>{
  saved='pl'; await i18n.initializeI18n(); assert.equal(i18n.locale(),'pl');
  saved='pl'; location.pathname='/nb/'; await i18n.initializeI18n(); assert.equal(i18n.locale(),'nb');
  saved='unknown'; location.pathname='/'; await i18n.initializeI18n(); assert.equal(i18n.locale(),'en');
  assert.equal(routes.at(-1),'/en?test=1#refine');
  assert.ok(events.includes('ui-locale-changed'));
});
test('translations preserve substitutions without interpreting user text as HTML',async()=>{
  await i18n.setLocale('ja');
  const seed=12345, seconds='2.75';
  assert.equal(i18n.msg`${seconds} seconds elapsed`,'2.75 秒経過');
  assert.ok(i18n.msg`${1024} × ${1024} · Seed ${seed} · ${seconds} s generation`.includes('12345'));
  const input='<img src=x onerror=alert(1)>', source=['Connection interrupted. Your job may still be running. ',''];
  assert.ok(i18n.msg(source,input).includes(input));
  assert.equal(i18n.msg('Unknown diagnostic: exact bytes'),'Unknown diagnostic: exact bytes');
  for (const key of ['constructor','toString','__proto__']) assert.equal(i18n.msg(key),key);
});
test('blank or missing translations fall back to English',async()=>{
  catalogs.es['Choose a picture']=''; delete catalogs.es['Clear reference'];
  await i18n.setLocale('es');
  assert.equal(i18n.msg('Choose a picture'),'Choose a picture');
  assert.equal(i18n.msg('Clear reference'),'Clear reference');
});
test('changing language does not require browser storage',async()=>{
  const storage=globalThis.localStorage;
  globalThis.localStorage={getItem:()=>{throw Error('blocked');},setItem:()=>{throw Error('blocked');}};
  location.pathname='/zh'; await i18n.initializeI18n(); assert.equal(i18n.locale(),'zh');
  globalThis.localStorage=storage;
});
test('a failed catalog leaves the active language intact and is retryable',async()=>{
  const original=globalThis.fetch;
  // Import a fresh module to avoid its successful catalog cache.
  const fresh=await import(pathToFileURL(path.join(staticRoot,'i18n.js'))+'?failure-test');
  await fresh.setLocale('en');
  globalThis.fetch=async url=>url.includes('/pl.json')?{ok:false}:original(url);
  await assert.rejects(fresh.setLocale('pl')); assert.equal(fresh.locale(),'en');
  globalThis.fetch=original; await fresh.setLocale('pl'); assert.equal(fresh.locale(),'pl');
});
test('a late response cannot overwrite the last language choice',async()=>{
  const original=globalThis.fetch;
  let release;
  const blocked=new Promise(resolve=>{release=resolve;});
  globalThis.fetch=async url=>{if(url.includes('/nb.json'))await blocked;return original(url);};
  const fresh=await import(pathToFileURL(path.join(staticRoot,'i18n.js'))+'?race-test');
  const first=fresh.setLocale('nb');await fresh.setLocale('pl');release();await first;
  assert.equal(fresh.locale(),'pl');globalThis.fetch=original;
});
test('localized option labels retain machine values and the current selection',()=>{
  const select={value:'outpaint',options:[],replaceChildren(...items){this.options=items;this.value=items[0]?.value??'';}};
  i18n.replaceOptions(select,{value:''},{value:'outpaint'},{value:'zoom'});
  assert.equal(select.value,'outpaint');
});
test('paint metadata translation never redraws the existing edit canvas',async()=>{
  const {GreenPainter}=await import(pathToFileURL(path.join(staticRoot,'outpaint.js')));
  const painter=Object.create(GreenPainter.prototype);
  const metadata={textContent:''};
  await i18n.setLocale('pl');
  const getElement=document.getElementById;
  document.getElementById=id=>{assert.equal(id,'paint-meta');return metadata;};
  painter.source={};painter.width=1536;painter.height=1024;
  painter.canvas={width:1248,height:832,getContext:()=>{throw Error('Metadata must not redraw strokes');}};
  painter.describe();
  assert.ok(metadata.textContent.includes('1536 × 1024'));
  assert.ok(metadata.textContent.includes('1248 × 832'));
  document.getElementById=getElement;
});

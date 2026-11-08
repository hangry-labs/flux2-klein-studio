import {msg, retranslate, replaceOptions} from './i18n.js';
import {runtimeState} from './shell.js';
import {GreenPainter} from './outpaint.js';
import {ZoomSelector} from './zoom.js';
const $ = (id) => document.getElementById(id);
const terminal = new Set(['completed', 'failed', 'cancelled']);
let api, reference = null, active = null, result = null, uploading = false, polling = false, recipeURL = null;
let formats = [];
let painter, zoomer;
let additional = [];
async function discardAdditional() {const old=additional;additional=[];for(const item of old) await api.request(`/api/references/${item.id}`,{method:'DELETE'}).catch(()=>{});}
function renderReferences() {
  const list=$('refine-reference-list'); list.replaceChildren();
  for(const [index,item] of additional.entries()) {
    const card=document.createElement('div'), image=document.createElement('img'), label=document.createElement('span');
    image.src=item.image; image.alt=msg`Reference ${index+2}`; label.textContent=msg`Image ${index+2}`;
    card.append(image,label);
    for(const [text,action] of [['↑',()=>{if(index>0){[additional[index-1],additional[index]]=[additional[index],additional[index-1]];controls();}}],[msg('Remove'),async()=>{additional.splice(index,1);await api.request(`/api/references/${item.id}`,{method:'DELETE'}).catch(()=>{});controls();}]]) {
      const button=document.createElement('button');button.type='button';button.textContent=text;button.className='subtle';button.disabled=Boolean(uploading||refineRunning()||(text==='↑'&&index===0));button.setAttribute('aria-label',msg`${text==='↑'?msg('Move earlier'):msg('Remove')} reference ${index+2}`);button.onclick=action;card.append(button);
    }
    list.append(card);
  }
}
async function uploadFiles(files, append=false) {
  if(!files.length)return;
  if(files.length+(append?additional.length+1:0)>4)return note(msg('Choose at most four references.'));
  if((append||files.length>1)&&$('refine-adapter').value)return note(msg('Multiple references use None · Base model.'));
  if(files.some(file=>file.size>16*1024*1024))return note(msg('Each reference must be at most 16 MiB.'));
  uploading=true;note();controls();const prepared=[];
  try {
    for(const file of files){const body=new FormData();body.append('image',file);if(outpaint())body.append('preserve_green','true');prepared.push(await api.request('/api/references',{method:'POST',body}));}
    if(append)additional.push(...prepared);else {await choose(prepared.shift());additional=[...prepared];}
    prepared.length=0;
  } catch(error){note(error.message);} finally {for(const item of prepared)await api.request(`/api/references/${item.id}`,{method:'DELETE'}).catch(()=>{});uploading=false;controls();}
}
const outpaint = () => $('refine-adapter').value === 'outpaint';
const zoom = () => $('refine-adapter').value === 'zoom';
function adapterChanged(updatePrompt = true) {
  const profile = api.config?.edit_adapters.find(item => item.id === $('refine-adapter').value);
  $('refine-adapter-help').textContent = profile ? msg(profile.help) + (profile.unavailable_reason ? ' ' + msg(profile.unavailable_reason) : '') : msg('Use the base model for instruction-based editing.');
  $('outpaint-instructions').hidden = !profile;
  $('paint-tools').hidden = !outpaint();
  $('paint-canvas').hidden = !outpaint() || !reference;
  $('zoom-tools').hidden = !zoom();
  $('zoom-canvas').hidden = !zoom() || !reference;
  $('refine-source-image').hidden = Boolean(profile) || !reference;
  if (profile) {
    $('outpaint-default-prompt').textContent = profile.default_prompt;
    const text = $('refine-prompt').value.trim();
    if (updatePrompt && text.startsWith(profile.default_prompt)) $('refine-prompt').value = text.slice(profile.default_prompt.length).replace(/^[.\s]+/,'');
  }
  $('edit-instruction-title').textContent = zoom() ? msg('Fixed zoom instruction · always included') : msg('Fixed fill instruction · always included');
  $('edit-instruction-help').textContent = zoom() ? msg('Draw a rectangle on the reference. Extra focus details below are optional.') : msg('Add an optional description below, or leave it empty for a natural fill.');
  $('refine-prompt-label').textContent = zoom() ? msg('Optional: what should the zoom focus on?') : profile ? msg('Optional: what should fill the green space?') : msg('What should change?');
  $('refine-prompt').required = !profile;
  $('refine-prompt').placeholder = zoom() ? msg('For example: focus on the robot’s face. Leave empty to use only the rectangle.') : profile ? msg('For example: a brick wall matching the lighting. Leave empty for a natural fill.') : msg('Keep the subject and composition, but change the lighting to warm sunset.');
  $('refine-use-result').textContent = zoom() ? msg('⌕ Zoom again from this result') : msg('✎ Use this result');
  $('refine-submit').textContent = zoom() ? msg('⌕ Zoom into selection') : msg('✎ Refine picture');
  if (updatePrompt) painter?.resize(Number($('refine-width').value),Number($('refine-height').value));
  controls();
}
export function refineRunning() {return active && !terminal.has(active.status);}
function note(text = '') {$('refine-message').textContent = retranslate(text);}
function controls() {
  const running = refineRunning();
  $('refine-add-reference').disabled=Boolean(!reference||running||uploading||additional.length>=3||$('refine-adapter').value);
  for(const option of $('refine-adapter').options)if(option.value)option.disabled=additional.length>0||api.config?.edit_adapters.find(p=>p.id===option.value)?.available===false;
  renderReferences();
  if(reference)$('refine-reference-meta').textContent=msg`Image 1 of ${additional.length+1} · ${reference.original_width} × ${reference.original_height} source · ${reference.width} × ${reference.height} prepared. References stay temporary unless saved with the result.`;
  $('refine-submit').disabled = !reference || running || uploading || (zoom() && !zoomer?.hasSelection());
  $('refine-upload').disabled = Boolean(running || uploading);
  $('refine-upload-button').disabled = $('refine-upload').disabled;
  $('refine-add-button').disabled = $('refine-add-reference').disabled;
  $('refine-clear').disabled = !reference || running || uploading;
  $('refine-use-result').disabled = Boolean(running || uploading);
  for (const id of ['refine-adapter','refine-resolution','refine-width','refine-height']) $(id).disabled = Boolean(running || uploading);
  painter?.setDisabled(running || uploading || !reference || !outpaint());
  zoomer?.setDisabled(running || uploading || !reference || !zoom());
  $('refine-cancel').hidden = !running;
  $('refine-cancel').disabled = active?.status === 'cancelling';
  $('refine-busy').hidden = !running;
  $('refine-state').textContent = uploading ? msg('Preparing reference') : running ? msg(active.status) : result ? msg('Completed') : reference ? msg('Ready to refine') : msg('Choose a picture');
  $('refine-busy-title').textContent = active?.status === 'queued' ? msg`Waiting · Queue position ${active.queue_position}` : active?.status === 'loading' ? msg('Loading your local model…') : active?.status === 'processing' ? msg('Preparing your picture…') : active?.status === 'cancelling' ? msg('Stopping refinement…') : msg('Refining your picture…');
  if (running && !$('refine-panel').hidden) runtimeState('busy', active.status === 'queued' ? msg('Refinement queued') : msg('Refining image'));
}
function sizeChanged() {
  const size = `${$('refine-width').value}x${$('refine-height').value}`;
  $('refine-resolution').value = [...$('refine-resolution').options].some(option => option.value === size) ? size : 'custom';
  painter?.resize(Number($('refine-width').value),Number($('refine-height').value));
  zoomer?.setOutputSize(Number($('refine-width').value),Number($('refine-height').value));
}
function clearResult() {
  result = null; $('refine-result-image').hidden = true;
  $('refine-result-image').removeAttribute('src');
  $('refine-result-empty').hidden = false; $('refine-result-actions').hidden = true;
  if (recipeURL) URL.revokeObjectURL(recipeURL); recipeURL = null;
}
async function choose(next, dimensions = null, markedZoom = false) {
  const [width, height] = dimensions || [next.width, next.height];
  const scale = Math.min(1, 1920 / Math.max(width, height));
  const outputWidth = Math.max(64, Math.floor(width * scale / 16) * 16);
  const outputHeight = Math.max(64, Math.floor(height * scale / 16) * 16);
  // Decode before replacing the current reference and its result. A failed
  // preview must leave the previous selection usable, without orphaning a handle.
  try {await painter.setSource(next.image,outputWidth,outputHeight);}
  catch (error) {await api.request(`/api/references/${next.id}`,{method:'DELETE'}).catch(()=>{});throw error;}
  await discardAdditional();
  const old = reference; reference = next; clearResult();
  $('refine-source-image').src = next.image; $('refine-source-image').hidden = false;
  $('refine-source-empty').hidden = true;
  $('refine-reference-meta').textContent = msg`${next.original_width} × ${next.original_height} source · ${next.width} × ${next.height} prepared reference. Temporary until saved with a refinement.`;
  $('refine-width').value = outputWidth;
  $('refine-height').value = outputHeight;
  zoomer.setSource(painter.source,markedZoom,Number($('refine-width').value),Number($('refine-height').value));
  $('refine-prompt').value = ''; sizeChanged(); adapterChanged();
  if (old && old.id !== next.id) await api.request(`/api/references/${old.id}`, {method: 'DELETE'}).catch(() => {});
}
export async function refineFromJob(item) {
  api.switchView('refine');
  if (refineRunning() || uploading) return note(msg('Finish or cancel the current refinement before choosing another picture.'));
  uploading = true; note(); controls();
  try {
    const next = await api.request('/api/references', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({source_job_id: item.id, ...(outpaint() ? {preserve_green:true} : {})})});
    await choose(next, [item.settings.width, item.settings.height]);
    $('refine-prompt').focus();
  } catch (error) {note(error.message);} finally {uploading = false; controls();}
}
export async function reviewRefinement(item) {
  api.switchView('refine');
  if (refineRunning() || uploading) return note(msg('Finish or cancel the current refinement before opening another recipe.'));
  uploading = true; note(); controls();
  try {
    const response = await fetch(`/api/jobs/${item.id}/reference`, {cache: 'no-store'});
    if (!response.ok) throw new Error(msg('The saved reference is unavailable. Choose a new reference to continue.'));
    const body = new FormData(); body.append('image', await response.blob(), 'reference.png');
    $('refine-adapter').value = item.settings.adapter || '';
    if (outpaint()) body.append('preserve_green','true');
    await choose(await api.request('/api/references', {method: 'POST', body}), [item.settings.width, item.settings.height], zoom() ? item.settings.reference?.zoom_region || true : false);
    $('refine-prompt').value = item.settings.prompt;
    for(let index=1;index<(item.settings.references?.length||1);index++) {
      const response=await fetch(`/api/jobs/${item.id}/reference?index=${index}`,{cache:'no-store'});
      if(!response.ok)throw new Error(msg('A saved reference is unavailable; restore all references before generating.'));
      const body=new FormData();body.append('image',await response.blob(),`reference-${index+1}.png`);
      additional.push(await api.request('/api/references',{method:'POST',body}));
    }
    adapterChanged();
    $('refine-seed').value = item.settings.seed; $('refine-steps').value = item.settings.steps;
    $('refine-random-seed').checked = false; $('refine-seed').readOnly = false;
    active = null; showResult(item);
  } catch (error) {
    await discardAdditional();
    if(reference) await api.request(`/api/references/${reference.id}`,{method:'DELETE'}).catch(()=>{});
    reference=null;painter.clear();zoomer.clear();clearResult();$('refine-source-image').hidden=true;$('refine-source-empty').hidden=false;
    note(error.message);
  } finally {uploading = false; controls();}
}
function download() {
  if (!result) return;
  const format = formats.find(item => item.value === $('refine-format').value) || formats[0];
  $('refine-download').href = `/api/jobs/${result.id}/download?format=${format.value}`;
  $('refine-download').download = `klein-refined-${result.settings.seed}.${format.extension}`;
}
function showResult(item) {
  result = item; $('refine-result-image').src = item.image; $('refine-result-image').hidden = false;
  $('refine-result-empty').hidden = true; $('refine-result-actions').hidden = false;
  $('refine-result-meta').textContent = msg`${item.settings.width} × ${item.settings.height} · Seed ${item.settings.seed} · ${item.seconds.toFixed(2)} s generation`;
  $('refine-result-prompt').textContent = item.settings.effective_prompt || item.settings.prompt;
  if (recipeURL) URL.revokeObjectURL(recipeURL);
  recipeURL = URL.createObjectURL(new Blob([JSON.stringify({model: api.config.model, revision: api.config.revision, ...item.settings, reduction: 'ordered'}, null, 2)], {type: 'application/json'}));
  $('refine-recipe').href = recipeURL; $('refine-recipe').download = `klein-refined-${item.settings.seed}.json`; download();
}
async function poll() {
  if (!refineRunning() || polling || document.hidden) return;
  polling = true;
  try {
    active = await api.request(`/api/jobs/${active.id}`);
    if (active.status === 'completed') {showResult(active); await api.refreshGallery();}
    else if (active.status === 'failed') note(active.error);
    else if (active.status === 'cancelled') note(msg('Refinement cancelled. Your reference is still available.'));
    controls();
  } catch (error) {
    if (error.status === 404) {active = null; controls(); note(msg('This job is no longer available. Your reference is still ready for another refinement.'));}
    else note(error.message);
  } finally {polling = false;}
}
export function configureRefine(config, changingLocale = false) {
  api.config = config; formats = config.download_formats;
  replaceOptions($('refine-adapter'), new Option(msg('None · Base model'),''), ...config.edit_adapters.map(item => {const option = new Option(msg(item.label) + (item.available ? '' : msg(' · unavailable offline')),item.id); option.disabled = !item.available; return option;}));
  replaceOptions($('refine-format'), ...formats.map(format => new Option(msg(format.label), format.value)));
  replaceOptions($('refine-resolution'), ...config.presets.map(([width, height]) => new Option(`${width} × ${height}`, `${width}x${height}`)), new Option(msg('Custom size'), 'custom'));
  if (!changingLocale) sizeChanged();
  adapterChanged(!changingLocale);
}
export function initializeRefine(options) {
  api = options;
  painter = new GreenPainter();
  zoomer = new ZoomSelector(controls);
  $('refine-adapter').onchange = () => adapterChanged();
  $('refine-gallery').onclick = () => api.switchView('gallery');
  $('refine-upload-button').onclick = () => $('refine-upload').click();
  $('refine-add-button').onclick = () => $('refine-add-reference').click();
  $('refine-upload').onchange = async () => {
    await uploadFiles([...$('refine-upload').files]);$('refine-upload').value='';
  };
  $('refine-add-reference').onchange=async()=>{await uploadFiles([...$('refine-add-reference').files],true);$('refine-add-reference').value='';};
  $('refine-clear').onclick = async () => {
    await discardAdditional();
    if (reference) await api.request(`/api/references/${reference.id}`, {method: 'DELETE'}).catch(() => {});
    reference = null; painter.clear(); zoomer.clear(); clearResult(); $('refine-source-image').hidden = true; $('refine-source-image').removeAttribute('src'); $('refine-source-empty').hidden = false;
    $('refine-reference-meta').textContent = msg('Upload a picture or send a generated result here.'); adapterChanged();
  };
  $('refine-resolution').onchange = () => {if ($('refine-resolution').value !== 'custom') {const [width, height] = $('refine-resolution').value.split('x'); $('refine-width').value = width; $('refine-height').value = height; sizeChanged();}};
  $('refine-width').oninput = sizeChanged; $('refine-height').oninput = sizeChanged;
  $('refine-random-seed').onchange = () => $('refine-seed').readOnly = $('refine-random-seed').checked;
  $('refine-format').onchange = download;
  $('refine-use-result').onclick = () => {if (result) refineFromJob(result);};
  $('refine-cancel').onclick = async () => {try {active = await api.request(`/api/jobs/${active.id}/cancel`, {method: 'POST'}); controls();} catch (error) {note(error.message);}};
  $('refine-form').onsubmit = async event => {
    event.preventDefault(); if (!reference || refineRunning()) return; note();
    if (zoom() && !zoomer.hasSelection()) return note(msg('Select a red zoom area first.'));
    if ($('refine-random-seed').checked) $('refine-seed').value = api.randomSeed();
    const payload = {prompt: $('refine-prompt').value, reference_id: reference.id, save_gallery: $('refine-save-gallery').checked,
      ...Object.fromEntries(['width', 'height', 'seed', 'steps'].map(key => [key, Number($(`refine-${key}`).value)]))};
    if(additional.length){delete payload.reference_id;payload.reference_ids=[reference.id,...additional.map(item=>item.id)];}
    if (!Number.isSafeInteger(payload.seed)) return note(msg('Choose a whole-number seed within the displayed range.'));
    uploading = true; controls(); let painted = null;
    try {
      if (outpaint()) {
        const body = new FormData(); body.append('image',await painter.blob(),'green-reference.png'); body.append('preserve_green','true');
        painted = await api.request('/api/references',{method:'POST',body}); payload.reference_id = painted.id; payload.adapter = 'outpaint';
      }
      if (zoom()) {
        payload.adapter = 'zoom';
        if (!zoomer.locked) {
          painted = await api.request('/api/references',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reference_id:reference.id,zoom_region:zoomer.region})});
          payload.reference_id = painted.id;
        }
      }
      active = await api.request('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    } catch (error) {note(error.message);}
    finally {if (painted) await api.request(`/api/references/${painted.id}`,{method:'DELETE'}).catch(() => {}); uploading = false; controls();}
  };
  window.addEventListener('ui-locale-changed', () => {
    if (!api.config) return; configureRefine(api.config,true); controls(); painter.describe(); zoomer.preview();
    if (result) $('refine-result-meta').textContent=msg`${result.settings.width} × ${result.settings.height} · Seed ${result.settings.seed} · ${result.seconds.toFixed(2)} s generation`;
    note(retranslate($('refine-message').textContent));
  });
  setInterval(poll, 700);
  setInterval(() => {if (refineRunning()) $('refine-elapsed').textContent = msg`${Math.max(0, Math.round(Date.now() / 1000 - active.created))} seconds elapsed`;}, 1000);
  document.addEventListener('visibilitychange', () => {if (!document.hidden) poll();});
}

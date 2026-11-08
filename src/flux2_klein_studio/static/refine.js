import {runtimeState} from './shell.js';
import {GreenPainter} from './outpaint.js';
import {ZoomSelector} from './zoom.js';
const $ = (id) => document.getElementById(id);
const terminal = new Set(['completed', 'failed', 'cancelled']);
let api, reference = null, active = null, result = null, uploading = false, polling = false, recipeURL = null;
let formats = [];
let painter, zoomer;
const outpaint = () => $('refine-adapter').value === 'outpaint';
const zoom = () => $('refine-adapter').value === 'zoom';
function adapterChanged() {
  const profile = api.config?.edit_adapters.find(item => item.id === $('refine-adapter').value);
  $('refine-adapter-help').textContent = profile ? profile.help + (profile.unavailable_reason ? ' ' + profile.unavailable_reason : '') : 'Use the base model for instruction-based editing.';
  $('outpaint-instructions').hidden = !profile;
  $('paint-tools').hidden = !outpaint();
  $('paint-canvas').hidden = !outpaint() || !reference;
  $('zoom-tools').hidden = !zoom();
  $('zoom-canvas').hidden = !zoom() || !reference;
  $('refine-source-image').hidden = Boolean(profile) || !reference;
  if (profile) {
    $('outpaint-default-prompt').textContent = profile.default_prompt;
    const text = $('refine-prompt').value.trim();
    if (text.startsWith(profile.default_prompt)) $('refine-prompt').value = text.slice(profile.default_prompt.length).replace(/^[.\s]+/,'');
  }
  $('edit-instruction-title').textContent = zoom() ? 'Fixed zoom instruction · always included' : 'Fixed fill instruction · always included';
  $('edit-instruction-help').textContent = zoom() ? 'Draw a rectangle on the reference. Extra focus details below are optional.' : 'Add an optional description below, or leave it empty for a natural fill.';
  $('refine-prompt-label').textContent = zoom() ? 'Optional: what should the zoom focus on?' : profile ? 'Optional: what should fill the green space?' : 'What should change?';
  $('refine-prompt').required = !profile;
  $('refine-prompt').placeholder = zoom() ? 'For example: focus on the robot’s face. Leave empty to use only the rectangle.' : profile ? 'For example: a brick wall matching the lighting. Leave empty for a natural fill.' : 'Keep the subject and composition, but change the lighting to warm sunset.';
  $('refine-use-result').textContent = zoom() ? '⌕ Zoom again from this result' : '✎ Use this result';
  $('refine-submit').textContent = zoom() ? '⌕ Zoom into selection' : '✎ Refine picture';
  painter?.resize(Number($('refine-width').value),Number($('refine-height').value)); controls();
}
export function refineRunning() {return active && !terminal.has(active.status);}
function note(text = '') {$('refine-message').textContent = text;}
function controls() {
  const running = refineRunning();
  $('refine-submit').disabled = !reference || running || uploading || (zoom() && !zoomer?.hasSelection());
  $('refine-upload').disabled = Boolean(running || uploading);
  $('refine-clear').disabled = !reference || running || uploading;
  $('refine-use-result').disabled = Boolean(running || uploading);
  for (const id of ['refine-adapter','refine-resolution','refine-width','refine-height']) $(id).disabled = Boolean(running || uploading);
  painter?.setDisabled(running || uploading || !reference || !outpaint());
  zoomer?.setDisabled(running || uploading || !reference || !zoom());
  $('refine-cancel').hidden = !running;
  $('refine-cancel').disabled = active?.status === 'cancelling';
  $('refine-busy').hidden = !running;
  $('refine-state').textContent = uploading ? 'Preparing reference' : running ? active.status : result ? 'Completed' : reference ? 'Ready to refine' : 'Choose a picture';
  $('refine-busy-title').textContent = active?.status === 'queued' ? `Waiting · Queue position ${active.queue_position}` : active?.status === 'loading' ? 'Loading your local model…' : active?.status === 'processing' ? 'Preparing your picture…' : active?.status === 'cancelling' ? 'Stopping refinement…' : 'Refining your picture…';
  if (running && !$('refine-panel').hidden) runtimeState('busy', active.status === 'queued' ? 'Refinement queued' : 'Refining image');
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
  const old = reference; reference = next; clearResult();
  $('refine-source-image').src = next.image; $('refine-source-image').hidden = false;
  $('refine-source-empty').hidden = true;
  $('refine-reference-meta').textContent = `${next.original_width} × ${next.original_height} source · ${next.width} × ${next.height} prepared reference. Temporary until saved with a refinement.`;
  const [width, height] = dimensions || [next.width, next.height];
  const scale = Math.min(1, 1920 / Math.max(width, height));
  $('refine-width').value = Math.max(64, Math.floor(width * scale / 16) * 16);
  $('refine-height').value = Math.max(64, Math.floor(height * scale / 16) * 16);
  await painter.setSource(next.image,Number($('refine-width').value),Number($('refine-height').value));
  zoomer.setSource(painter.source,markedZoom,Number($('refine-width').value),Number($('refine-height').value));
  $('refine-prompt').value = ''; sizeChanged(); adapterChanged();
  if (old && old.id !== next.id) await api.request(`/api/references/${old.id}`, {method: 'DELETE'}).catch(() => {});
}
export async function refineFromJob(item) {
  api.switchView('refine');
  if (refineRunning() || uploading) return note('Finish or cancel the current refinement before choosing another picture.');
  uploading = true; note(); controls();
  try {
    const next = await api.request('/api/references', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({source_job_id: item.id, ...(outpaint() ? {preserve_green:true} : {})})});
    await choose(next, [item.settings.width, item.settings.height]);
    $('refine-prompt').focus();
  } catch (error) {note(error.message);} finally {uploading = false; controls();}
}
export async function reviewRefinement(item) {
  api.switchView('refine');
  if (refineRunning() || uploading) return note('Finish or cancel the current refinement before opening another recipe.');
  uploading = true; note(); controls();
  try {
    const response = await fetch(`/api/jobs/${item.id}/reference`, {cache: 'no-store'});
    if (!response.ok) throw new Error('The saved reference is unavailable. Choose a new reference to continue.');
    const body = new FormData(); body.append('image', await response.blob(), 'reference.png');
    $('refine-adapter').value = item.settings.adapter || '';
    if (outpaint()) body.append('preserve_green','true');
    await choose(await api.request('/api/references', {method: 'POST', body}), [item.settings.width, item.settings.height], zoom() ? item.settings.reference?.zoom_region || true : false);
    $('refine-prompt').value = item.settings.prompt;
    adapterChanged();
    $('refine-seed').value = item.settings.seed; $('refine-steps').value = item.settings.steps;
    $('refine-random-seed').checked = false; $('refine-seed').readOnly = false;
    active = null; showResult(item);
  } catch (error) {note(error.message);} finally {uploading = false; controls();}
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
  $('refine-result-meta').textContent = `${item.settings.width} × ${item.settings.height} · Seed ${item.settings.seed} · ${item.seconds.toFixed(2)} s generation`;
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
    else if (active.status === 'cancelled') note('Refinement cancelled. Your reference is still available.');
    controls();
  } catch (error) {note(error.message);} finally {polling = false;}
}
export function configureRefine(config) {
  api.config = config; formats = config.download_formats;
  $('refine-adapter').replaceChildren(new Option('None · Base model',''), ...config.edit_adapters.map(item => {const option = new Option(item.label + (item.available ? '' : ' · unavailable offline'),item.id); option.disabled = !item.available; return option;}));
  $('refine-format').replaceChildren(...formats.map(format => new Option(format.label, format.value)));
  $('refine-resolution').replaceChildren(...config.presets.map(([width, height]) => new Option(`${width} × ${height}`, `${width}x${height}`)), new Option('Custom size', 'custom'));
  sizeChanged(); adapterChanged();
}
export function initializeRefine(options) {
  api = options;
  painter = new GreenPainter();
  zoomer = new ZoomSelector(controls);
  $('refine-adapter').onchange = adapterChanged;
  $('refine-gallery').onclick = () => api.switchView('gallery');
  $('refine-upload').onchange = async () => {
    const file = $('refine-upload').files[0]; if (!file) return;
    if (file.size > 16 * 1024 * 1024) return note('Choose an image of at most 16 MiB.');
    uploading = true; note(); controls();
    try {const data = new FormData(); data.append('image', file); if (outpaint()) data.append('preserve_green','true'); await choose(await api.request('/api/references', {method: 'POST', body: data}));}
    catch (error) {note(error.message);} finally {uploading = false; $('refine-upload').value = ''; controls();}
  };
  $('refine-clear').onclick = async () => {
    if (reference) await api.request(`/api/references/${reference.id}`, {method: 'DELETE'}).catch(() => {});
    reference = null; painter.clear(); zoomer.clear(); clearResult(); $('refine-source-image').hidden = true; $('refine-source-image').removeAttribute('src'); $('refine-source-empty').hidden = false;
    $('refine-reference-meta').textContent = 'Upload a picture or send a generated result here.'; adapterChanged();
  };
  $('refine-resolution').onchange = () => {if ($('refine-resolution').value !== 'custom') {const [width, height] = $('refine-resolution').value.split('x'); $('refine-width').value = width; $('refine-height').value = height; sizeChanged();}};
  $('refine-width').oninput = sizeChanged; $('refine-height').oninput = sizeChanged;
  $('refine-random-seed').onchange = () => $('refine-seed').readOnly = $('refine-random-seed').checked;
  $('refine-format').onchange = download;
  $('refine-use-result').onclick = () => {if (result) refineFromJob(result);};
  $('refine-cancel').onclick = async () => {try {active = await api.request(`/api/jobs/${active.id}/cancel`, {method: 'POST'}); controls();} catch (error) {note(error.message);}};
  $('refine-form').onsubmit = async event => {
    event.preventDefault(); if (!reference || refineRunning()) return; note();
    if (zoom() && !zoomer.hasSelection()) return note('Select a red zoom area first.');
    if ($('refine-random-seed').checked) $('refine-seed').value = api.randomSeed();
    const payload = {prompt: $('refine-prompt').value, reference_id: reference.id, save_gallery: $('refine-save-gallery').checked,
      ...Object.fromEntries(['width', 'height', 'seed', 'steps'].map(key => [key, Number($(`refine-${key}`).value)]))};
    if (!Number.isSafeInteger(payload.seed)) return note('Choose a whole-number seed within the displayed range.');
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
  setInterval(poll, 700);
  setInterval(() => {if (refineRunning()) $('refine-elapsed').textContent = `${Math.max(0, Math.round(Date.now() / 1000 - active.created))} seconds elapsed`;}, 1000);
  document.addEventListener('visibilitychange', () => {if (!document.hidden) poll();});
}

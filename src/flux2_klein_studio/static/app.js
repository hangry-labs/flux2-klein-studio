import {msg, retranslate, initializeI18n, replaceOptions} from './i18n.js';
import {loadResolutionSettings} from './resolutions.js';
import {initializeRefine, configureRefine, refineFromJob, reviewRefinement, refineRunning} from './refine.js';
import {initializeShell, runtimeState} from './shell.js';
import {initializeGPU} from './gpu-monitor.js';
await initializeI18n();
const $ = (id) => document.getElementById(id);
const examples = [
  'A glass robot tending orchids in a sunlit greenhouse, cinematic lighting, highly detailed',
  'A tiny lighthouse on a floating island above a sea of clouds, warm sunset, intricate illustration',
  'A small orange sailboat on a calm mountain lake at sunrise, realistic photograph',
  'A quiet bookshop on a rainy evening, warm light spilling onto cobblestones, cinematic photograph',
];
const terminal = new Set(['completed', 'failed', 'cancelled']);
let view = 'generate', active = null, selected = null, config = null, polling = false, submitting = false;
const storage = {get(key, fallback) {try {return sessionStorage.getItem(`klein-${key}`) || fallback;} catch {return fallback;}}, set(key, value) {try {sessionStorage.setItem(`klein-${key}`, value);} catch { /* Optional browser storage. */ }}};
let noticeMessage = {source:'',values:[]};
function message(source = '', ...values) {noticeMessage={source,values}; renderNotice();}
function renderNotice() {
  const {source,values}=noticeMessage;
  const text=Array.isArray(source) ? msg(source,...values.map(value=>typeof value==='string'?retranslate(value):value)) : retranslate(source);
  $('notice').textContent=text; $('notice').hidden=!text;
}
async function request(path, options = {}) {
  const response = await fetch(path, {cache: 'no-store', ...options});
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? msg(data.detail) : msg('Please check the prompt, dimensions, seed and steps.'));
    error.status = response.status; throw error;
  }
  return data;
}
function switchView(next) {
  view = next; storage.set('view', next);
  document.querySelectorAll('[data-view]').forEach((button) => {const current = button.dataset.view === view; button.setAttribute('aria-selected', String(current)); button.tabIndex = current ? 0 : -1;});
  ['generate', 'refine', 'gallery', 'system'].forEach((name) => $(`${name}-panel`).hidden = name !== view);
  if (view === 'gallery') refreshGallery().catch((error) => message(error.message));
  gpuMonitor.setVisible(view === 'system');
  if (view === 'system') loadResolutionSettings();
}
const gpuMonitor = initializeGPU({request, storage});
document.querySelectorAll('[data-view]').forEach((button) => {
  button.onclick = () => switchView(button.dataset.view);
  button.onkeydown = (event) => {if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {event.preventDefault(); const tabs = [...document.querySelectorAll('[data-view]')]; const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (tabs.indexOf(button) + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; tabs[index].focus(); tabs[index].click(); tabs[index].scrollIntoView({block: 'nearest', inline: 'nearest'});}};
});
initializeShell(storage);
function idea() {$('prompt').value = examples[Math.floor(Math.random() * examples.length)]; updatePrompt(); switchView('generate'); $('prompt').focus();}
['examples', 'sample', 'empty-example'].forEach((id) => $(id).onclick = idea);
function updatePrompt() {$('characters').textContent = `${$('prompt').value.length} / 4000`;}
function adapterChanged() {
  const profile = config?.adapters.find(item => item.id === $('adapter').value);
  $('adapter-template').hidden = !profile;
  $('adapter-help').textContent = profile ? msg(profile.help) + (profile.unavailable_reason ? ' ' + msg(profile.unavailable_reason) : '') : msg('Choose a style, then describe your image below.');
  $('adapter-prefix').textContent = profile?.prefix || '';
  $('adapter-suffix').textContent = profile?.suffix || '';
}
$('adapter').onchange = adapterChanged;
$('prompt').oninput = updatePrompt;
function randomSeed() {
  const words = crypto.getRandomValues(new Uint32Array(2));
  return (words[0] & 0x1fffff) * 2 ** 32 + words[1];
}
function seedMode() {
  $('seed').readOnly = $('random-seed').checked;
  $('seed-help').textContent = $('random-seed').checked ? msg('A fresh seed is chosen each time you generate.') : msg('Fixed seed: reuse these settings to repeat a picture.');
}
$('random-seed').onchange = seedMode;
$('seed').value = randomSeed(); seedMode();
$('resolution').onchange = () => {const [width, height] = $('resolution').value.split('x'); if (width && height) {$('width').value = width; $('height').value = height;} sizeHelp();};
function sizeHelp() {
  const width = Number($('width').value), height = Number($('height').value);
  const preset = `${width}x${height}`; $('resolution').value = [...$('resolution').options].some((o) => o.value === preset) ? preset : 'custom';
  $('size-help').textContent = width * height > 1792 ** 2 ? msg('This size is near the tested memory limit. Other GPU workloads may prevent it from fitting.') : msg('Dimensions use multiples of 16. Larger canvases need more GPU memory.');
}
$('width').oninput = sizeHelp; $('height').oninput = sizeHelp;
function busy(job, updateRuntime = true) {
  active = job; const running = job && !terminal.has(job.status);
  $('generate').disabled = !config || running || submitting; $('cancel').hidden = !running;
  $('busy').hidden = !running; $('empty').hidden = Boolean(selected) || running;
  if (updateRuntime) runtimeState(running ? 'busy' : 'ready', running ? (job.status === 'queued' ? msg`Queued · Position ${job.queue_position}` : job.status === 'processing' ? msg('Preparing image download') : job.status === 'loading' ? msg('Loading model') : msg('Generating image')) : msg('Ready for an idea'));
  $('result-state').textContent = running ? msg(job.status) : selected ? msg('Completed') : msg('Ready to create');
  $('busy-title').textContent = job?.status === 'cancelling' ? msg('Stopping generation…') : job?.status === 'queued' ? msg`Waiting in queue · Position ${job.queue_position}` : job?.status === 'processing' ? msg('Preparing your image…') : job?.status === 'loading' ? msg('Loading your local model…') : msg('Creating your image…');
  $('cancel').disabled = job?.status === 'cancelling';
}
function showResult(item) {
  selected = item; $('result-image').src = item.image; $('result-image').alt = item.settings.prompt;
  $('result-image').hidden = false; $('empty').hidden = true; $('result-actions').hidden = false;
  $('result-title').textContent = msg('A little imagination, made real.');
  resultMetadata(item);
  $('result-prompt').textContent = item.settings.effective_prompt || item.settings.prompt;
  updateDownload();
  $('result-state').textContent = msg('Completed');
}
function resultMetadata(item) {
  $('result-meta').textContent = msg`${item.settings.width} × ${item.settings.height} · Seed ${item.settings.seed} · ${item.seconds.toFixed(2)} s generation`;
}
function updateDownload() {
  if (!selected || !config) return;
  const format = config.download_formats.find(item => item.value === $('download-format').value);
  if (!format) return;
  $('download').href = `/api/jobs/${selected.id}/download?format=${format.value}`;
  $('download').download = `flux2-${selected.settings.seed}.${format.extension}`;
}
$('download-format').onchange = updateDownload;
$('recipe').onclick = () => {
  if (!selected) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify({model: config.model, revision: config.revision, ...selected.settings, reduction: 'ordered'}, null, 2)], {type: 'application/json'}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = `klein-${selected.settings.seed}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
$('generation-form').onsubmit = async (event) => {
  event.preventDefault();
  if (submitting || (active && !terminal.has(active.status))) return;
  message();
  if ($('random-seed').checked) $('seed').value = randomSeed();
  const payload = {prompt: $('prompt').value, save_gallery: $('save_gallery').checked, ...Object.fromEntries(['width', 'height', 'seed', 'steps'].map((id) => [id, Number($(id).value)]))};
  if ($('adapter').value) payload.adapter = $('adapter').value;
  if (!Number.isSafeInteger(payload.seed)) return message(msg('Choose a whole-number seed within the displayed range.'));
  submitting = true; $('generate').disabled = true;
  try {busy(await request('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)}));}
  catch (error) {message(error.message);}
  finally {submitting = false; busy(active);}
};
initializeRefine({request, switchView, refreshGallery, randomSeed});
$('send-refine').onclick = () => {if (selected) refineFromJob(selected);};
$('cancel').onclick = async () => {try {if (active) busy(await request(`/api/jobs/${active.id}/cancel`, {method: 'POST'}));} catch (error) {message(error.message);}};
let galleryItems = [], marked = new Set(), pendingDelete = [], deleting = false;
function selectionCount() { $('delete-selected').disabled = deleting || !marked.size; $('delete-selected').textContent = msg`Delete selected (${marked.size})`; }
function clearResult() {
  selected = null; $('result-image').removeAttribute('src'); $('result-image').hidden = true;
  $('result-actions').hidden = true; $('download').removeAttribute('href');
  $('result-title').textContent = msg('Room for an idea'); busy(active);
}
async function refreshGallery() {
  galleryItems = await request('/api/jobs'); renderGallery(); return galleryItems;
}
function renderGallery() {
  const items = galleryItems;
  const valid = new Set(items.map(item => item.id)); marked = new Set([...marked].filter(id => valid.has(id)));
  if (selected && selected.settings.save_gallery !== false && !valid.has(selected.id)) clearResult();
  $('gallery-count').textContent = items.filter(item => item.status === 'completed').length;
  $('gallery').replaceChildren();
  if (!items.length) {const empty = document.createElement('p'); empty.className = 'muted'; empty.textContent = msg('Save a generation to gallery to keep it here.'); $('gallery').append(empty);}
  items.forEach(item => {
    const card = document.createElement('article'); card.className = 'gallery-card';
    const preview = document.createElement('button'); preview.className = 'gallery-preview'; preview.type = 'button';
    if (item.status === 'completed') {
      const image = document.createElement('img'); image.src = item.image; image.alt = item.settings.prompt; image.loading = 'lazy'; preview.append(image);
    } else {const state = document.createElement('p'); state.textContent = msg(item.status); preview.append(state); preview.disabled = true;}
    const info = document.createElement('div'), title = document.createElement('strong'), prompt = document.createElement('p');
    title.textContent = `${item.settings.width} × ${item.settings.height} · ${item.status === 'completed' ? item.seconds.toFixed(2) + ' s' : msg(item.status)}`;
    prompt.textContent = item.settings.prompt || item.settings.effective_prompt || ''; info.append(title, prompt); preview.append(info);
    preview.onclick = () => {if (item.settings.reference) {reviewRefinement(item); return;} Object.entries(item.settings).forEach(([key, value]) => {if ($(key) && key !== 'save_gallery') $(key).value = value;}); $('adapter').value = item.settings.adapter || ''; adapterChanged(); $('random-seed').checked = false; seedMode(); updatePrompt(); sizeHelp(); showResult(item); switchView('generate');};
    card.append(preview);
    const controls = document.createElement('div'); controls.className = 'gallery-controls';
    if (terminal.has(item.status)) {
      const label = document.createElement('label'), check = document.createElement('input'); check.type = 'checkbox'; check.checked = marked.has(item.id);
      check.setAttribute('aria-label', msg`Select saved job ${item.id.slice(0, 8)}`);
      check.onchange = () => {if (check.checked) marked.add(item.id); else marked.delete(item.id); selectionCount();};
      label.append(check, document.createTextNode(msg(' Select')));
      const remove = document.createElement('button'); remove.textContent = msg('Delete'); remove.className = 'danger'; remove.onclick = () => confirmDelete([item.id]);
      controls.append(label);
      if (item.status === 'completed') {const refine = document.createElement('button'); refine.textContent = msg('Refine'); refine.className = 'secondary'; refine.onclick = () => refineFromJob(item); controls.append(refine);}
      controls.append(remove);
    }
    card.append(controls); $('gallery').append(card);
  });
  selectionCount(); return items;
}
function confirmDelete(ids) {
  pendingDelete = [...ids]; $('delete-description').textContent = msg`Saved jobs to delete: ${ids.length}. This removes all files in each job folder.`;
  $('delete-dialog').showModal();
}
$('select-gallery').onclick = () => {const ids = galleryItems.filter(item => terminal.has(item.status)).map(item => item.id); marked = ids.every(id => marked.has(id)) ? new Set() : new Set(ids); refreshGallery().catch(error => message(error.message));};
$('delete-selected').onclick = () => confirmDelete([...marked]);
$('delete-cancel').onclick = () => $('delete-dialog').close();
$('delete-dialog').addEventListener('cancel', event => {if (deleting) event.preventDefault();});
$('delete-confirm').onclick = async () => {
  deleting = true; $('delete-confirm').disabled = $('delete-cancel').disabled = true;
  let count = 0, failure = '';
  for (const id of pendingDelete) {
    try {
      await request(`/api/jobs/${id}`, {method:'DELETE'}); count++; marked.delete(id);
      if (active?.id === id) active = null;
      if (selected?.id === id) clearResult();
    } catch(error) {failure = error.message; break;}
  }
  deleting = false; $('delete-confirm').disabled = $('delete-cancel').disabled = false; $('delete-dialog').close();
  if (failure) message`${count} removed. ${failure}`; else message`Saved jobs permanently deleted: ${count}.`;
  await refreshGallery().catch(error => {message`${count} removed. Refresh the gallery to reconnect: ${error.message}`;});
};
$('refresh-gallery').onclick = () => refreshGallery().catch((error) => message(error.message));
async function pollJob() {
  if (!active || terminal.has(active.status) || polling || document.hidden) return;
  polling = true;
  try {const job = await request(`/api/jobs/${active.id}`); busy(job); if (job.status === 'completed') {showResult(job); await refreshGallery();} else if (job.status === 'failed') message(job.error); else if (job.status === 'cancelled') message(msg('Generation cancelled. Your previous images are still available.'));}
  catch (error) {
    if (error.status === 404) {busy(null); message(msg('This job is no longer available. Choose a saved image or generate another.'));}
    else message`Connection interrupted. Your job may still be running. ${error.message}`;
  }
  finally {polling = false;}
}
setInterval(pollJob, 900);
setInterval(() => {if (active && !terminal.has(active.status)) $('elapsed').textContent = msg`${Math.max(0, Math.round(Date.now() / 1000 - active.created))} seconds elapsed`;}, 1000);
document.addEventListener('visibilitychange', () => {if (!document.hidden) pollJob();});
function configureGeneration() {
    replaceOptions($('adapter'), new Option(msg('None · Base model'), ''), ...config.adapters.map(item => {const option = new Option(msg(item.label) + (item.available ? '' : msg(' · unavailable offline')), item.id); option.disabled = !item.available; return option;}));
    adapterChanged();
    replaceOptions($('download-format'), ...config.download_formats.map(format => new Option(msg(format.label), format.value)));
    replaceOptions($('resolution'), ...config.presets.map(([width, height]) => new Option(`${width} × ${height}${width === height ? msg(' · Square') : width > height ? msg(' · Landscape') : msg(' · Portrait')}`, `${width}x${height}`)), new Option(msg('Custom size'), 'custom'));
}
async function start() {
  $('generate').disabled = true;
  try {
    config = await request('/api/status'); configureRefine(config); $('version').textContent = `v${config.version}`;
    configureGeneration();
    const items = await refreshGallery(); const job = config.active_job ? await request(`/api/jobs/${config.active_job}`) : null;
    const last = items.find((item) => item.status === 'completed'); if (last) showResult(last);
    busy(job || null); sizeHelp(); switchView(['generate', 'refine', 'gallery', 'system'].includes(storage.get('view', 'generate')) ? storage.get('view', 'generate') : 'generate');
  } catch (error) {message`Studio connection failed: ${error.message}. Reload to reconnect.`; runtimeState('error', msg('Disconnected'));}
}
start();

async function residencyStatus() {
  if (document.hidden || !config) return;
  try {
    const status = await request('/api/status'); const model = status.model_residency;
    $('model-state').textContent = model.state === 'ready' ? (model.keep_loaded ? msg('Weights loaded. Kept ready by the residency policy.') : msg`Weights loaded. Unload in ${model.unload_in_seconds} seconds if idle.`) : msg`Model ${msg(model.state)}.`;
    if ((!active || terminal.has(active.status)) && !refineRunning()) {
      if (status.queue.outstanding) runtimeState('busy', status.active_job ? msg('Generating image') : status.queue.processing ? msg('Preparing image download') : msg('Images queued'));
      else runtimeState(model.state === 'error' ? 'error' : ['loading', 'unloading'].includes(model.state) ? 'busy' : 'ready', model.state === 'ready' ? msg('Model loaded · Ready') : model.state === 'unloaded' ? msg('Ready to load') : msg`Model ${msg(model.state)}`);
    }
    $('unload-model').disabled = Boolean(status.active_job) || model.state === 'unloaded';
    $('trim-workspace').disabled = Boolean(status.active_job) || model.state === 'unloaded';
  } catch { /* Keep prior state during reconnects. */ }
}
$('save-residency').onclick = async () => {
  try {const idle_seconds = Number($('idle-seconds').value); if (!Number.isInteger(idle_seconds) || idle_seconds < 0 || idle_seconds > 86400) throw new Error(msg('Choose 0–86400 whole seconds.'));
    const residency = await request('/api/settings');
    await request('/api/settings', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...residency, idle_seconds})}); $('settings-message').textContent = msg('Idle timeout saved.'); await residencyStatus();
  } catch(error) {$('settings-message').textContent = error.message;}
};
$('unload-model').onclick = async () => {try {await request('/api/model/unload', {method:'POST'}); await residencyStatus();} catch(error) {message(error.message);}};
$('trim-workspace').onclick = async () => {try {const model = await request('/api/model/trim', {method:'POST'}); $('settings-message').textContent = model.state === 'unloaded' ? msg('The model is already unloaded.') : msg('Unused GPU memory released. Model weights remain loaded.'); await residencyStatus();} catch(error) {$('settings-message').textContent = error.message;}};
request('/api/settings').then(settings => {$('idle-seconds').value = settings.idle_seconds;}).catch(error => message(error.message));
setInterval(residencyStatus, 2000);

window.addEventListener('ui-locale-changed', () => {
  if (!config) return; configureGeneration(); seedMode(); sizeHelp(); busy(active, !refineRunning());
  if (selected) {resultMetadata(selected); $('result-title').textContent=msg('A little imagination, made real.'); $('result-image').alt=selected.settings.prompt;}
  renderGallery(); if (pendingDelete.length) $('delete-description').textContent=msg`Saved jobs to delete: ${pendingDelete.length}. This removes all files in each job folder.`;
  renderNotice(); $('settings-message').textContent=retranslate($('settings-message').textContent);
  residencyStatus();
});
window.addEventListener('ui-locale-error', event=>message(event.detail));

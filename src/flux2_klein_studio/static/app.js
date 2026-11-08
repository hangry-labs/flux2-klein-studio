import {loadResolutionSettings} from './resolutions.js';
import {initializeRefine, configureRefine, refineFromJob, reviewRefinement, refineRunning} from './refine.js';
import {initializeShell, runtimeState} from './shell.js';
import {initializeGPU} from './gpu-monitor.js';
const $ = (id) => document.getElementById(id);
const examples = [
  'A glass robot tending orchids in a sunlit greenhouse, cinematic lighting, highly detailed',
  'A tiny lighthouse on a floating island above a sea of clouds, warm sunset, intricate illustration',
  'A small orange sailboat on a calm mountain lake at sunrise, realistic photograph',
  'A quiet bookshop on a rainy evening, warm light spilling onto cobblestones, cinematic photograph',
];
const terminal = new Set(['completed', 'failed', 'cancelled']);
let view = 'generate', active = null, selected = null, config = null, polling = false;
const storage = {get(key, fallback) {try {return sessionStorage.getItem(`klein-${key}`) || fallback;} catch {return fallback;}}, set(key, value) {try {sessionStorage.setItem(`klein-${key}`, value);} catch { /* Optional browser storage. */ }}};
function message(text = '') {$('notice').textContent = text; $('notice').hidden = !text;}
async function request(path, options = {}) {
  const response = await fetch(path, {cache: 'no-store', ...options});
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : 'Please check the prompt, dimensions, seed and steps.');
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
  $('adapter-help').textContent = profile ? profile.help + (profile.unavailable_reason ? ' ' + profile.unavailable_reason : '') : 'Choose a style, then describe your image below.';
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
  $('seed-help').textContent = $('random-seed').checked ? 'A fresh seed is chosen each time you generate.' : 'Fixed seed: reuse these settings to repeat a picture.';
}
$('random-seed').onchange = seedMode;
$('seed').value = randomSeed(); seedMode();
$('resolution').onchange = () => {const [width, height] = $('resolution').value.split('x'); if (width && height) {$('width').value = width; $('height').value = height;} sizeHelp();};
function sizeHelp() {
  const width = Number($('width').value), height = Number($('height').value);
  const preset = `${width}x${height}`; $('resolution').value = [...$('resolution').options].some((o) => o.value === preset) ? preset : 'custom';
  $('size-help').textContent = width * height > 1792 ** 2 ? 'This size is near the tested memory limit. Other GPU workloads may prevent it from fitting.' : 'Dimensions use multiples of 16. Larger canvases need more GPU memory.';
}
$('width').oninput = sizeHelp; $('height').oninput = sizeHelp;
function busy(job) {
  active = job; const running = job && !terminal.has(job.status);
  $('generate').disabled = !config || running; $('cancel').hidden = !running;
  $('busy').hidden = !running; $('empty').hidden = Boolean(selected) || running;
  runtimeState(running ? 'busy' : 'ready', running ? (job.status === 'queued' ? `Queued · Position ${job.queue_position}` : job.status === 'processing' ? 'Preparing image download' : job.status === 'loading' ? 'Loading model' : 'Generating image') : 'Ready for an idea');
  $('result-state').textContent = running ? job.status : selected ? 'Completed' : 'Ready to create';
  $('busy-title').textContent = job?.status === 'cancelling' ? 'Stopping generation…' : job?.status === 'queued' ? `Waiting in queue · Position ${job.queue_position}` : job?.status === 'processing' ? 'Preparing your image…' : job?.status === 'loading' ? 'Loading your local model…' : 'Creating your image…';
  $('cancel').disabled = job?.status === 'cancelling';
}
function showResult(item) {
  selected = item; $('result-image').src = item.image; $('result-image').alt = item.settings.prompt;
  $('result-image').hidden = false; $('empty').hidden = true; $('result-actions').hidden = false;
  $('result-title').textContent = 'A little imagination, made real.';
  $('result-meta').textContent = `${item.settings.width} × ${item.settings.height} · Seed ${item.settings.seed} · ${item.seconds.toFixed(2)} s generation`;
  $('result-prompt').textContent = item.settings.effective_prompt || item.settings.prompt;
  updateDownload();
  $('result-state').textContent = 'Completed';
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
  event.preventDefault(); message();
  if ($('random-seed').checked) $('seed').value = randomSeed();
  const payload = {prompt: $('prompt').value, save_gallery: $('save_gallery').checked, ...Object.fromEntries(['width', 'height', 'seed', 'steps'].map((id) => [id, Number($(id).value)]))};
  if ($('adapter').value) payload.adapter = $('adapter').value;
  if (!Number.isSafeInteger(payload.seed)) return message('Choose a whole-number seed within the displayed range.');
  $('generate').disabled = true;
  try {busy(await request('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)}));}
  catch (error) {message(error.message); $('generate').disabled = false;}
};
initializeRefine({request, switchView, refreshGallery, randomSeed});
$('send-refine').onclick = () => {if (selected) refineFromJob(selected);};
$('cancel').onclick = async () => {try {if (active) busy(await request(`/api/jobs/${active.id}/cancel`, {method: 'POST'}));} catch (error) {message(error.message);}};
let galleryItems = [], marked = new Set(), pendingDelete = [], deleting = false;
function selectionCount() { $('delete-selected').disabled = deleting || !marked.size; $('delete-selected').textContent = `Delete selected (${marked.size})`; }
function clearResult() {
  selected = null; $('result-image').removeAttribute('src'); $('result-image').hidden = true;
  $('result-actions').hidden = true; $('download').removeAttribute('href');
  $('result-title').textContent = 'Room for an idea'; busy(active);
}
async function refreshGallery() {
  const items = await request('/api/jobs'); galleryItems = items;
  const valid = new Set(items.map(item => item.id)); marked = new Set([...marked].filter(id => valid.has(id)));
  if (selected && selected.settings.save_gallery !== false && !valid.has(selected.id)) clearResult();
  $('gallery-count').textContent = items.filter(item => item.status === 'completed').length;
  $('gallery').replaceChildren();
  if (!items.length) {const empty = document.createElement('p'); empty.className = 'muted'; empty.textContent = 'Save a generation to gallery to keep it here.'; $('gallery').append(empty);}
  items.forEach(item => {
    const card = document.createElement('article'); card.className = 'gallery-card';
    const preview = document.createElement('button'); preview.className = 'gallery-preview'; preview.type = 'button';
    if (item.status === 'completed') {
      const image = document.createElement('img'); image.src = item.image; image.alt = item.settings.prompt; image.loading = 'lazy'; preview.append(image);
    } else {const state = document.createElement('p'); state.textContent = item.status; preview.append(state); preview.disabled = true;}
    const info = document.createElement('div'), title = document.createElement('strong'), prompt = document.createElement('p');
    title.textContent = `${item.settings.width} × ${item.settings.height} · ${item.status === 'completed' ? item.seconds.toFixed(2) + ' s' : item.status}`;
    prompt.textContent = item.settings.prompt || item.settings.effective_prompt || ''; info.append(title, prompt); preview.append(info);
    preview.onclick = () => {if (item.settings.reference) {reviewRefinement(item); return;} Object.entries(item.settings).forEach(([key, value]) => {if ($(key) && key !== 'save_gallery') $(key).value = value;}); $('adapter').value = item.settings.adapter || ''; adapterChanged(); $('random-seed').checked = false; seedMode(); updatePrompt(); sizeHelp(); showResult(item); switchView('generate');};
    card.append(preview);
    const controls = document.createElement('div'); controls.className = 'gallery-controls';
    if (terminal.has(item.status)) {
      const label = document.createElement('label'), check = document.createElement('input'); check.type = 'checkbox'; check.checked = marked.has(item.id);
      check.setAttribute('aria-label', `Select saved job ${item.id.slice(0, 8)}`);
      check.onchange = () => {if (check.checked) marked.add(item.id); else marked.delete(item.id); selectionCount();};
      label.append(check, document.createTextNode(' Select'));
      const remove = document.createElement('button'); remove.textContent = 'Delete'; remove.className = 'danger'; remove.onclick = () => confirmDelete([item.id]);
      controls.append(label);
      if (item.status === 'completed') {const refine = document.createElement('button'); refine.textContent = 'Refine'; refine.className = 'secondary'; refine.onclick = () => refineFromJob(item); controls.append(refine);}
      controls.append(remove);
    }
    card.append(controls); $('gallery').append(card);
  });
  selectionCount(); return items;
}
function confirmDelete(ids) {
  pendingDelete = [...ids]; $('delete-description').textContent = `${ids.length} saved job${ids.length === 1 ? '' : 's'} will be removed, including all files in each job folder.`;
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
  message(failure ? `${count} removed. ${failure}` : `${count} saved job${count === 1 ? '' : 's'} permanently deleted.`);
  await refreshGallery().catch(error => message(`${count} removed. Refresh the gallery to reconnect: ${error.message}`));
};
$('refresh-gallery').onclick = () => refreshGallery().catch((error) => message(error.message));
async function pollJob() {
  if (!active || terminal.has(active.status) || polling || document.hidden) return;
  polling = true;
  try {const job = await request(`/api/jobs/${active.id}`); busy(job); if (job.status === 'completed') {showResult(job); await refreshGallery();} else if (job.status === 'failed') message(job.error); else if (job.status === 'cancelled') message('Generation cancelled. Your previous images are still available.');}
  catch (error) {
    if (error.status === 404) {busy(null); message('This job is no longer available. Choose a saved image or generate another.');}
    else message(`Connection interrupted. Your job may still be running. ${error.message}`);
  }
  finally {polling = false;}
}
setInterval(pollJob, 900);
setInterval(() => {if (active && !terminal.has(active.status)) $('elapsed').textContent = `${Math.max(0, Math.round(Date.now() / 1000 - active.created))} seconds elapsed`;}, 1000);
document.addEventListener('visibilitychange', () => {if (!document.hidden) pollJob();});
async function start() {
  $('generate').disabled = true;
  try {
    config = await request('/api/status'); configureRefine(config); $('version').textContent = `v${config.version}`;
    $('adapter').replaceChildren(new Option('None · Base model', ''), ...config.adapters.map(item => {const option = new Option(item.label + (item.available ? '' : ' · unavailable offline'), item.id); option.disabled = !item.available; return option;}));
    adapterChanged();
    $('download-format').replaceChildren(...config.download_formats.map(format => new Option(format.label, format.value)));
    $('resolution').replaceChildren(...config.presets.map(([width, height]) => new Option(`${width} × ${height}${width === height ? ' · Square' : width > height ? ' · Landscape' : ' · Portrait'}`, `${width}x${height}`)), new Option('Custom size', 'custom'));
    const items = await refreshGallery(); const job = config.active_job ? await request(`/api/jobs/${config.active_job}`) : null;
    const last = items.find((item) => item.status === 'completed'); if (last) showResult(last);
    busy(job || null); sizeHelp(); switchView(['generate', 'refine', 'gallery', 'system'].includes(storage.get('view', 'generate')) ? storage.get('view', 'generate') : 'generate');
  } catch (error) {message(`Studio connection failed: ${error.message}. Reload to reconnect.`); runtimeState('error', 'Disconnected');}
}
start();

async function residencyStatus() {
  if (document.hidden || !config) return;
  try {
    const status = await request('/api/status'); const model = status.model_residency;
    $('model-state').textContent = model.state === 'ready' ? (model.keep_loaded ? 'Weights loaded. Kept ready by the residency policy.' : `Weights loaded. Unload in ${model.unload_in_seconds} seconds if idle.`) : `Model ${model.state}.`;
    if ((!active || terminal.has(active.status)) && !refineRunning()) {
      if (status.queue.outstanding) runtimeState('busy', status.active_job ? 'Generating image' : status.queue.processing ? 'Preparing image download' : 'Images queued');
      else runtimeState(model.state === 'error' ? 'error' : ['loading', 'unloading'].includes(model.state) ? 'busy' : 'ready', model.state === 'ready' ? 'Model loaded · Ready' : model.state === 'unloaded' ? 'Ready to load' : `Model ${model.state}`);
    }
    $('unload-model').disabled = Boolean(status.active_job) || model.state === 'unloaded';
    $('trim-workspace').disabled = Boolean(status.active_job) || model.state === 'unloaded';
  } catch { /* Keep prior state during reconnects. */ }
}
$('save-residency').onclick = async () => {
  try {const idle_seconds = Number($('idle-seconds').value); if (!Number.isInteger(idle_seconds) || idle_seconds < 0 || idle_seconds > 86400) throw new Error('Choose 0–86400 whole seconds.');
    const residency = await request('/api/settings');
    await request('/api/settings', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...residency, idle_seconds})}); $('settings-message').textContent = 'Idle timeout saved.'; await residencyStatus();
  } catch(error) {$('settings-message').textContent = error.message;}
};
$('unload-model').onclick = async () => {try {await request('/api/model/unload', {method:'POST'}); await residencyStatus();} catch(error) {message(error.message);}};
$('trim-workspace').onclick = async () => {try {const model = await request('/api/model/trim', {method:'POST'}); $('settings-message').textContent = model.state === 'unloaded' ? 'The model is already unloaded.' : 'Unused GPU memory released. Model weights remain loaded.'; await residencyStatus();} catch(error) {$('settings-message').textContent = error.message;}};
request('/api/settings').then(settings => {$('idle-seconds').value = settings.idle_seconds;}).catch(error => message(error.message));
setInterval(residencyStatus, 2000);

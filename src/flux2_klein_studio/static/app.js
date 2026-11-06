const $ = (id) => document.getElementById(id);
const examples = [
  'A glass robot tending orchids in a sunlit greenhouse, cinematic lighting, highly detailed',
  'A tiny lighthouse on a floating island above a sea of clouds, warm sunset, intricate illustration',
  'A small orange sailboat on a calm mountain lake at sunrise, realistic photograph',
  'A quiet bookshop on a rainy evening, warm light spilling onto cobblestones, cinematic photograph',
];
const terminal = new Set(['completed', 'failed', 'cancelled']);
let view = 'generate', active = null, selected = null, config = null, polling = false, gpuFetching = false, gpuHistory = [];
const storage = {get(key, fallback) {try {return sessionStorage.getItem(`klein-${key}`) || fallback;} catch {return fallback;}}, set(key, value) {try {sessionStorage.setItem(`klein-${key}`, value);} catch { /* Optional browser storage. */ }}};
function message(text = '') {$('notice').textContent = text; $('notice').hidden = !text;}
async function request(path, options = {}) {
  const response = await fetch(path, {cache: 'no-store', ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Please check the prompt, dimensions, seed and steps.');
  return data;
}
function switchView(next) {
  view = next; storage.set('view', next);
  document.querySelectorAll('[data-view]').forEach((button) => button.setAttribute('aria-selected', String(button.dataset.view === view)));
  ['generate', 'gallery', 'system'].forEach((name) => $(`${name}-panel`).hidden = name !== view);
  if (view === 'gallery') refreshGallery().catch((error) => message(error.message));
  if (view === 'system') refreshGPU();
}
document.querySelectorAll('[data-view]').forEach((button) => {
  button.onclick = () => switchView(button.dataset.view);
  button.onkeydown = (event) => {if (['ArrowLeft', 'ArrowRight'].includes(event.key)) {event.preventDefault(); const tabs = [...document.querySelectorAll('[data-view]')]; const index = (tabs.indexOf(button) + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; tabs[index].focus(); tabs[index].click();}};
});
function collapse(value) {
  $('hero').classList.toggle('compact', value);
  $('collapse').setAttribute('aria-expanded', String(!value));
  $('collapse').setAttribute('aria-label', value ? 'Expand header' : 'Collapse header');
  $('collapse').textContent = value ? '⌄' : '⌃'; storage.set('collapsed', String(value));
}
$('collapse').onclick = () => collapse(!$('hero').classList.contains('compact'));
collapse(storage.get('collapsed', 'false') === 'true');
function idea() {$('prompt').value = examples[Math.floor(Math.random() * examples.length)]; updatePrompt(); switchView('generate'); $('prompt').focus();}
['examples', 'sample', 'empty-example'].forEach((id) => $(id).onclick = idea);
function updatePrompt() {$('characters').textContent = `${$('prompt').value.length} / 4000`;}
$('prompt').oninput = updatePrompt;
$('random-seed').onclick = () => {$('seed').value = crypto.getRandomValues(new Uint32Array(1))[0];};
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
  $('runtime').textContent = running ? (job.status === 'loading' ? 'Loading model' : 'Generating image') : 'Ready for an idea';
  $('result-state').textContent = running ? job.status : selected ? 'Completed' : 'Ready to create';
  $('busy-title').textContent = job?.status === 'cancelling' ? 'Stopping generation…' : job?.status === 'loading' ? 'Loading your local model…' : 'Creating your image…';
  $('cancel').disabled = job?.status === 'cancelling';
}
function showResult(item) {
  selected = item; $('result-image').src = item.image; $('result-image').alt = item.settings.prompt;
  $('result-image').hidden = false; $('empty').hidden = true; $('result-actions').hidden = false;
  $('result-title').textContent = 'A little imagination, made real.';
  $('result-meta').textContent = `${item.settings.width} × ${item.settings.height} · Seed ${item.settings.seed} · ${item.seconds.toFixed(2)} s generation`;
  $('result-prompt').textContent = item.settings.prompt;
  $('download').href = item.image; $('download').download = `klein-${item.settings.seed}.png`;
  $('result-state').textContent = 'Completed';
}
$('recipe').onclick = () => {
  if (!selected) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify({model: config.model, revision: config.revision, ...selected.settings, reduction: 'ordered'}, null, 2)], {type: 'application/json'}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = `klein-${selected.settings.seed}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
$('generation-form').onsubmit = async (event) => {
  event.preventDefault(); message();
  const payload = {prompt: $('prompt').value, save_gallery: $('save_gallery').checked, ...Object.fromEntries(['width', 'height', 'seed', 'steps'].map((id) => [id, Number($(id).value)]))};
  if (!Number.isSafeInteger(payload.seed)) return message('Choose a whole-number seed within the displayed range.');
  $('generate').disabled = true;
  try {busy(await request('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)}));}
  catch (error) {message(error.message); $('generate').disabled = false;}
};
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
    prompt.textContent = item.settings.prompt; info.append(title, prompt); preview.append(info);
    preview.onclick = () => {Object.entries(item.settings).forEach(([key, value]) => {if ($(key) && key !== 'save_gallery') $(key).value = value;}); updatePrompt(); sizeHelp(); showResult(item); switchView('generate');};
    card.append(preview);
    const controls = document.createElement('div'); controls.className = 'gallery-controls';
    if (terminal.has(item.status)) {
      const label = document.createElement('label'), check = document.createElement('input'); check.type = 'checkbox'; check.checked = marked.has(item.id);
      check.setAttribute('aria-label', `Select saved job ${item.id.slice(0, 8)}`);
      check.onchange = () => {if (check.checked) marked.add(item.id); else marked.delete(item.id); selectionCount();};
      label.append(check, document.createTextNode(' Select'));
      const remove = document.createElement('button'); remove.textContent = 'Delete'; remove.className = 'danger'; remove.onclick = () => confirmDelete([item.id]);
      controls.append(label, remove);
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
  catch (error) {message(`Connection interrupted. Your job may still be running. ${error.message}`);}
  finally {polling = false;}
}
setInterval(pollJob, 900);
setInterval(() => {if (active && !terminal.has(active.status)) $('elapsed').textContent = `${Math.max(0, Math.round(Date.now() / 1000 - active.created))} seconds elapsed`;}, 1000);
function renderGPU() {
  $('gpu').replaceChildren(); const latest = gpuHistory.at(-1);
  if (!latest?.gpus.length) {$('gpu').textContent = 'GPU telemetry unavailable or starting. The monitor will retry while this view is open.'; return;}
  latest.gpus.forEach((gpu) => {
    const name = document.createElement('h3'); name.textContent = gpu.name; $('gpu').append(name);
    const grid = document.createElement('div'); grid.className = 'gpu-grid'; $('gpu').append(grid);
    const windowSeconds = Number($('gpu-window').value);
    for (const [key, title, unit, limit] of [['used', 'VRAM used', 'MiB', gpu.total], ['utilization', 'GPU load', '%', 100], ['temperature', 'Temperature', '°C', 100], ['power', 'Power', 'W', gpu.power_limit], ['fan', 'Fan', '%', 100]]) {
      if (gpu[key] === null) continue;
      const points = gpuHistory.filter((sample) => latest.timestamp - sample.timestamp <= windowSeconds).map((sample) => ({time: sample.timestamp, value: sample.gpus.find((item) => item.index === gpu.index)?.[key]})).filter((point) => Number.isFinite(point.value));
      if (!points.length) continue;
      const average = points.reduce((sum, p) => sum + p.value, 0) / points.length, peak = Math.max(...points.map((p) => p.value));
      const card = document.createElement('div'); card.className = 'gpu-metric'; const label = document.createElement('small'), value = document.createElement('strong'), details = document.createElement('small');
      label.textContent = title; value.textContent = `${gpu[key].toFixed(0)} ${unit}${key === 'used' ? ` / ${gpu.total} MiB` : ''}`; details.textContent = `Average ${average.toFixed(0)} · Peak ${peak.toFixed(0)} ${unit}`;
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 300 65'); svg.setAttribute('preserveAspectRatio', 'none'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', `${title}, average ${average.toFixed(0)}, peak ${peak.toFixed(0)} ${unit}`);
      const line = document.createElementNS(svg.namespaceURI, 'polyline'); const maximum = limit || Math.max(1, peak * 1.1);
      line.setAttribute('points', points.map((p) => `${300 * (p.time - latest.timestamp + windowSeconds) / windowSeconds},${63 - Math.min(1, p.value / maximum) * 60}`).join(' ')); svg.append(line);
      svg.onpointermove = (event) => {const bounds = svg.getBoundingClientRect(); const timestamp = latest.timestamp - windowSeconds + (event.clientX - bounds.left) / bounds.width * windowSeconds; const nearest = points.reduce((a, b) => Math.abs(a.time - timestamp) < Math.abs(b.time - timestamp) ? a : b); svg.setAttribute('aria-label', `${title}: ${nearest.value} ${unit} at ${new Date(nearest.time * 1000).toLocaleTimeString()}`); card.title = svg.getAttribute('aria-label');};
      card.append(label, value, svg, details); grid.append(card);
    }
  });
}
async function refreshGPU() {
  if (view !== 'system' || document.hidden || gpuFetching) return;
  gpuFetching = true;
  try {const data = await request('/system/gpu'); const samples = new Map([...gpuHistory, ...data.history].map((sample) => [sample.timestamp, sample])); gpuHistory = [...samples.values()].sort((a, b) => a.timestamp - b.timestamp).filter((sample) => Date.now() / 1000 - sample.timestamp < 600); storage.set('gpu-history', JSON.stringify(gpuHistory)); renderGPU();} catch { /* Preserve the last valid sample on transient errors. */ } finally {gpuFetching = false;}
}
setInterval(refreshGPU, 1500); document.addEventListener('visibilitychange', () => {if (!document.hidden) {refreshGPU(); pollJob();}});
$('gpu-window').value = storage.get('gpu-window', '60'); $('gpu-window').onchange = () => {storage.set('gpu-window', $('gpu-window').value); renderGPU();};
try {gpuHistory = JSON.parse(storage.get('gpu-history', '[]'));} catch {gpuHistory = [];}
async function start() {
  $('generate').disabled = true;
  try {
    config = await request('/api/status'); $('version').textContent = config.version;
    $('resolution').replaceChildren(...config.presets.map(([width, height]) => new Option(`${width} × ${height}${width === height ? ' · Square' : width > height ? ' · Landscape' : ' · Portrait'}`, `${width}x${height}`)), new Option('Custom size', 'custom'));
    const items = await refreshGallery(); const job = config.active_job ? await request(`/api/jobs/${config.active_job}`) : null;
    const last = items.find((item) => item.status === 'completed'); if (last) showResult(last);
    busy(job || null); sizeHelp(); switchView(['generate', 'gallery', 'system'].includes(storage.get('view', 'generate')) ? storage.get('view', 'generate') : 'generate');
  } catch (error) {message(`Studio connection failed: ${error.message}. Reload to reconnect.`); $('runtime').textContent = 'Disconnected';}
}
start();

async function residencyStatus() {
  if (document.hidden || !config) return;
  try {
    const status = await request('/api/status'); const model = status.model_residency;
    $('model-state').textContent = model.state === 'ready' ? `Weights loaded. Unload in ${model.unload_in_seconds} seconds if idle.` : `Model ${model.state}.`;
    if (!active || terminal.has(active.status)) $('runtime').textContent = model.state === 'ready' ? 'Model loaded · Ready' : 'Ready to load';
    $('unload-model').disabled = Boolean(status.active_job) || model.state === 'unloaded';
  } catch { /* Keep prior state during reconnects. */ }
}
$('save-residency').onclick = async () => {
  try {const idle_seconds = Number($('idle-seconds').value); if (!Number.isInteger(idle_seconds) || idle_seconds < 0 || idle_seconds > 86400) throw new Error('Choose 0–86400 whole seconds.');
    await request('/api/settings', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({idle_seconds})}); $('settings-message').textContent = 'Idle timeout saved.'; await residencyStatus();
  } catch(error) {$('settings-message').textContent = error.message;}
};
$('unload-model').onclick = async () => {try {await request('/api/model/unload', {method:'POST'}); await residencyStatus();} catch(error) {message(error.message);}};
request('/api/settings').then(settings => {$('idle-seconds').value = settings.idle_seconds;}).catch(error => message(error.message));
setInterval(residencyStatus, 2000);

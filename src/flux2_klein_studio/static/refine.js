const $ = (id) => document.getElementById(id);
const terminal = new Set(['completed', 'failed', 'cancelled']);
let api, reference = null, active = null, result = null, uploading = false, polling = false, recipeURL = null;
let formats = [];
export function refineRunning() {return active && !terminal.has(active.status);}
function note(text = '') {$('refine-message').textContent = text;}
function controls() {
  const running = refineRunning();
  $('refine-submit').disabled = !reference || running || uploading;
  $('refine-upload').disabled = Boolean(running || uploading);
  $('refine-clear').disabled = !reference || running || uploading;
  $('refine-use-result').disabled = Boolean(running || uploading);
  $('refine-cancel').hidden = !running;
  $('refine-cancel').disabled = active?.status === 'cancelling';
  $('refine-busy').hidden = !running;
  $('refine-state').textContent = uploading ? 'Preparing reference' : running ? active.status : result ? 'Completed' : reference ? 'Ready to refine' : 'Choose a picture';
  $('refine-busy-title').textContent = active?.status === 'queued' ? `Waiting · Queue position ${active.queue_position}` : active?.status === 'loading' ? 'Loading your local model…' : active?.status === 'processing' ? 'Preparing your picture…' : active?.status === 'cancelling' ? 'Stopping refinement…' : 'Refining your picture…';
  if (running && !$('refine-panel').hidden) $('runtime').textContent = active.status === 'queued' ? 'Refinement queued' : 'Refining image';
}
function sizeChanged() {
  const size = `${$('refine-width').value}x${$('refine-height').value}`;
  $('refine-resolution').value = [...$('refine-resolution').options].some(option => option.value === size) ? size : 'custom';
}
function clearResult() {
  result = null; $('refine-result-image').hidden = true;
  $('refine-result-image').removeAttribute('src');
  $('refine-result-empty').hidden = false; $('refine-result-actions').hidden = true;
  if (recipeURL) URL.revokeObjectURL(recipeURL); recipeURL = null;
}
async function choose(next, dimensions = null) {
  const old = reference; reference = next; clearResult();
  $('refine-source-image').src = next.image; $('refine-source-image').hidden = false;
  $('refine-source-empty').hidden = true;
  $('refine-reference-meta').textContent = `${next.original_width} × ${next.original_height} source · ${next.width} × ${next.height} prepared reference. Temporary until saved with a refinement.`;
  const [width, height] = dimensions || [next.width, next.height];
  const scale = Math.min(1, 1920 / Math.max(width, height));
  $('refine-width').value = Math.max(64, Math.floor(width * scale / 16) * 16);
  $('refine-height').value = Math.max(64, Math.floor(height * scale / 16) * 16);
  $('refine-prompt').value = ''; sizeChanged(); controls();
  if (old && old.id !== next.id) await api.request(`/api/references/${old.id}`, {method: 'DELETE'}).catch(() => {});
}
export async function refineFromJob(item) {
  api.switchView('refine');
  if (refineRunning() || uploading) return note('Finish or cancel the current refinement before choosing another picture.');
  uploading = true; note(); controls();
  try {
    const next = await api.request('/api/references', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({source_job_id: item.id})});
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
    await choose(await api.request('/api/references', {method: 'POST', body}), [item.settings.width, item.settings.height]);
    $('refine-prompt').value = item.settings.prompt;
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
  $('refine-result-prompt').textContent = item.settings.prompt;
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
  $('refine-format').replaceChildren(...formats.map(format => new Option(format.label, format.value)));
  $('refine-resolution').replaceChildren(...config.presets.map(([width, height]) => new Option(`${width} × ${height}`, `${width}x${height}`)), new Option('Custom size', 'custom'));
  sizeChanged(); controls();
}
export function initializeRefine(options) {
  api = options;
  $('refine-gallery').onclick = () => api.switchView('gallery');
  $('refine-upload').onchange = async () => {
    const file = $('refine-upload').files[0]; if (!file) return;
    if (file.size > 16 * 1024 * 1024) return note('Choose an image of at most 16 MiB.');
    uploading = true; note(); controls();
    try {const data = new FormData(); data.append('image', file); await choose(await api.request('/api/references', {method: 'POST', body: data}));}
    catch (error) {note(error.message);} finally {uploading = false; $('refine-upload').value = ''; controls();}
  };
  $('refine-clear').onclick = async () => {
    if (reference) await api.request(`/api/references/${reference.id}`, {method: 'DELETE'}).catch(() => {});
    reference = null; clearResult(); $('refine-source-image').hidden = true; $('refine-source-image').removeAttribute('src'); $('refine-source-empty').hidden = false;
    $('refine-reference-meta').textContent = 'Upload a picture or send a generated result here.'; controls();
  };
  $('refine-resolution').onchange = () => {if ($('refine-resolution').value !== 'custom') {const [width, height] = $('refine-resolution').value.split('x'); $('refine-width').value = width; $('refine-height').value = height;}};
  $('refine-width').oninput = sizeChanged; $('refine-height').oninput = sizeChanged;
  $('refine-random-seed').onchange = () => $('refine-seed').readOnly = $('refine-random-seed').checked;
  $('refine-format').onchange = download;
  $('refine-use-result').onclick = () => {if (result) refineFromJob(result);};
  $('refine-cancel').onclick = async () => {try {active = await api.request(`/api/jobs/${active.id}/cancel`, {method: 'POST'}); controls();} catch (error) {note(error.message);}};
  $('refine-form').onsubmit = async event => {
    event.preventDefault(); if (!reference || refineRunning()) return; note();
    if ($('refine-random-seed').checked) $('refine-seed').value = api.randomSeed();
    const payload = {prompt: $('refine-prompt').value, reference_id: reference.id, save_gallery: $('refine-save-gallery').checked,
      ...Object.fromEntries(['width', 'height', 'seed', 'steps'].map(key => [key, Number($(`refine-${key}`).value)]))};
    if (!Number.isSafeInteger(payload.seed)) return note('Choose a whole-number seed within the displayed range.');
    $('refine-submit').disabled = true;
    try {active = await api.request('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)}); controls();}
    catch (error) {note(error.message); controls();}
  };
  setInterval(poll, 700);
  setInterval(() => {if (refineRunning()) $('refine-elapsed').textContent = `${Math.max(0, Math.round(Date.now() / 1000 - active.created))} seconds elapsed`;}, 1000);
  document.addEventListener('visibilitychange', () => {if (!document.hidden) poll();});
}

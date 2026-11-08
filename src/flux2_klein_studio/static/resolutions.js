import {msg, retranslate} from './i18n.js';
const $ = id => document.getElementById(id);
let data = null, selected = new Set(), dirty = false, saving = false, loading = false;
const familyNames = {square:'Square · 1:1', portrait:'Portrait · 3:4', landscape:'Landscape · 4:3', widescreen:'Widescreen · 16:9', panorama:'Panorama · 2:1', photo:'Photo · 2:3 / 3:2'};
async function request(options = {}) {
  const response = await fetch('/api/resolutions', {cache:'no-store', ...options});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? msg(result.detail) : msg('Could not save resolution choices.'));
  return result;
}
let lastFeedback={source:'',values:[]};
function feedback(source='',...values) {lastFeedback={source,values}; renderFeedback();}
function renderFeedback() {const {source,values}=lastFeedback; $('resolution-message').textContent=Array.isArray(source) ? msg(source,...values.map(value=>typeof value==='string'?retranslate(value):value)) : retranslate(source);}
function updateControls() {
  document.querySelectorAll('[data-resolution-choice]').forEach(check => {check.checked = selected.has(check.dataset.resolutionChoice); check.disabled = saving;});
  document.querySelectorAll('[data-resolution-family]').forEach(check => {
    const rows = data.resolutions.filter(row => row.family === check.dataset.resolutionFamily);
    const count = rows.filter(row => selected.has(row.id)).length;
    check.checked = count === rows.length; check.indeterminate = count > 0 && count < rows.length; check.disabled = saving;
  });
  $('resolution-count').textContent = msg`${selected.size} of ${data.resolutions.length} enabled${dirty ? msg(' · Unsaved changes') : ''}`;
  const fallback = selected.has('square_big') ? 'square_big' : data.resolutions.find(row => selected.has(row.id))?.id;
  $('resolution-default').textContent = selected.size ? `${dirty ? msg('Default after saving') : msg('Default for agents')}: ${fallback}` : msg('No generation sizes enabled.');
  $('save-resolutions').disabled = saving || !dirty;
  ['enable-resolutions','disable-resolutions','reload-resolutions'].forEach(id => {$(id).disabled = saving;});
}
function changed() {
  dirty = selected.size !== data.enabled_resolutions.length || data.enabled_resolutions.some(id => !selected.has(id));
  feedback(selected.size ? '' : msg('All sizes are off. Saving will hide the MCP generation tool and reject agent generation requests.'));
  updateControls();
}
function gib(value) {return Number.isFinite(value) ? msg`${(value / 2 ** 30).toFixed(2)} GiB` : msg('Not measured');}
function render() {
  $('resolution-families').replaceChildren(); $('resolution-rows').replaceChildren();
  const families = [...new Set(data.resolutions.map(row => row.family))];
  families.forEach(family => {
    const label = document.createElement('label'), check = document.createElement('input'); check.type = 'checkbox'; check.dataset.resolutionFamily = family;
    check.onchange = () => {data.resolutions.filter(row => row.family === family).forEach(row => {if(check.checked) selected.add(row.id); else selected.delete(row.id);}); changed();};
    label.append(check, document.createTextNode(msg(familyNames[family] || family))); $('resolution-families').append(label);
  });
  data.resolutions.forEach(row => {
    const tr = document.createElement('tr'), choice = document.createElement('td'), check = document.createElement('input');
    check.type = 'checkbox'; check.dataset.resolutionChoice = row.id; check.setAttribute('aria-label', msg`Enable ${row.id}`);
    check.onchange = () => {if(check.checked) selected.add(row.id); else selected.delete(row.id); changed();}; choice.append(check); tr.append(choice);
    const name = document.createElement('th'); name.scope = 'row'; name.className = 'preset-name'; name.textContent = row.id; tr.append(name);
    const values = [`${row.width} × ${row.height}`, gib(row.memory?.loading_reserved_peak_bytes), gib(row.memory?.reserved_peak_bytes), Number.isFinite(row.memory?.sampled_device_headroom_mib) ? msg`${(row.memory.sampled_device_headroom_mib / 1024).toFixed(2)} GiB` : msg('Not measured')];
    values.forEach(value => {const td = document.createElement('td'); td.textContent = value; tr.append(td);});
    if(row.memory) tr.title = msg`Benchmark: ${row.memory.status}. Generation allocated peak: ${gib(row.memory.allocated_peak_bytes)}. Sampled whole-GPU generation peak: ${Number.isFinite(row.memory.generation_device_peak_mib) ? (row.memory.generation_device_peak_mib / 1024).toFixed(2) + msg(' GiB') : msg('not measured')}.`;
    $('resolution-rows').append(tr);
  });
  const ref = data.reference;
  $('resolution-reference').textContent = ref ? msg`Reference: ${ref.environment.gpu} · ${ref.steps} steps · ${ref.warmups} warmups + ${ref.measured_calls_per_resolution} measured calls · Run ${ref.run_id}` : msg('No packaged VRAM measurements are available in this build.');
  updateControls();
}
export async function loadResolutionSettings(force = false) {
  if(loading || saving || (dirty && !force)) return;
  loading = true;
  try {data = await request(); selected = new Set(data.enabled_resolutions); dirty = false; render(); feedback('');}
  catch(error) {feedback(error.message);}
  finally {loading = false;}
}
$('enable-resolutions').onclick = () => {if(data) {selected = new Set(data.resolutions.map(row => row.id)); changed();}};
$('disable-resolutions').onclick = () => {if(data) {selected.clear(); changed();}};
$('reload-resolutions').onclick = () => loadResolutionSettings(true);
$('save-resolutions').onclick = async () => {
  if(!data || saving) return;
  saving = true; updateControls(); feedback('Saving…');
  try {
    data = await request({method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled_resolutions:data.resolutions.filter(row => selected.has(row.id)).map(row => row.id)})});
    selected = new Set(data.enabled_resolutions); dirty = false; render();
    feedback`Saved. ${selected.size ? msg('Enabled sizes are available now.') : msg('Agent generation is disabled.')} Refresh MCP tools or reconnect your agent to update cached choices.`;
  } catch(error) {feedback(error.message);}
  finally {saving = false; updateControls();}
};

window.addEventListener('ui-locale-changed',()=>{if(data){render();renderFeedback();}});

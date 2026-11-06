const $ = id => document.getElementById(id);
let data = null, selected = new Set(), dirty = false, saving = false, loading = false;
const familyNames = {square:'Square · 1:1', portrait:'Portrait · 3:4', landscape:'Landscape · 4:3', widescreen:'Widescreen · 16:9', panorama:'Panorama · 2:1', photo:'Photo · 2:3 / 3:2'};
async function request(options = {}) {
  const response = await fetch('/api/resolutions', {cache:'no-store', ...options});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not save resolution choices.');
  return result;
}
function feedback(text) {$('resolution-message').textContent = text;}
function updateControls() {
  document.querySelectorAll('[data-resolution-choice]').forEach(check => {check.checked = selected.has(check.dataset.resolutionChoice); check.disabled = saving;});
  document.querySelectorAll('[data-resolution-family]').forEach(check => {
    const rows = data.resolutions.filter(row => row.family === check.dataset.resolutionFamily);
    const count = rows.filter(row => selected.has(row.id)).length;
    check.checked = count === rows.length; check.indeterminate = count > 0 && count < rows.length; check.disabled = saving;
  });
  $('resolution-count').textContent = `${selected.size} of ${data.resolutions.length} enabled${dirty ? ' · Unsaved changes' : ''}`;
  const fallback = selected.has('square_big') ? 'square_big' : data.resolutions.find(row => selected.has(row.id))?.id;
  $('resolution-default').textContent = selected.size ? `${dirty ? 'Default after saving' : 'Default for agents'}: ${fallback}` : 'No generation sizes enabled.';
  $('save-resolutions').disabled = saving || !dirty;
  ['enable-resolutions','disable-resolutions','reload-resolutions'].forEach(id => {$(id).disabled = saving;});
}
function changed() {
  dirty = selected.size !== data.enabled_resolutions.length || data.enabled_resolutions.some(id => !selected.has(id));
  feedback(selected.size ? '' : 'All sizes are off. Saving will hide the MCP generation tool and reject agent generation requests.');
  updateControls();
}
function gib(value) {return Number.isFinite(value) ? `${(value / 2 ** 30).toFixed(2)} GiB` : 'Not measured';}
function render() {
  $('resolution-families').replaceChildren(); $('resolution-rows').replaceChildren();
  const families = [...new Set(data.resolutions.map(row => row.family))];
  families.forEach(family => {
    const label = document.createElement('label'), check = document.createElement('input'); check.type = 'checkbox'; check.dataset.resolutionFamily = family;
    check.onchange = () => {data.resolutions.filter(row => row.family === family).forEach(row => {if(check.checked) selected.add(row.id); else selected.delete(row.id);}); changed();};
    label.append(check, document.createTextNode(familyNames[family] || family)); $('resolution-families').append(label);
  });
  data.resolutions.forEach(row => {
    const tr = document.createElement('tr'), choice = document.createElement('td'), check = document.createElement('input');
    check.type = 'checkbox'; check.dataset.resolutionChoice = row.id; check.setAttribute('aria-label', `Enable ${row.id}`);
    check.onchange = () => {if(check.checked) selected.add(row.id); else selected.delete(row.id); changed();}; choice.append(check); tr.append(choice);
    const name = document.createElement('th'); name.scope = 'row'; name.className = 'preset-name'; name.textContent = row.id; tr.append(name);
    const values = [`${row.width} × ${row.height}`, gib(row.memory?.loading_reserved_peak_bytes), gib(row.memory?.reserved_peak_bytes), Number.isFinite(row.memory?.sampled_device_headroom_mib) ? `${(row.memory.sampled_device_headroom_mib / 1024).toFixed(2)} GiB` : 'Not measured'];
    values.forEach(value => {const td = document.createElement('td'); td.textContent = value; tr.append(td);});
    if(row.memory) tr.title = `Benchmark: ${row.memory.status}. Generation allocated peak: ${gib(row.memory.allocated_peak_bytes)}. Sampled whole-GPU generation peak: ${Number.isFinite(row.memory.generation_device_peak_mib) ? (row.memory.generation_device_peak_mib / 1024).toFixed(2) + ' GiB' : 'not measured'}.`;
    $('resolution-rows').append(tr);
  });
  const ref = data.reference;
  $('resolution-reference').textContent = ref ? `Reference: ${ref.environment.gpu} · ${ref.steps} steps · ${ref.warmups} warmups + ${ref.measured_calls_per_resolution} measured calls · Run ${ref.run_id}` : 'No packaged VRAM measurements are available in this build.';
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
    feedback(`Saved. ${selected.size ? 'Enabled sizes are available now.' : 'Agent generation is disabled.'} Refresh MCP tools or reconnect your agent to update cached choices.`);
  } catch(error) {feedback(error.message);}
  finally {saving = false; updateControls();}
};

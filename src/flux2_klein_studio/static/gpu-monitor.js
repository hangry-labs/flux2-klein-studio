const svgNS = 'http://www.w3.org/2000/svg';
const metrics = [
  ['utilization', 'GPU utilization', '%', 100, '#ff984d'],
  ['used', 'VRAM', 'MiB', 'total', '#75a7ff'],
  ['temperature', 'Temperature', '°C', 100, '#ef7880'],
  ['power', 'Power', 'W', 'power_limit', '#f3ce65'],
  ['fan', 'Fan', '%', 100, '#77cba0'],
];
const finite = value => typeof value === 'number' && Number.isFinite(value);
const format = (value, unit) => unit === 'MiB' ? `${(value / 1024).toFixed(1)} GiB` : `${value.toFixed(0)}${unit === '%' ? '' : ' '}${unit}`;
function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function svgNode(tag, attributes) {
  const element = document.createElementNS(svgNS, tag);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  return element;
}

export function initializeGPU({request, storage}) {
  const root = document.getElementById('gpu');
  let history = [], windowSeconds = storage.get('gpu-window', '60') === '600' ? 600 : 60;
  let visible = false, timer = null, fetching = false, stopped = false;
  try {
    const restored = JSON.parse(storage.get('gpu-history', '[]'));
    if (Array.isArray(restored)) history = restored.filter(validSample).slice(-600);
  } catch { /* Browser history is optional. */ }
  function validSample(sample) {return sample && finite(sample.timestamp) && Array.isArray(sample.gpus);}
  function updateWindow() {
    document.querySelectorAll('[data-gpu-window]').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.gpuWindow) === windowSeconds)));
  }
  function render() {
    const latest = history.at(-1);
    if (!latest?.gpus.length) {
      root.replaceChildren(node('p', 'muted', 'GPU telemetry unavailable or starting. The monitor will retry while this view is open.'));
      return;
    }
    const cards = latest.gpus.map(gpu => {
      const card = node('article', 'gpu-card');
      const heading = node('div', 'gpu-card-head');
      heading.append(node('strong', '', `GPU ${gpu.index}`), node('span', '', gpu.name));
      const grid = node('div', 'gpu-grid');
      for (const [key, label, unit, bound, color] of metrics) {
        if (!finite(gpu[key])) continue;
        const points = history.filter(sample => latest.timestamp - sample.timestamp <= windowSeconds)
          .map(sample => ({time: sample.timestamp, value: sample.gpus.find(item => item.index === gpu.index)?.[key]}))
          .filter(point => finite(point.value));
        if (!points.length) continue;
        const average = points.reduce((sum, point) => sum + point.value, 0) / points.length;
        const peak = Math.max(...points.map(point => point.value));
        const scale = typeof bound === 'string' ? gpu[bound] : bound;
        const maximum = finite(scale) && scale > 0 ? scale : Math.max(1, peak * 1.1);
        const chart = node('div', 'gpu-metric'); chart.style.setProperty('--chart-color', color);
        const caption = node('div', 'gpu-chart-scale');
        const value = node('strong', '', format(gpu[key], unit));
        if (key === 'used' && finite(gpu.total)) value.title = `Total VRAM: ${format(gpu.total, unit)}`;
        caption.append(node('span', '', label), value);
        const plot = node('div', 'gpu-chart-plot');
        const svg = svgNode('svg', {viewBox: '0 0 300 86', preserveAspectRatio: 'none', role: 'img', tabindex: '0',
          'aria-label': `${label}: ${format(gpu[key], unit)}, average ${format(average, unit)}, peak ${format(peak, unit)}`});
        for (let x = 0; x <= 300; x += 30) svg.append(svgNode('line', {x1: x, x2: x, y1: 0, y2: 86, class: 'gpu-grid-line'}));
        for (let y = 0; y <= 86; y += 21.5) svg.append(svgNode('line', {x1: 0, x2: 300, y1: y, y2: y, class: 'gpu-grid-line'}));
        const coordinates = points.map(point => [Math.max(1, Math.min(299, 300 * (point.time - latest.timestamp + windowSeconds) / windowSeconds)),
          84 - Math.max(0, Math.min(1, point.value / maximum)) * 82]);
        const line = coordinates.map(point => point.join(',')).join(' ');
        svg.append(svgNode('polygon', {points: `${coordinates[0][0]},86 ${line} ${coordinates.at(-1)[0]},86`, class: 'gpu-chart-area'}),
          svgNode('polyline', {points: line, class: 'gpu-chart-line'}),
          svgNode('circle', {cx: coordinates.at(-1)[0], cy: coordinates.at(-1)[1], r: 2.5, class: 'gpu-chart-marker'}));
        const cursor = node('span', 'gpu-hover-line'), tooltip = node('span', 'gpu-hover-tooltip');
        cursor.hidden = tooltip.hidden = true;
        const showSample = fraction => {
          const timestamp = latest.timestamp - windowSeconds + fraction * windowSeconds;
          const nearest = points.reduce((a, b) => Math.abs(a.time - timestamp) < Math.abs(b.time - timestamp) ? a : b);
          const position = Math.max(0, Math.min(1, (nearest.time - latest.timestamp + windowSeconds) / windowSeconds));
          cursor.style.left = tooltip.style.left = `${position * 100}%`;
          tooltip.classList.toggle('align-start', position < .3); tooltip.classList.toggle('align-end', position > .7);
          tooltip.textContent = `${format(nearest.value, unit)} · ${new Date(nearest.time * 1000).toLocaleTimeString()}`;
          cursor.hidden = tooltip.hidden = false;
        };
        svg.onpointermove = event => {const rect = svg.getBoundingClientRect(); showSample((event.clientX - rect.left) / rect.width);};
        svg.onpointerleave = svg.onblur = () => {cursor.hidden = tooltip.hidden = true;};
        svg.onfocus = () => showSample(1);
        plot.append(svg, cursor, tooltip);
        const axis = node('div', 'gpu-chart-axis');
        axis.append(node('span', '', windowSeconds === 60 ? '1 minute' : '10 minutes'), node('span', '', `Average ${format(average, unit)} · Peak ${format(peak, unit)}`));
        chart.append(caption, plot, axis); grid.append(chart);
      }
      card.append(heading, grid);
      if (finite(gpu.total)) card.append(node('div', 'gpu-live-details', `Total VRAM ${format(gpu.total, 'MiB')}${finite(gpu.power_limit) ? ` · Power limit ${format(gpu.power_limit, 'W')}` : ''}`));
      return card;
    });
    // Keep a hovered or keyboard-focused plot stable until interaction finishes.
    if (root.querySelector('svg:focus, .gpu-chart-plot:hover')) return;
    root.replaceChildren(...cards);
  }
  async function refresh() {
    if (!visible || document.hidden || fetching || stopped) return;
    fetching = true;
    try {
      const data = await request('/system/gpu');
      const samples = new Map([...history, ...(data.history || []).filter(validSample)].map(sample => [sample.timestamp, sample]));
      history = [...samples.values()].sort((a, b) => a.timestamp - b.timestamp).filter(sample => Date.now() / 1000 - sample.timestamp < 600).slice(-600);
      storage.set('gpu-history', JSON.stringify(history));
      if (visible && !document.hidden && !stopped) render();
    } catch { /* Keep the last valid charts on transient network failures. */ }
    finally {fetching = false;}
  }
  function schedule() {
    clearInterval(timer); timer = null;
    if (visible && !document.hidden && !stopped) {render(); refresh(); timer = setInterval(refresh, 1500);}
  }
  document.querySelectorAll('[data-gpu-window]').forEach(button => {
    button.onclick = () => {windowSeconds = Number(button.dataset.gpuWindow); storage.set('gpu-window', String(windowSeconds)); updateWindow(); render();};
  });
  updateWindow();
  document.addEventListener('visibilitychange', schedule);
  window.addEventListener('pagehide', () => {stopped = true; clearInterval(timer);});
  window.addEventListener('pageshow', () => {stopped = false; schedule();});
  return {setVisible(value) {visible = value; schedule();}};
}

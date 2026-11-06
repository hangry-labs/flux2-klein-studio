const $ = id => document.getElementById(id);

export function initializeShell(storage) {
  let animation;
  function collapse(compact, animate = false) {
    const hero = $('hero');
    const from = hero.getBoundingClientRect().height;
    animation?.cancel();
    hero.classList.toggle('compact', compact);
    const control = $('collapse');
    control.setAttribute('aria-expanded', String(!compact));
    control.setAttribute('aria-label', compact ? 'Expand header' : 'Collapse header');
    control.title = control.getAttribute('aria-label');
    storage.set('collapsed', String(compact));
    if (animate && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const to = hero.getBoundingClientRect().height;
      animation = hero.animate([{height: `${from}px`}, {height: `${to}px`}], {duration: 220, easing: 'ease-out'});
    }
  }
  $('collapse').onclick = () => collapse(!$('hero').classList.contains('compact'), true);
  collapse(storage.get('collapsed', 'false') === 'true');
}

export function runtimeState(state, text) {
  if (text) $('runtime').textContent = text;
  $('runtime-badge').dataset.state = state;
}

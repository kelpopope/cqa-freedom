// Ширина бокового меню: тянуть правый край мышью или тачпадом, как в приложении Claude.
// 200–360 px; уже ~140 px — меню схлопывается в рейку с иконками (76 px), шире — разворачивается обратно.
// Двойной клик — ширина по умолчанию. Клавиатура: ←/→ по 16 px, Home/End — минимум/максимум.
// Ширина хранится в localStorage (в Dynamics 365 хранилище может быть недоступно — тогда просто не запоминается).
// На планшете (рейка по медиазапросу) и телефоне (выезжающий ящик) ручки нет и сохранённая ширина не применяется.

const KEY = 'cqa.sideWidth';
const MIN = 200;
const MAX = 360;
const DEFAULT = 220;
const RAIL = 76;
const COLLAPSE_AT = 140;
const STEP = 16;
const wideScreen = matchMedia('(min-width: 1101px)');

const clamp = (w) => Math.min(MAX, Math.max(MIN, Math.round(w)));

function load() {
  try {
    const v = localStorage.getItem(KEY);
    if (v === 'rail') return 'rail';
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? clamp(n) : DEFAULT;
  } catch { return DEFAULT; }
}

function store(state) {
  try { localStorage.setItem(KEY, String(state)); } catch { /* хранилище недоступно */ }
}

let state = load();

function apply(root, handle) {
  if (!wideScreen.matches) {
    root.style.removeProperty('--cqa-side-w');
    delete root.dataset.side;
    return;
  }
  if (state === 'rail') {
    root.dataset.side = 'rail';
    root.style.setProperty('--cqa-side-w', `${RAIL}px`);
  } else {
    delete root.dataset.side;
    root.style.setProperty('--cqa-side-w', `${state}px`);
  }
  if (handle) {
    handle.setAttribute('aria-valuenow', String(state === 'rail' ? RAIL : state));
    handle.setAttribute('aria-valuetext', state === 'rail' ? 'свёрнуто в иконки' : `${state} пикселей`);
  }
}

/** Ширина при перетаскивании: схлопывание в рейку ниже порога, иначе 200–360. */
const fromDrag = (w) => (w < COLLAPSE_AT ? 'rail' : clamp(w));

export function initResizer(root) {
  const side = root.querySelector('#cqa-side');
  if (!side) return;
  const handle = document.createElement('div');
  handle.className = 'cqa-side__resizer';
  Object.entries({
    role: 'separator', 'aria-orientation': 'vertical', 'aria-label': 'Ширина бокового меню',
    'aria-valuemin': String(RAIL), 'aria-valuemax': String(MAX), tabindex: '0', title: 'Потяните, чтобы изменить ширину. Двойной клик — по умолчанию',
  }).forEach(([k, v]) => handle.setAttribute(k, v));
  side.appendChild(handle);
  apply(root, handle);

  const set = (next, { save = true } = {}) => { state = next; apply(root, handle); if (save) store(state); };

  let startX = 0; let startW = 0; let dragging = false;
  handle.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || !wideScreen.matches) return;
    e.preventDefault();
    dragging = true; startX = e.clientX; startW = side.getBoundingClientRect().width;
    handle.setPointerCapture(e.pointerId);
    root.classList.add('cqa-resizing');
  });
  handle.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const next = fromDrag(startW + (e.clientX - startX));
    if (next !== state) set(next, { save: false });
  });
  const end = (e) => {
    if (!dragging) return;
    dragging = false;
    if (handle.hasPointerCapture?.(e.pointerId)) handle.releasePointerCapture(e.pointerId);
    root.classList.remove('cqa-resizing');
    store(state);
  };
  handle.addEventListener('pointerup', end);
  handle.addEventListener('pointercancel', end);
  handle.addEventListener('dblclick', () => set(DEFAULT));
  handle.addEventListener('keydown', (e) => {
    const cur = state === 'rail' ? RAIL : state;
    let next = null;
    if (e.key === 'ArrowRight') next = state === 'rail' ? MIN : clamp(cur + STEP);
    else if (e.key === 'ArrowLeft') next = state === 'rail' ? 'rail' : cur - STEP < MIN ? 'rail' : clamp(cur - STEP);
    else if (e.key === 'Home') next = MIN;
    else if (e.key === 'End') next = MAX;
    else if (e.key === 'Enter') next = DEFAULT;
    if (next === null) return;
    e.preventDefault();
    set(next);
  });
}

// Переход между телефоном/планшетом и десктопом: применить или снять сохранённую ширину.
wideScreen.addEventListener?.('change', () => {
  const root = document.querySelector('.cqa-root');
  if (root) apply(root, root.querySelector('.cqa-side__resizer'));
});

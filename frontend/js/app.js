// Оболочка: боковое меню, роли, маршруты, палитра команд (Ctrl K), горячие клавиши, адаптив.
import { api } from './api.js';
import { store, ROLES } from './store.js';
import { icon } from './icons.js';
import { esc, toast } from './utils.js';
import { openModal, openPopover } from './ui.js';
import * as overview from './views/overview.js';
import * as workspace from './views/workspace.js';
import * as appeals from './views/appeals.js';
import * as checklist from './views/checklist.js';

const root = document.getElementById('cqa-app');
let current = null;
let currentKey = '';

const NAV = [
  { key: 'overview', href: '#/overview', label: 'Обзор', icon: 'chart', roles: ['manager', 'qa'] },
  { key: 'dialogs', href: '#/dialogs', label: 'Диалоги', icon: 'chat', roles: ['manager', 'qa', 'operator'] },
  { key: 'appeals', href: '#/appeals', label: 'Апелляции', icon: 'scale', roles: ['manager', 'qa', 'operator'], pill: true },
  { key: 'checklist', href: '#/checklist', label: 'Чек-лист', icon: 'checklist', roles: ['manager', 'qa'] },
];

const ROUTES = [
  { re: /^#\/overview\/?$/, key: 'overview', view: overview },
  { re: /^#\/dialogs\/(.+)$/, key: 'dialogs', view: workspace, params: (m) => ({ id: decodeURIComponent(m[1]) }) },
  { re: /^#\/dialogs\/?$/, key: 'dialogs', view: workspace },
  { re: /^#\/c\/(.+)$/, key: 'dialogs', view: workspace, params: (m) => ({ id: decodeURIComponent(m[1]) }) },
  { re: /^#\/appeals\/(.+)$/, key: 'appeals', view: appeals, params: (m) => ({ id: decodeURIComponent(m[1]) }) },
  { re: /^#\/appeals\/?$/, key: 'appeals', view: appeals },
  { re: /^#\/checklist\/?$/, key: 'checklist', view: checklist },
];

// Официальный логотип Freedom Broker: знак-щит + словесный знак (тёмный для светлой темы, светлый для тёмной).
const LOGO = `<img class="cqa-logo__mark" src="assets/freedom-mark.png" alt="" width="30" height="34"><div class="cqa-logo__words"><img class="cqa-logo__word cqa-logo__word--ink" src="assets/freedom-word.png" alt="Freedom Broker" height="26"><img class="cqa-logo__word cqa-logo__word--light" src="assets/freedom-word-light.png" alt="" height="26"></div>`;

function defaultRoute() { return store.get().role === 'manager' ? '#/overview' : '#/dialogs'; }

function shell() {
  const s = store.get();
  const role = ROLES[s.role];
  root.dataset.theme = s.theme;
  document.body.style.background = s.theme === 'dark' ? '#121512' : '#f5f6f3';
  root.innerHTML = `
    <a class="cqa-skip" href="#cqa-main" data-skip>Перейти к содержимому</a>
    <div class="cqa-shell">
      <aside class="cqa-side" id="cqa-side" aria-label="Главное меню">
        <a class="cqa-logo" href="${defaultRoute()}" aria-label="Freedom Broker, контроль качества">${LOGO}</a>
        <div class="cqa-logo__sub">Контроль качества</div>
        <nav class="cqa-menu" id="cqa-menu">
          ${NAV.filter((n) => n.roles.includes(s.role)).map((n) => `<a href="${n.href}" data-nav="${n.key}" title="${n.label}" aria-label="${n.label}">${icon(n.icon)}<span>${n.label}</span>${n.pill ? '<span class="cqa-pill" id="cqa-pill-appeals" hidden></span>' : ''}</a>`).join('')}
        </nav>
        <div class="cqa-dataset" id="cqa-dataset">
          <div class="cqa-dataset__label">Набор данных</div>
          <div class="cqa-dataset__row"><b id="cqa-ds-name">${esc(s.groupId)}</b><span id="cqa-ds-count">загрузка</span></div>
          <div class="cqa-dataset__bar"><i id="cqa-ds-bar" style="width:0"></i></div>
        </div>
        <div class="cqa-side__foot">
          <div class="cqa-side__tools">
            <button type="button" class="cqa-btn cqa-btn--ghost cqa-btn--icon cqa-btn--sm" id="cqa-cmd-btn" title="Поиск (Ctrl K)" aria-label="Поиск">${icon('search')}</button>
            <button type="button" class="cqa-btn cqa-btn--ghost cqa-btn--icon cqa-btn--sm" id="cqa-theme" title="Тема" aria-label="Сменить тему">${icon(s.theme === 'dark' ? 'sun' : 'moon')}</button>
            <button type="button" class="cqa-btn cqa-btn--ghost cqa-btn--icon cqa-btn--sm" id="cqa-help" title="Горячие клавиши (?)" aria-label="Горячие клавиши">${icon('keyboard')}</button>
          </div>
          <button type="button" class="cqa-user" id="cqa-user" aria-haspopup="menu" title="Сменить роль">
            <span class="cqa-avatar">${role.initials}</span>
            <span class="cqa-user__text"><span class="cqa-user__name">${esc(role.person)}</span><span class="cqa-user__role">${role.label}</span></span>
            ${icon('up', 'cqa-ico--sm')}
          </button>
        </div>
      </aside>
      <div class="cqa-scrim" data-close-nav></div>
      <div class="cqa-app">
        <header class="cqa-mtop">
          <button type="button" class="cqa-btn cqa-btn--ghost cqa-btn--icon" id="cqa-burger" aria-label="Меню">${icon('menu')}</button>
          <a class="cqa-logo" href="${defaultRoute()}" aria-label="Freedom Broker">${LOGO}</a>
          <div class="cqa-mtop__right"><button type="button" class="cqa-btn cqa-btn--ghost cqa-btn--icon" data-cmd aria-label="Поиск">${icon('search')}</button></div>
        </header>
        <main class="cqa-main" id="cqa-main" tabindex="-1"></main>
      </div>
    </div>`;

  root.querySelector('#cqa-theme').addEventListener('click', () => {
    const theme = store.get().theme === 'dark' ? 'light' : 'dark';
    store.set({ theme });
    root.dataset.theme = theme;
    document.body.style.background = theme === 'dark' ? '#121512' : '#f5f6f3';
    root.querySelector('#cqa-theme').innerHTML = icon(theme === 'dark' ? 'sun' : 'moon');
  });
  root.querySelector('#cqa-user').addEventListener('click', (e) => {
    openPopover(e.currentTarget, Object.entries(ROLES).map(([k, r]) => ({ value: k, label: r.label, hint: r.hint, checked: k === store.get().role })), (v) => {
      if (v === store.get().role) return;
      store.set({ role: v });
      shell();
      toast(`Роль: ${ROLES[v].label}`);
      const allowed = NAV.find((n) => n.key === currentKey)?.roles.includes(v);
      if (!allowed) location.hash = defaultRoute(); else route(true);
    }, { up: true });
  });
  root.querySelector('#cqa-cmd-btn').addEventListener('click', openPalette);
  root.querySelectorAll('[data-cmd]').forEach((b) => b.addEventListener('click', openPalette));
  root.querySelector('#cqa-help').addEventListener('click', openShortcuts);
  // Ссылка «к содержимому» не должна менять hash-маршрут
  root.querySelector('[data-skip]').addEventListener('click', (e) => { e.preventDefault(); root.querySelector('#cqa-main').focus(); });
  root.querySelector('#cqa-burger').addEventListener('click', () => { root.dataset.navOpen = 'true'; });
  root.querySelector('[data-close-nav]').addEventListener('click', () => { root.dataset.navOpen = 'false'; });
  root.querySelector('#cqa-menu').addEventListener('click', () => { root.dataset.navOpen = 'false'; });
  markNav();
  refreshShell();
}

function markNav() {
  root.querySelectorAll('[data-nav]').forEach((a) => {
    if (a.dataset.nav === currentKey) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
}

export async function refreshShell() {
  try {
    const [list, open] = await Promise.all([api.listConversations(), api.listAppeals('open')]);
    const ev = list.filter((x) => x.evaluation && x.evaluation.status !== 'error').length;
    const n = root.querySelector('#cqa-ds-name'); if (n) n.textContent = store.get().groupId;
    const c = root.querySelector('#cqa-ds-count'); const bar = root.querySelector('#cqa-ds-bar');
    if (c) c.textContent = `${ev} из ${list.length} оценено`;
    if (bar) bar.style.width = `${list.length ? (ev / list.length) * 100 : 0}%`;
    const pill = root.querySelector('#cqa-pill-appeals');
    if (pill) { pill.hidden = !open.length; pill.textContent = open.length; }
  } catch { /* не критично */ }
}

/* ---------- Палитра команд ---------- */
let paletteOpen = false;
async function openPalette() {
  if (paletteOpen) return;
  paletteOpen = true;
  const role = store.get().role;
  const screens = NAV.filter((n) => n.roles.includes(role)).map((n) => ({ group: 'Разделы', label: n.label, hint: '', href: n.href, icon: n.icon }));
  let dialogs = [];
  try {
    dialogs = (await api.listConversations()).map((d) => ({ group: 'Диалоги', label: d.conversation_id, hint: d.evaluation?.headline || [d.operator_name, d.language].filter(Boolean).join(', '), href: `#/dialogs/${encodeURIComponent(d.conversation_id)}`, icon: d.channel === 'voice call' ? 'phone' : 'chat', score: d.evaluation?.final_total }));
  } catch { /* нет данных */ }
  const actions = [
    { group: 'Действия', label: 'Сменить тему', hint: '', run: () => root.querySelector('#cqa-theme').click(), icon: 'moon' },
    { group: 'Действия', label: 'Горячие клавиши', hint: '?', run: openShortcuts, icon: 'keyboard' },
  ];
  const all = [...screens, ...dialogs, ...actions];
  const back = document.createElement('div');
  back.className = 'cqa-cmd-back';
  back.innerHTML = `<div class="cqa-cmd" role="dialog" aria-modal="true" aria-label="Поиск">
      <div class="cqa-cmd__input">${icon('search')}<input id="cqa-cmd-q" placeholder="Диалог, оператор, фраза или раздел" autocomplete="off"><span class="cqa-kbd">Esc</span></div>
      <div class="cqa-cmd__list cqa-scroll" id="cqa-cmd-list" role="listbox"></div>
      <div class="cqa-cmd__foot"><span><span class="cqa-kbd">↑</span> <span class="cqa-kbd">↓</span> выбор</span><span><span class="cqa-kbd">Enter</span> открыть</span></div>
    </div>`;
  root.appendChild(back);
  const input = back.querySelector('#cqa-cmd-q'); const listEl = back.querySelector('#cqa-cmd-list');
  let sel = 0; let shown = [];
  const close = () => { back.remove(); paletteOpen = false; };
  const draw = () => {
    const q = input.value.trim().toLowerCase();
    shown = all.filter((x) => !q || `${x.label} ${x.hint}`.toLowerCase().includes(q)).slice(0, 30);
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    let g = '';
    listEl.innerHTML = shown.length ? shown.map((x, i) => {
      const head = x.group !== g ? `<div class="cqa-cmd__group">${esc(x.group)}</div>` : '';
      g = x.group;
      return `${head}<button type="button" class="cqa-cmd__item" role="option" aria-selected="${i === sel}" data-i="${i}">${icon(x.icon, 'cqa-ico--sm')}<span><b style="font-weight:600">${esc(x.label)}</b>${x.hint ? ` <span class="cqa-faint">${esc(x.hint)}</span>` : ''}</span>${x.score != null ? `<small>${new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2 }).format(x.score)}</small>` : ''}</button>`;
    }).join('') : '<div class="cqa-empty" style="padding:28px">Ничего не найдено</div>';
    listEl.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  };
  const go = (x) => { if (!x) return; close(); if (x.run) x.run(); else location.hash = x.href; };
  input.addEventListener('input', () => { sel = 0; draw(); });
  back.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') close();
    else if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(shown.length - 1, sel + 1); draw(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); draw(); }
    else if (e.key === 'Enter') { e.preventDefault(); go(shown[sel]); }
  });
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  listEl.addEventListener('click', (e) => { const b = e.target.closest('[data-i]'); if (b) go(shown[Number(b.dataset.i)]); });
  draw();
  input.focus();
}

function openShortcuts() {
  const rows = [
    ['Ctrl K', 'Поиск по диалогам и разделам'],
    ['J / K', 'Следующий / предыдущий диалог'],
    ['1–8', 'Открыть критерий'],
    ['/', 'Поиск в очереди'],
    ['Esc', 'Закрыть окно, снять выделение'],
    ['?', 'Эта подсказка'],
  ];
  openModal({ title: 'Горячие клавиши', body: `<table class="cqa-calc">${rows.map(([k, t]) => `<tr><td style="width:120px"><span class="cqa-kbd">${k}</span></td><td>${t}</td></tr>`).join('')}</table>` });
}

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); return; }
  if (e.target.closest?.('input, textarea, select') || document.querySelector('.cqa-modal-back, .cqa-cmd-back')) return;
  if (e.key === '?') openShortcuts();
});

/* ---------- Маршруты ---------- */
async function route(force = false) {
  const hash = location.hash || defaultRoute();
  const r = ROUTES.find((x) => x.re.test(hash));
  if (!r) { location.hash = defaultRoute(); return; }
  const navItem = NAV.find((n) => n.key === r.key);
  if (!navItem.roles.includes(store.get().role)) { location.hash = defaultRoute(); return; }
  const params = r.params ? r.params(hash.match(r.re)) : {};
  // Внутри рабочего места смена диалога не пересоздаёт экран.
  if (!force && current === r.view && r.view.update) { await r.view.update(params); return; }
  if (current?.destroy) current.destroy();
  current = r.view; currentKey = r.key;
  markNav();
  const main = root.querySelector('#cqa-main');
  main.innerHTML = '';
  main.classList.remove('cqa-enter'); void main.offsetWidth; main.classList.add('cqa-enter');
  try {
    await r.view.render(main, params);
    // После смены экрана фокус — в содержимое (для клавиатуры и экранных дикторов)
    main.focus({ preventScroll: true });
  } catch (err) {
    console.error(err);
    main.innerHTML = `<div class="cqa-empty"><div class="cqa-empty__icon">${icon('alert')}</div><h2>Не удалось загрузить</h2><p>${esc(err.message)}</p><a class="cqa-btn" href="${defaultRoute()}">На главную</a></div>`;
  }
  refreshShell();
}

store.subscribe((s, patch) => { if ('role' in patch) markNav(); if ('groupId' in patch) refreshShell(); });
shell();
window.addEventListener('hashchange', () => route());
if (!location.hash) location.hash = defaultRoute(); else route();

// Оболочка приложения: шапка, навигация, роутинг по hash, роль и тема.
import { api } from './api.js';
import { store } from './store.js';
import { icon } from './icons.js';
import { esc } from './utils.js';
import * as listView from './views/list.js';
import * as convView from './views/conversation.js';
import * as checklistView from './views/checklist.js';
import * as appealsView from './views/appeals.js';

const root = document.getElementById('cqa-app');
let current = null;

const routes = [
  { re: /^#\/c\/(.+)$/, view: convView, nav: 'list', params: (m) => ({ id: decodeURIComponent(m[1]) }) },
  { re: /^#\/checklist$/, view: checklistView, nav: 'checklist' },
  { re: /^#\/appeals$/, view: appealsView, nav: 'appeals' },
  { re: /^#?\/?(list)?$/, view: listView, nav: 'list' },
];

function shell() {
  const s = store.get();
  root.dataset.theme = s.theme;
  root.innerHTML = `
    <header class="cqa-header">
      <a class="cqa-brand" href="#/list" aria-label="Контроль качества диалогов">
        <span class="cqa-brand__mark">${icon('shield')}</span>
        <span class="cqa-brand__name">Freedom</span>
        <span class="cqa-brand__sub">Контроль качества диалогов</span>
      </a>
      <nav class="cqa-nav" aria-label="Разделы">
        <a href="#/list" data-nav="list">${icon('list')}Диалоги</a>
        <a href="#/checklist" data-nav="checklist">${icon('checklist')}Чек-лист</a>
        <a href="#/appeals" data-nav="appeals">${icon('scale')}Апелляции <span class="cqa-count-pill" id="cqa-appeals-count" hidden></span></a>
      </nav>
      <div class="cqa-header__right">
        <span class="cqa-mode ${api.mode === 'live' ? 'cqa-mode--live' : ''}" title="${api.mode === 'live' ? 'Данные с сервера' : 'Встроенные демо-данные, сервер не нужен'}">
          <span class="cqa-mode__dot"></span>${api.mode === 'live' ? 'Сервер' : 'Демо-данные'}
        </span>
        <div class="cqa-seg" role="group" aria-label="Роль">
          <button type="button" data-role="operator" aria-pressed="${s.role === 'operator'}">Оператор</button>
          <button type="button" data-role="reviewer" aria-pressed="${s.role === 'reviewer'}">Проверяющий</button>
        </div>
        <button type="button" class="cqa-btn cqa-btn--ghost cqa-btn--icon" id="cqa-theme" aria-label="Сменить тему">${icon(s.theme === 'dark' ? 'sun' : 'moon')}</button>
      </div>
    </header>
    <main class="cqa-main" id="cqa-main"></main>`;

  root.querySelectorAll('[data-role]').forEach((b) => b.addEventListener('click', () => {
    store.set({ role: b.dataset.role });
    root.querySelectorAll('[data-role]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.role === b.dataset.role)));
  }));
  root.querySelector('#cqa-theme').addEventListener('click', () => {
    const theme = store.get().theme === 'dark' ? 'light' : 'dark';
    store.set({ theme });
    root.dataset.theme = theme;
    document.body.style.background = theme === 'dark' ? '#0e1113' : '#f5f6f8';
    root.querySelector('#cqa-theme').innerHTML = icon(theme === 'dark' ? 'sun' : 'moon');
  });
  document.body.style.background = s.theme === 'dark' ? '#0e1113' : '#f5f6f8';
}

export async function refreshAppealsCount() {
  try {
    const open = await api.listAppeals('open');
    const pill = root.querySelector('#cqa-appeals-count');
    if (!pill) return;
    pill.hidden = !open.length;
    pill.textContent = open.length;
  } catch { /* не критично */ }
}

async function route() {
  const hash = location.hash || '#/list';
  const r = routes.find((x) => x.re.test(hash)) || routes.at(-1);
  const m = hash.match(r.re);
  root.querySelectorAll('[data-nav]').forEach((a) => { if (a.dataset.nav === r.nav) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  if (current?.destroy) current.destroy();
  current = r.view;
  const main = root.querySelector('#cqa-main');
  main.innerHTML = '';
  try {
    await r.view.render(main, r.params ? r.params(m) : {});
  } catch (err) {
    console.error(err);
    main.innerHTML = `<div class="cqa-empty"><div class="cqa-empty__icon">${icon('alert')}</div><h2>Не удалось загрузить</h2><p>${esc(err.message)}</p><a class="cqa-btn" href="#/list">К списку диалогов</a></div>`;
  }
  refreshAppealsCount();
}

shell();
window.addEventListener('hashchange', route);
route();

// Окно «Данные»: переключение наборов, загрузка нового набора (API организаторов, файл, JSON, свой API)
// и выгрузка (results.json для организаторов, Excel, сам набор). Система не привязана к выданному DEV.
import { api } from '../api.js';
import { CONFIG } from '../config.js';
import { store } from '../store.js';
import { icon } from '../icons.js';
import { esc, toast } from '../utils.js';
import { openModal } from '../ui.js';

const SOURCES = [
  ['org', 'API организаторов'],
  ['file', 'Файл JSON'],
  ['json', 'Вставить JSON'],
  ['url', 'Свой API'],
];
const SAMPLE = '{"records": [{"conversation_id": "my-001", "language": "RU", "transcript": [\n  {"timestamp": "00:01", "speaker": "client", "text": "Здравствуйте, не могу найти отчёт"},\n  {"timestamp": "00:40", "speaker": "operator", "text": "Здравствуйте! Меня зовут Анна…"}\n]}]}';

const plural = (n, one, few, many) => (n % 10 === 1 && n % 100 !== 11 ? one : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? few : many);

export function openDataModal({ onChange } = {}) {
  const live = !!api.listDatasets;
  const g = store.get().groupId;
  const q = (path, params) => `${CONFIG.apiBase}${path}?${new URLSearchParams(params)}`;
  const m = openModal({
    title: 'Данные',
    sub: 'Наборы диалогов: открыть, загрузить новый, выгрузить результаты',
    wide: true,
    body: `
      <section class="cqa-data__sec"><h3>Наборы</h3><ul class="cqa-data__sets" id="cqa-sets"><li class="cqa-faint">${live ? 'Загрузка…' : 'Доступно при работе с сервером'}</li></ul></section>
      <section class="cqa-data__sec"><h3>Загрузить набор</h3>
        <div class="cqa-seg cqa-data__src" role="group" aria-label="Источник">${SOURCES.map(([k, t], i) => `<button type="button" data-src="${k}" aria-pressed="${i === 0}">${t}</button>`).join('')}</div>
        <div class="cqa-data__pane" data-pane="org"><label class="cqa-label" for="cqa-d-org">group_id набора</label><input class="cqa-input" id="cqa-d-org" placeholder="DEV или контрольный group_id" autocomplete="off"></div>
        <div class="cqa-data__pane" data-pane="file" hidden><label class="cqa-label" for="cqa-d-file">Файл .json</label><input class="cqa-input" type="file" id="cqa-d-file" accept=".json,application/json"></div>
        <div class="cqa-data__pane" data-pane="json" hidden><label class="cqa-label" for="cqa-d-json">JSON набора</label><textarea class="cqa-textarea cqa-data__json" id="cqa-d-json" spellcheck="false" placeholder="${esc(SAMPLE)}"></textarea></div>
        <div class="cqa-data__pane" data-pane="url" hidden><label class="cqa-label" for="cqa-d-url">Адрес (GET, ответ JSON)</label><input class="cqa-input" id="cqa-d-url" placeholder="https://example.com/dialogs.json" autocomplete="off"></div>
        <div class="cqa-data__pane" data-pane="name" hidden><label class="cqa-label" for="cqa-d-name">Название набора</label><input class="cqa-input" id="cqa-d-name" placeholder="например TEST-10 (по умолчанию — из файла или дата)" autocomplete="off"></div>
        <p class="cqa-faint cqa-data__hint">Формат: ответ API организаторов, <code>{"records": [...]}</code>, массив диалогов или один диалог. Диалог — <code>transcript: [{timestamp, speaker, text}]</code>; id, язык и канал дополняются сами.</p>
        <div class="cqa-data__go"><button type="button" class="cqa-btn cqa-btn--primary" id="cqa-d-go" ${live ? '' : 'disabled'}>${icon('download', 'cqa-ico--sm')}Загрузить</button><span id="cqa-d-res" role="status"></span></div>
      </section>
      <section class="cqa-data__sec"><h3>Выгрузить набор «${esc(g)}»</h3>
        <div class="cqa-data__out">
          <a class="cqa-btn" href="${q('/results/export', { group_id: g })}" download>${icon('file', 'cqa-ico--sm')}Результаты для организаторов (JSON)</a>
          <a class="cqa-btn" href="${q('/results/export.csv', { group_id: g })}" download>${icon('file', 'cqa-ico--sm')}Таблица для Excel (CSV)</a>
          <a class="cqa-btn" href="${CONFIG.apiBase}/datasets/${encodeURIComponent(g)}/export" download>${icon('file', 'cqa-ico--sm')}Диалоги набора (JSON)</a>
        </div>
      </section>`,
  });
  const el = m.el;
  let src = 'org';
  const showPanes = () => {
    el.querySelectorAll('[data-pane]').forEach((p) => { p.hidden = p.dataset.pane === 'name' ? src === 'org' : p.dataset.pane !== src; });
    el.querySelectorAll('[data-src]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.src === src)));
  };
  el.querySelectorAll('[data-src]').forEach((b) => b.addEventListener('click', () => { src = b.dataset.src; showPanes(); }));

  const open = async (groupId) => { store.set({ groupId }); m.close(); await onChange?.(); };
  const drawSets = async () => {
    if (!live) return;
    const sets = await api.listDatasets();
    const cur = store.get().groupId;
    el.querySelector('#cqa-sets').innerHTML = sets.length ? sets.map((s) => `<li><button type="button" class="cqa-data__set ${s.group_id === cur ? 'is-current' : ''}" data-set="${esc(s.group_id)}">
        <b>${esc(s.group_id)}</b><span>${s.count} ${plural(s.count, 'диалог', 'диалога', 'диалогов')} · оценено ${s.evaluated}</span>${s.group_id === cur ? '<em>открыт</em>' : `<em>${icon('right', 'cqa-ico--sm')}</em>`}</button></li>`).join('')
      : '<li class="cqa-faint">Наборов пока нет</li>';
    el.querySelectorAll('[data-set]').forEach((b) => b.addEventListener('click', () => open(b.dataset.set)));
  };
  drawSets().catch((e) => toast(e.message, 'bad'));

  el.querySelector('#cqa-d-go').addEventListener('click', async (e) => {
    const b = e.currentTarget; const res = el.querySelector('#cqa-d-res');
    const name = el.querySelector('#cqa-d-name').value.trim() || undefined;
    res.textContent = ''; res.className = '';
    let call;
    if (src === 'org') {
      const gid = el.querySelector('#cqa-d-org').value.trim();
      if (!gid) { res.textContent = 'Укажите group_id'; res.className = 'cqa-bad-text'; return; }
      call = () => api.importDataset(gid).then((r) => ({ ...r, group_id: gid }));
    } else if (src === 'file') {
      const f = el.querySelector('#cqa-d-file').files[0];
      if (!f) { res.textContent = 'Выберите файл'; res.className = 'cqa-bad-text'; return; }
      call = async () => api.uploadDataset(await f.text(), name || f.name.replace(/\.json$/i, ''));
    } else if (src === 'json') {
      const t = el.querySelector('#cqa-d-json').value.trim();
      if (!t) { res.textContent = 'Вставьте JSON'; res.className = 'cqa-bad-text'; return; }
      call = () => api.uploadDataset(t, name);
    } else {
      const u = el.querySelector('#cqa-d-url').value.trim();
      if (!/^https?:\/\//i.test(u)) { res.textContent = 'Адрес должен начинаться с http:// или https://'; res.className = 'cqa-bad-text'; return; }
      call = () => api.fetchUrlDataset(u, name);
    }
    b.disabled = true; b.innerHTML = '<span class="cqa-spin"></span>Загрузка';
    try {
      const r = await call();
      const extra = [r.skipped?.length ? `пропущено ${r.skipped.length}: ${r.skipped[0].error}` : '', r.renamed?.length ? `переименовано ${r.renamed.length} (id уже заняты другим набором)` : ''].filter(Boolean).join('; ');
      toast(`Набор ${r.group_id}: загружено ${r.imported} ${plural(r.imported, 'диалог', 'диалога', 'диалогов')}${extra ? `, ${extra}` : ''}. Нажмите «Оценить все»`);
      await open(r.group_id);
    } catch (er) {
      res.textContent = er.message; res.className = 'cqa-bad-text';
      b.disabled = false; b.innerHTML = `${icon('download', 'cqa-ico--sm')}Загрузить`;
    }
  });
  showPanes();
}

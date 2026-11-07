// Экран «Диалоги»: одна строка сводки, один переключатель риска, спокойная таблица из трёх колонок.
import { api } from '../api.js';
import { store } from '../store.js';
import { icon } from '../icons.js';
import { esc, fmt2, fmtDuration, CHANNEL, GROSS, scoreTone, debounce, toast } from '../utils.js';

let rows = [];
let cursor = 0;
let keyHandler = null;
let pollTimer = null;

const isRisk = (r) => !!r.evaluation?.gross_error;
const isAttention = (r) => !!(r.evaluation?.needs_review || r.appeal_status === 'open');

const riskRank = (r) => {
  if (!r.evaluation) return 3;
  if (isRisk(r)) return 0;
  if (isAttention(r)) return 1;
  if ((r.evaluation.final_total ?? 100) < 60) return 2;
  return 4;
};

function applyFilters(all) {
  const f = store.get().filters;
  const q = f.q.trim().toLowerCase();
  const view = f.view || 'all';
  let out = all.filter((r) =>
    (view === 'all' || (view === 'risk' && isRisk(r)) || (view === 'attention' && isAttention(r))) &&
    (!f.lang || r.language === f.lang) &&
    (!f.channel || r.channel === f.channel) &&
    (!q || r.conversation_id.toLowerCase().includes(q) || (r.operator_name || '').toLowerCase().includes(q)));
  const sort = store.get().sort;
  out = [...out].sort((a, b) => {
    if (sort === 'score') return (a.evaluation?.final_total ?? 999) - (b.evaluation?.final_total ?? 999);
    if (sort === 'id') return a.conversation_id.localeCompare(b.conversation_id);
    return riskRank(a) - riskRank(b) || (a.evaluation?.final_total ?? 999) - (b.evaluation?.final_total ?? 999);
  });
  return out;
}

function statusHtml(r) {
  const e = r.evaluation;
  if (!e) return `<button class="cqa-btn cqa-btn--sm" data-eval="${esc(r.conversation_id)}">${icon('sparkle')}Оценить</button>`;
  if (e.gross_error) return `<span class="cqa-status cqa-status--bad">${esc((e.gross_types || []).map((t) => GROSS[t] || t).join(', ') || 'Грубое нарушение')}</span>`;
  if (r.appeal_status === 'open') return `<span class="cqa-status cqa-status--info">Апелляция на рассмотрении</span>`;
  if (e.needs_review) return `<span class="cqa-status cqa-status--warn">Требует проверки</span>`;
  if (r.appeal_status === 'resolved') return `<span class="cqa-status">Апелляция рассмотрена</span>`;
  return `<span class="cqa-status cqa-status--muted">Без замечаний</span>`;
}

function rowHtml(r, i) {
  const e = r.evaluation;
  const total = e?.final_total;
  const sub = [r.operator_name, CHANNEL[r.channel] || r.channel, r.language, fmtDuration(r.duration_sec)].filter(Boolean).map(esc).join(', ');
  return `
    <tr data-id="${esc(r.conversation_id)}" class="${i === cursor ? 'is-cursor' : ''}">
      <td><div class="cqa-cell-id">${esc(r.conversation_id)}</div><div class="cqa-cell-sub">${sub}</div></td>
      <td class="ta-r">${e ? `<span class="cqa-total-cell cqa-total-cell--${scoreTone(total)}">${fmt2(total)}</span>` : '<span class="cqa-faint">—</span>'}</td>
      <td>${statusHtml(r)}</td>
      <td class="cqa-chev-cell">${icon('right')}</td>
    </tr>`;
}

function renderTable(main) {
  const list = applyFilters(rows);
  cursor = Math.min(cursor, Math.max(0, list.length - 1));
  const body = main.querySelector('#cqa-tbody');
  body.innerHTML = list.length ? list.map(rowHtml).join('') : `<tr><td colspan="4"><div class="cqa-empty"><b>Ничего не найдено</b><span>Измените фильтры или очистите поиск</span></div></td></tr>`;
  body.querySelectorAll('tr[data-id]').forEach((tr) => tr.addEventListener('click', (ev) => {
    if (ev.target.closest('[data-eval]')) return;
    location.hash = `#/c/${encodeURIComponent(tr.dataset.id)}`;
  }));
  body.querySelectorAll('[data-eval]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true; b.innerHTML = '<span class="cqa-spin"></span>Оценка';
    try { await api.evaluate(b.dataset.eval); toast(`Диалог ${b.dataset.eval} оценён`); await reload(main); } catch (err) { toast(err.message, 'bad'); b.disabled = false; }
  }));
}

function renderSummary(main) {
  const ev = rows.filter((r) => r.evaluation);
  const totals = ev.map((r) => r.evaluation.final_total).filter((x) => x != null);
  const avg = totals.length ? totals.reduce((s, x) => s + x, 0) / totals.length : null;
  main.querySelector('#cqa-summary').textContent = `Оценено ${ev.length} из ${rows.length}, средний итог ${fmt2(avg)}`;
  const f = store.get().filters; const view = f.view || 'all';
  const seg = [['all', 'Все', rows.length], ['risk', 'С нарушениями', rows.filter(isRisk).length], ['attention', 'Требуют внимания', rows.filter(isAttention).length]];
  main.querySelector('#cqa-views').innerHTML = seg.map(([v, t, n]) => `<button type="button" data-view="${v}" aria-pressed="${view === v}">${t}<span class="cqa-seg__n ${v === 'risk' && n ? 'cqa-seg__n--bad' : ''}">${n}</span></button>`).join('');
  main.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => { f.view = b.dataset.view; renderSummary(main); renderTable(main); }));
}

async function reload(main) {
  rows = await api.listConversations();
  renderSummary(main); renderTable(main);
}

async function runBatch(main, btn) {
  btn.disabled = true;
  try {
    const { job_id } = await api.evaluateBatch({ conversation_ids: rows.map((r) => r.conversation_id) });
    await new Promise((resolve) => {
      pollTimer = setInterval(async () => {
        const j = await api.getJob(job_id);
        btn.innerHTML = `<span class="cqa-spin"></span>Оценено ${j.done} из ${j.total}`;
        if (j.status !== 'running') { clearInterval(pollTimer); resolve(j); }
      }, 400);
    });
    toast('Все диалоги оценены');
    await reload(main);
  } catch (err) { toast(err.message, 'bad'); }
  btn.disabled = false; btn.innerHTML = 'Оценить все';
}

export async function render(main) {
  const s = store.get(); const f = s.filters;
  main.innerHTML = `
    <div class="cqa-page-head">
      <div><h1>Диалоги</h1><p id="cqa-summary">Загрузка</p></div>
      <div class="cqa-actions">
        <input class="cqa-input" id="cqa-group" value="DEV" aria-label="Набор данных (group_id)" title="group_id набора у организаторов" style="width:110px">
        <button type="button" class="cqa-btn" id="cqa-import">Загрузить набор</button>
        <button type="button" class="cqa-btn cqa-btn--primary" id="cqa-batch">Оценить все</button>
      </div>
    </div>
    <div class="cqa-toolbar">
      <div class="cqa-seg cqa-seg--lg" id="cqa-views" role="group" aria-label="Показать"></div>
      <select class="cqa-select" id="cqa-lang" aria-label="Язык">
        <option value="">Все языки</option><option value="RU">Русский</option><option value="KZ">Қазақша</option><option value="EN">English</option>
      </select>
      <select class="cqa-select" id="cqa-chan" aria-label="Канал">
        <option value="">Чаты и звонки</option><option value="agent chat">Только чаты</option><option value="voice call">Только звонки</option>
      </select>
      <label class="cqa-search cqa-toolbar__right">${icon('search')}<input class="cqa-input" id="cqa-q" placeholder="Поиск" value="${esc(f.q)}" aria-label="Поиск по ID или оператору"></label>
    </div>
    <div class="cqa-list">
      <table class="cqa-table">
        <thead><tr><th>Диалог</th><th class="ta-r" style="width:120px">Итог</th><th style="width:38%">Статус</th><th style="width:40px"></th></tr></thead>
        <tbody id="cqa-tbody">${Array.from({ length: 5 }, () => '<tr><td colspan="4"><div class="cqa-skel" style="height:14px;width:50%"></div></td></tr>').join('')}</tbody>
      </table>
    </div>
    <p class="cqa-hint"><span class="cqa-kbd">/</span> поиск <span class="cqa-kbd">↑</span><span class="cqa-kbd">↓</span> выбор <span class="cqa-kbd">Enter</span> открыть</p>`;

  main.querySelector('#cqa-lang').value = f.lang || '';
  main.querySelector('#cqa-chan').value = f.channel || '';
  main.querySelector('#cqa-lang').addEventListener('change', (ev) => { f.lang = ev.target.value; renderTable(main); });
  main.querySelector('#cqa-chan').addEventListener('change', (ev) => { f.channel = ev.target.value; renderTable(main); });
  main.querySelector('#cqa-q').addEventListener('input', debounce((ev) => { f.q = ev.target.value; renderTable(main); }, 120));
  main.querySelector('#cqa-batch').addEventListener('click', (ev) => runBatch(main, ev.currentTarget));
  main.querySelector('#cqa-import').addEventListener('click', async (ev) => {
    const btn = ev.currentTarget; const g = main.querySelector('#cqa-group').value.trim();
    if (!g) { toast('Укажите group_id набора', 'bad'); return; }
    btn.disabled = true; btn.innerHTML = '<span class="cqa-spin"></span>Загрузка';
    try { const r = await api.importDataset(g); toast(r.note || `Загружено диалогов: ${r.imported}`); await reload(main); } catch (err) { toast(err.message, 'bad'); }
    btn.disabled = false; btn.innerHTML = 'Загрузить набор';
  });

  keyHandler = (ev) => {
    if (ev.target.matches('input, textarea, select')) { if (ev.key === 'Escape') ev.target.blur(); return; }
    const list = applyFilters(rows);
    if (ev.key === '/') { ev.preventDefault(); main.querySelector('#cqa-q').focus(); }
    else if (ev.key === 'ArrowDown' || ev.key === 'j') { ev.preventDefault(); cursor = Math.min(list.length - 1, cursor + 1); renderTable(main); main.querySelector('tr.is-cursor')?.scrollIntoView({ block: 'nearest' }); }
    else if (ev.key === 'ArrowUp' || ev.key === 'k') { ev.preventDefault(); cursor = Math.max(0, cursor - 1); renderTable(main); main.querySelector('tr.is-cursor')?.scrollIntoView({ block: 'nearest' }); }
    else if (ev.key === 'Enter' && list[cursor]) { location.hash = `#/c/${encodeURIComponent(list[cursor].conversation_id)}`; }
  };
  document.addEventListener('keydown', keyHandler);
  await reload(main);
}

export function destroy() {
  document.removeEventListener('keydown', keyHandler);
  clearInterval(pollTimer);
}

/** Порядок диалогов для навигации из карточки (тот же, что видит пользователь). */
export async function orderedIds() {
  if (!rows.length) rows = await api.listConversations();
  return applyFilters(rows).map((r) => r.conversation_id);
}

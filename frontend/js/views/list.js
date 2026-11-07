// Экран «Диалоги»: сводка, фильтры, таблица. Сортировка по риску по умолчанию.
import { api } from '../api.js';
import { store } from '../store.js';
import { icon } from '../icons.js';
import { esc, fmt2, fmtDuration, CHANNEL, GROSS, scoreBar, debounce, toast } from '../utils.js';

let rows = [];
let cursor = 0;
let keyHandler = null;
let pollTimer = null;

const riskRank = (r) => {
  const e = r.evaluation;
  if (!e) return 3;
  if (e.gross_error) return 0;
  if (e.needs_review || r.appeal_status === 'open') return 1;
  if ((e.final_total ?? e.total ?? 100) < 60) return 2;
  return 4;
};

function applyFilters(all) {
  const f = store.get().filters;
  const q = f.q.trim().toLowerCase();
  let out = all.filter((r) =>
    (!f.lang || r.language === f.lang) &&
    (!f.channel || r.channel === f.channel) &&
    (!f.risk || r.evaluation?.gross_error) &&
    (!f.review || r.evaluation?.needs_review || r.appeal_status === 'open') &&
    (!q || r.conversation_id.toLowerCase().includes(q) || (r.operator_name || '').toLowerCase().includes(q)));
  const sort = store.get().sort;
  out = [...out].sort((a, b) => {
    if (sort === 'score') return (a.evaluation?.final_total ?? 999) - (b.evaluation?.final_total ?? 999);
    if (sort === 'id') return a.conversation_id.localeCompare(b.conversation_id);
    return riskRank(a) - riskRank(b) || (a.evaluation?.final_total ?? 999) - (b.evaluation?.final_total ?? 999);
  });
  return out;
}

function stats(all) {
  const ev = all.filter((r) => r.evaluation);
  const totals = ev.map((r) => r.evaluation.final_total).filter((x) => x != null);
  const avg = totals.length ? totals.reduce((s, x) => s + x, 0) / totals.length : null;
  return {
    evaluated: ev.length,
    avg,
    gross: ev.filter((r) => r.evaluation.gross_error).length,
    review: all.filter((r) => r.evaluation?.needs_review || r.appeal_status === 'open').length,
  };
}

function rowHtml(r, i) {
  const e = r.evaluation;
  const tone = e?.gross_error ? 'danger' : (e?.needs_review || r.appeal_status === 'open') ? 'warn' : '';
  const flags = [];
  if (e?.gross_error) flags.push(`<span class="cqa-badge cqa-badge--danger">${icon('alert', 'cqa-ico--sm')}${esc((e.gross_types || []).map((t) => GROSS[t] || t).join(', ') || 'Грубая ошибка')}</span>`);
  if (e?.needs_review) flags.push(`<span class="cqa-badge cqa-badge--warn">Проверить</span>`);
  if (r.appeal_status === 'open') flags.push(`<span class="cqa-badge cqa-badge--info">Апелляция</span>`);
  if (r.appeal_status === 'resolved') flags.push(`<span class="cqa-badge">Решено</span>`);
  return `
    <tr data-id="${esc(r.conversation_id)}" data-i="${i}" class="${i === cursor ? 'is-cursor' : ''}">
      <td class="cqa-risk"><div class="cqa-risk__bar ${tone ? `cqa-risk__bar--${tone}` : ''}"></div></td>
      <td><div class="cqa-cell-id">${esc(r.conversation_id)}</div><div class="cqa-cell-sub">${esc(r.operator_name || '—')}</div></td>
      <td>${esc(r.language)}</td>
      <td class="hide-sm">${icon(r.channel === 'voice call' ? 'phone' : 'chat', 'cqa-ico--sm')} ${esc(CHANNEL[r.channel] || r.channel)}</td>
      <td class="ta-r hide-sm cqa-num">${r.turns}</td>
      <td class="ta-r hide-sm cqa-num">${fmtDuration(r.duration_sec)}</td>
      <td>${e ? scoreBar(e.final_total) : `<button class="cqa-btn cqa-btn--sm" data-eval="${esc(r.conversation_id)}">${icon('sparkle')}Оценить</button>`}</td>
      <td><div class="cqa-flags">${flags.join('') || '<span class="cqa-faint">—</span>'}</div></td>
    </tr>`;
}

function renderTable(main) {
  const list = applyFilters(rows);
  cursor = Math.min(cursor, Math.max(0, list.length - 1));
  const body = main.querySelector('#cqa-tbody');
  body.innerHTML = list.length ? list.map(rowHtml).join('') : `<tr><td colspan="8"><div class="cqa-empty"><div class="cqa-empty__icon">${icon('search')}</div><b>Ничего не найдено</b><span>Измените фильтры или очистите поиск</span></div></td></tr>`;
  main.querySelector('#cqa-shown').textContent = `Показано ${list.length} из ${rows.length}`;
  body.querySelectorAll('tr[data-id]').forEach((tr) => tr.addEventListener('click', (ev) => {
    if (ev.target.closest('[data-eval]')) return;
    location.hash = `#/c/${encodeURIComponent(tr.dataset.id)}`;
  }));
  body.querySelectorAll('[data-eval]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true; b.innerHTML = '<span class="cqa-spin"></span>Оценка';
    try { await api.evaluate(b.dataset.eval); toast(`Диалог ${b.dataset.eval} оценён`); await reload(main); } catch (err) { toast(err.message, 'bad'); b.disabled = false; }
  }));
  return list;
}

function renderStats(main) {
  const s = stats(rows);
  const f = store.get().filters;
  main.querySelector('#cqa-stats').innerHTML = `
    <div class="cqa-stat cqa-stat--plain"><div class="cqa-stat__label">Оценено</div><div class="cqa-stat__value">${s.evaluated}<span class="cqa-faint" style="font-size:16px;font-weight:400"> из ${rows.length}</span></div><div class="cqa-stat__hint">диалогов в наборе</div></div>
    <div class="cqa-stat cqa-stat--plain"><div class="cqa-stat__label">Средний итог</div><div class="cqa-stat__value">${fmt2(s.avg)}</div><div class="cqa-stat__hint">по оценённым, из 100</div></div>
    <button type="button" class="cqa-stat cqa-stat--danger" data-toggle="risk" aria-pressed="${f.risk}"><div class="cqa-stat__label">Грубые ошибки</div><div class="cqa-stat__value">${s.gross}</div><div class="cqa-stat__hint">${f.risk ? 'показаны только они' : 'нажмите, чтобы отфильтровать'}</div></button>
    <button type="button" class="cqa-stat cqa-stat--warn" data-toggle="review" aria-pressed="${f.review}"><div class="cqa-stat__label">Требуют внимания</div><div class="cqa-stat__value">${s.review}</div><div class="cqa-stat__hint">низкая уверенность или апелляция</div></button>`;
  main.querySelectorAll('[data-toggle]').forEach((b) => b.addEventListener('click', () => {
    const key = b.dataset.toggle;
    store.get().filters[key] = !store.get().filters[key];
    renderStats(main); renderFilters(main); renderTable(main);
  }));
}

function renderFilters(main) {
  const f = store.get().filters;
  const langs = ['', 'RU', 'KZ', 'EN'];
  const count = (pred) => rows.filter(pred).length;
  main.querySelector('#cqa-lang').innerHTML = langs.map((l) => `<button type="button" class="cqa-fchip" data-lang="${l}" aria-pressed="${f.lang === l}">${l || 'Все языки'} <span class="cqa-count">${count((r) => !l || r.language === l)}</span></button>`).join('');
  main.querySelector('#cqa-chan').innerHTML = [['', 'Все каналы'], ['agent chat', 'Чаты'], ['voice call', 'Звонки']].map(([v, t]) => `<button type="button" class="cqa-fchip" data-chan="${v}" aria-pressed="${f.channel === v}">${t}</button>`).join('');
  main.querySelectorAll('[data-lang]').forEach((b) => b.addEventListener('click', () => { f.lang = b.dataset.lang; renderFilters(main); renderTable(main); }));
  main.querySelectorAll('[data-chan]').forEach((b) => b.addEventListener('click', () => { f.channel = b.dataset.chan; renderFilters(main); renderTable(main); }));
}

async function reload(main) {
  rows = await api.listConversations();
  renderStats(main); renderFilters(main); renderTable(main);
}

async function runBatch(main, btn) {
  const ids = rows.map((r) => r.conversation_id);
  btn.disabled = true;
  try {
    const { job_id } = await api.evaluateBatch({ conversation_ids: ids });
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
  btn.disabled = false; btn.innerHTML = `${icon('sparkle')}Оценить все`;
}

export async function render(main) {
  const s = store.get();
  main.innerHTML = `
    <div class="cqa-page-head">
      <div>
        <h1>Диалоги</h1>
        <p>Сначала показаны диалоги с грубыми ошибками и требующие внимания</p>
      </div>
      <div class="cqa-actions">
        <input class="cqa-input" id="cqa-group" value="DEV" aria-label="Набор данных (group_id)" style="width:120px" title="group_id набора у организаторов">
        <button type="button" class="cqa-btn" id="cqa-import">${icon('download')}Загрузить набор</button>
        <button type="button" class="cqa-btn cqa-btn--primary" id="cqa-batch">${icon('sparkle')}Оценить все</button>
      </div>
    </div>
    <div class="cqa-stats" id="cqa-stats"></div>
    <div class="cqa-filters">
      <label class="cqa-search">${icon('search')}<input class="cqa-input" id="cqa-q" placeholder="ID диалога или оператор" value="${esc(s.filters.q)}" aria-label="Поиск"></label>
      <span class="cqa-filters__sep"></span>
      <div class="cqa-actions" id="cqa-lang"></div>
      <span class="cqa-filters__sep"></span>
      <div class="cqa-actions" id="cqa-chan"></div>
      <div class="cqa-filters__right">
        <label class="cqa-faint" for="cqa-sort">Сортировка</label>
        <select class="cqa-select" id="cqa-sort">
          <option value="risk" ${s.sort === 'risk' ? 'selected' : ''}>Сначала риски</option>
          <option value="score" ${s.sort === 'score' ? 'selected' : ''}>По итогу, по возрастанию</option>
          <option value="id" ${s.sort === 'id' ? 'selected' : ''}>По ID</option>
        </select>
      </div>
    </div>
    <div class="cqa-table-wrap">
      <table class="cqa-table">
        <thead><tr>
          <th class="cqa-risk"></th><th>Диалог</th><th>Язык</th><th class="hide-sm">Канал</th>
          <th class="ta-r hide-sm">Реплик</th><th class="ta-r hide-sm">Длительность</th><th style="width:180px">Итог</th><th>Отметки</th>
        </tr></thead>
        <tbody id="cqa-tbody">${Array.from({ length: 6 }, () => '<tr><td colspan="8"><div class="cqa-skel" style="height:14px;width:60%"></div></td></tr>').join('')}</tbody>
      </table>
      <div class="cqa-table-foot"><span id="cqa-shown"></span><span style="margin-left:auto"><span class="cqa-kbd">/</span> поиск</span><span><span class="cqa-kbd">↑</span><span class="cqa-kbd">↓</span> выбор</span><span><span class="cqa-kbd">Enter</span> открыть</span></div>
    </div>`;

  main.querySelector('#cqa-q').addEventListener('input', debounce((ev) => { store.get().filters.q = ev.target.value; renderTable(main); }, 120));
  main.querySelector('#cqa-sort').addEventListener('change', (ev) => { store.set({ sort: ev.target.value }); renderTable(main); });
  main.querySelector('#cqa-batch').addEventListener('click', (ev) => runBatch(main, ev.currentTarget));
  main.querySelector('#cqa-import').addEventListener('click', async (ev) => {
    const btn = ev.currentTarget; const g = main.querySelector('#cqa-group').value.trim();
    if (!g) { toast('Укажите group_id набора', 'bad'); return; }
    btn.disabled = true; btn.innerHTML = '<span class="cqa-spin"></span>Загрузка';
    try { const r = await api.importDataset(g); toast(r.note || `Загружено диалогов: ${r.imported}`); await reload(main); } catch (err) { toast(err.message, 'bad'); }
    btn.disabled = false; btn.innerHTML = `${icon('download')}Загрузить набор`;
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

// Экран «Чек-лист»: редактор критериев, версии, применение к диалогам и сравнение «было → стало».
import { api } from '../api.js';
import { store } from '../store.js';
import { icon } from '../icons.js';
import { esc, fmt2, fmtMoney, toast } from '../utils.js';
import { openModal, relTime } from '../ui.js';
import { refreshShell } from '../app.js';

const S = { main: null, active: null, draft: null, versions: [], open: new Set(), diff: null, lastSaved: null };
const TYPES = { binary: 'Да / Нет', scale: 'Шкала', flag: 'Флаг без баллов' };
const pad2 = (n) => String(n).padStart(2, '0');
const clone = (x) => JSON.parse(JSON.stringify(x));
const readOnly = () => store.get().role !== 'qa';

function weightSum() { return S.draft.criteria.filter((c) => c.type !== 'flag').reduce((s, c) => s + (Number(c.weight) || 0), 0); }
const isDirty = () => JSON.stringify(stripMeta(S.draft)) !== JSON.stringify(stripMeta(S.active));
const stripMeta = (cl) => ({ ...cl, version: 0, is_active: true, created_at: null });

function problems() {
  const p = [];
  S.draft.criteria.forEach((c, i) => {
    const n = pad2(i + 1);
    if (!c.title.trim()) p.push(`${n}: нет названия`);
    if (!c.description.trim()) p.push(`${n}: нет правила оценки`);
    if (c.type !== 'flag' && !(Number(c.weight) > 0)) p.push(`${n}: вес должен быть больше 0`);
    if (c.na_allowed && !(c.na_condition || '').trim()) p.push(`${n}: опишите, когда ставится N/A`);
    if (c.type === 'scale') {
      const vals = (c.scale || []).map((s) => Number(s.value));
      if (vals.length < 2) p.push(`${n}: в шкале нужно минимум две ступени`);
      if (new Set(vals).size !== vals.length) p.push(`${n}: ступени шкалы повторяются`);
    }
  });
  if (!S.draft.criteria.some((c) => c.type !== 'flag')) p.push('Нужен хотя бы один критерий с весом');
  return p;
}

function critCard(c, i) {
  const open = S.open.has(c.id); const ro = readOnly() ? 'disabled' : '';
  const secs = S.draft.sections.map((s) => `<option value="${s.id}" ${s.id === c.section_id ? 'selected' : ''}>${esc(s.title)}</option>`).join('');
  const scale = c.type === 'scale' ? `<div class="cqa-field span4"><span class="cqa-label">Ступени шкалы</span><div class="cqa-steps">${(c.scale || []).map((s, k) => `<div class="cqa-step"><input class="cqa-input cqa-input--sm" type="number" min="0" step="1" value="${s.value}" data-f="step-value" data-k="${k}" aria-label="Значение ступени" ${ro}><input class="cqa-input cqa-input--sm" value="${esc(s.description)}" data-f="step-desc" data-k="${k}" aria-label="Описание ступени" ${ro}><button type="button" class="cqa-btn cqa-btn--ghost cqa-btn--icon cqa-btn--sm" data-act="step-del" data-k="${k}" aria-label="Удалить ступень" ${ro}>${icon('x', 'cqa-ico--sm')}</button></div>`).join('')}</div>${readOnly() ? '' : '<button type="button" class="cqa-link cqa-link--accent" data-act="step-add" style="align-self:flex-start;margin-top:4px">+ Ступень</button>'}</div>` : '';
  return `
    <article class="cqa-card cqa-ce ${open ? 'is-open' : ''} ${c._new ? 'is-new' : ''}" data-cid="${c.id}">
      <button type="button" class="cqa-ce__head" data-act="toggle" aria-expanded="${open}"><span class="cqa-ce__n">${pad2(i + 1)}</span><span class="cqa-ce__t">${esc(c.title || 'Новый критерий')}</span><span class="cqa-badge">${TYPES[c.type]}${c.na_allowed ? ', N/A' : ''}</span><span class="cqa-ce__w">${c.type === 'flag' ? '<small>без баллов</small>' : `${c.weight || 0} <small>вес</small>`}</span><span class="cqa-cr__chev">${icon('down', 'cqa-ico--sm')}</span></button>
      <div class="cqa-ce__wrap"><div><div class="cqa-ce__body">
        <div class="cqa-field span2"><label>Название</label><input class="cqa-input" value="${esc(c.title)}" data-f="title" ${ro}></div>
        <div class="cqa-field"><label>Тип ответа</label><select class="cqa-select" data-f="type" ${ro}>${Object.entries(TYPES).map(([v, t]) => `<option value="${v}" ${v === c.type ? 'selected' : ''}>${t}</option>`).join('')}</select></div>
        <div class="cqa-field"><label>Вес</label><input class="cqa-input" type="number" min="0" step="1" value="${c.type === 'flag' ? 0 : c.weight}" data-f="weight" ${c.type === 'flag' || readOnly() ? 'disabled' : ''}></div>
        <div class="cqa-field span4"><label>Правило оценки для модели</label><textarea class="cqa-textarea" data-f="description" ${ro}>${esc(c.description)}</textarea></div>
        <div class="cqa-field span2"><label>Раздел</label><select class="cqa-select" data-f="section_id" ${ro}>${secs}</select></div>
        <div class="cqa-field span2"><span class="cqa-label">Допускается N/A</span><div class="cqa-na-row"><label class="cqa-switch"><input type="checkbox" data-f="na_allowed" ${c.na_allowed ? 'checked' : ''} ${c.type === 'flag' || readOnly() ? 'disabled' : ''} aria-label="Допускается N/A"><span></span></label><span class="cqa-muted">${c.na_allowed ? 'Да' : 'Нет'}</span></div></div>
        ${c.na_allowed ? `<div class="cqa-field span4"><label>Когда ставится N/A</label><input class="cqa-input" value="${esc(c.na_condition || '')}" data-f="na_condition" placeholder="Например: чат закончился до того, как клиент сформулировал запрос" ${ro}></div>` : ''}
        ${scale}
        ${c.type === 'flag' ? '<div class="cqa-alert cqa-alert--warn span4">' + icon('info', 'cqa-ico--sm') + '<div><b>Флаг не влияет на балл</b><span class="cqa-alert__text">Отмечается отдельно, как грубые нарушения</span></div></div>' : ''}
        ${readOnly() ? '' : `<div class="cqa-ce__foot"><span class="cqa-faint" style="font-size:12px">ID ${esc(c.id)}</span><button type="button" class="cqa-btn cqa-btn--sm cqa-btn--danger" data-act="del">${icon('trash', 'cqa-ico--sm')}Удалить критерий</button></div>`}
      </div></div></div>
    </article>`;
}

function listHtml() {
  let i = 0;
  return S.draft.sections.map((s) => {
    const own = S.draft.criteria.filter((c) => c.section_id === s.id);
    if (!own.length) return '';
    return `<div class="cqa-cl__sec">${esc(s.title)}</div>${own.map((c) => critCard(c, S.draft.criteria.indexOf(c))).join('')}`;
  }).join('') + (S.draft.criteria.filter((c) => !S.draft.sections.some((s) => s.id === c.section_id)).map((c) => critCard(c, S.draft.criteria.indexOf(c))).join(''));
}

function sideHtml() {
  const sum = weightSum(); const p = problems(); const dirty = isDirty();
  return `
    <section class="cqa-card cqa-card__body">
      <div class="cqa-sum ${sum === 100 ? '' : 'cqa-sum--off'}"><span class="cqa-muted">Сумма весов</span><b>${sum}</b></div>
      <p class="cqa-faint" style="font-size:12px;margin-top:6px">${sum === 100 ? '' : 'Итог всё равно нормируется к 100'}</p>
    </section>
    ${p.length ? `<div class="cqa-alert cqa-alert--warn">${icon('alert', 'cqa-ico--sm')}<div><b>Нужно исправить перед сохранением</b><span class="cqa-alert__text">${p.map(esc).join('<br>')}</span></div></div>` : dirty ? `<div class="cqa-alert cqa-alert--ok">${icon('check', 'cqa-ico--sm')}<div><b>Есть несохранённые изменения</b><span class="cqa-alert__text">Сохраните новую версию и примените её к диалогам</span></div></div>` : ''}
    <section class="cqa-card"><div class="cqa-card__head"><h3>Версии</h3></div><div class="cqa-card__body"><ul class="cqa-versions">${[...S.versions].reverse().map((v) => `<li><span>Версия ${v.version}${v.is_active ? ' <span class="cqa-badge cqa-badge--ok">активна</span>' : ''}</span><span class="cqa-faint">${v.criteria.length} крит., ${esc(relTime(v.created_at))}</span></li>`).join('')}</ul></div></section>
    ${S.diff ? diffHtml() : ''}`;
}

// Почему изменился итог: ответы по критериям (Да → Нет) или только веса/состав чек-листа.
function whyHtml(r) {
  const to = new Map((S.active?.criteria || []).map((c) => [c.id, c]));
  const from = new Map((S.diff.prev?.criteria || []).map((c) => [c.id, c]));
  const ans = (c, a) => (!c ? String(a) : a === 'na' ? 'N/A' : c.type === 'scale' ? String(a) : a === 'yes' ? 'Да' : 'Нет');
  const parts = (r.changes || []).map((ch) => { const c = to.get(ch.criterion_id) || from.get(ch.criterion_id); return `${esc(c?.title || ch.criterion_id)}: ${ans(c, ch.from)} → ${ans(c, ch.to)}`; });
  Object.keys(r.added || {}).forEach((id) => parts.push(`новый критерий «${esc(to.get(id)?.title || id)}»: ${ans(to.get(id), r.added[id])}`));
  Object.keys(r.removed || {}).forEach((id) => parts.push(`удалён «${esc(from.get(id)?.title || id)}»`));
  if (!parts.length && r.from_total !== r.to_total) {
    const w = [...to.values()].filter((c) => from.has(c.id) && (from.get(c.id).weight || 0) !== (c.weight || 0)).map((c) => `${esc(c.title)} ${from.get(c.id).weight}→${c.weight}`);
    parts.push(w.length ? `ответы те же, изменились веса: ${w.join(', ')}` : 'ответы те же, изменились веса');
  }
  return parts.length ? `<div class="cqa-diff__why">${parts.join('; ')}</div>` : '';
}

function diffHtml() {
  const rows = S.diff.rows.filter((r) => r.from_total !== r.to_total || r.changed_criteria.length);
  return `<section class="cqa-card cqa-enter"><div class="cqa-card__head"><h3>Было → стало</h3><span class="cqa-faint" style="margin-left:auto;font-size:12px">версия ${S.diff.from} → ${S.diff.to}</span></div>
    <div class="cqa-card__body" style="padding-top:6px">${rows.length ? `<table class="cqa-diff"><thead><tr><th>Диалог</th><th class="r">Было</th><th class="r">Стало</th></tr></thead><tbody>${rows.map((r) => {
      const d = (r.to_total ?? 0) - (r.from_total ?? 0);
      return `<tr><td><a href="#/dialogs/${encodeURIComponent(r.conversation_id)}">${esc(r.conversation_id)}</a>${whyHtml(r)}</td><td class="r">${fmt2(r.from_total)}</td><td class="r">${fmt2(r.to_total)} <span class="${d > 0 ? 'cqa-delta--up' : d < 0 ? 'cqa-delta--down' : 'cqa-faint'}">${d > 0 ? '+' : ''}${d ? fmt2(d) : '0'}</span></td></tr>`;
    }).join('')}</tbody></table>` : '<p class="cqa-muted">Итоги не изменились</p>'}
    <p class="cqa-faint" style="font-size:12px;margin-top:10px">Изменились итоги у ${rows.length} из ${S.diff.rows.length} диалогов</p></div></section>`;
}

function draw() {
  S.main.querySelector('#cqa-cl-list').innerHTML = listHtml();
  drawSide();
}
function drawSide() {
  S.main.querySelector('#cqa-cl-side').innerHTML = sideHtml();
  const save = S.main.querySelector('#cqa-save');
  if (save) save.disabled = !isDirty() || problems().length > 0;
}

function crit(cid) { return S.draft.criteria.find((c) => c.id === cid); }

function bind() {
  const list = S.main.querySelector('#cqa-cl-list');
  list.addEventListener('click', (e) => {
    const card = e.target.closest('[data-cid]'); if (!card) return;
    const c = crit(card.dataset.cid); const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'toggle') { if (S.open.has(c.id)) S.open.delete(c.id); else S.open.add(c.id); card.classList.toggle('is-open'); card.querySelector('.cqa-ce__head').setAttribute('aria-expanded', String(card.classList.contains('is-open'))); }
    else if (act === 'del') {
      const m = openModal({ title: 'Удалить критерий?', sub: `«${esc(c.title)}» пропадёт из новой версии. Старые оценки останутся в истории`, foot: '<button type="button" class="cqa-btn" data-close>Отмена</button><button type="button" class="cqa-btn cqa-btn--primary" id="cqa-del-ok">Удалить</button>' });
      m.el.querySelector('#cqa-del-ok').addEventListener('click', () => { S.draft.criteria = S.draft.criteria.filter((x) => x !== c); m.close(); draw(); toast('Критерий удалён из черновика'); });
    } else if (act === 'step-add') { c.scale.push({ value: 0, description: '' }); draw(); }
    else if (act === 'step-del') { c.scale.splice(Number(e.target.closest('[data-k]').dataset.k), 1); draw(); }
  });
  const onField = (e) => {
    const f = e.target.dataset.f; if (!f) return;
    const c = crit(e.target.closest('[data-cid]').dataset.cid);
    if (f === 'step-value') c.scale[Number(e.target.dataset.k)].value = Number(e.target.value);
    else if (f === 'step-desc') c.scale[Number(e.target.dataset.k)].description = e.target.value;
    else if (f === 'weight') c.weight = Number(e.target.value);
    else if (f === 'na_allowed') { c.na_allowed = e.target.checked; if (!c.na_allowed) c.na_condition = null; draw(); return; }
    else if (f === 'type') {
      c.type = e.target.value;
      if (c.type === 'flag') { c.weight = 0; c.na_allowed = false; c.scale = null; c.flag_types = c.flag_types || []; }
      if (c.type === 'scale' && !c.scale?.length) c.scale = [{ value: 10, description: 'Полностью выполнено' }, { value: 5, description: 'Частично' }, { value: 0, description: 'Не выполнено' }];
      if (c.type === 'binary') c.scale = null;
      if (c.type !== 'flag' && !c.weight) c.weight = 10;
      draw(); return;
    } else if (f === 'section_id') { c.section_id = e.target.value; draw(); return; }
    else c[f] = e.target.value;
    if (f === 'title') e.target.closest('[data-cid]').querySelector('.cqa-ce__t').textContent = e.target.value || 'Новый критерий';
    if (f === 'weight') e.target.closest('[data-cid]').querySelector('.cqa-ce__w').innerHTML = `${c.weight || 0} <small>вес</small>`;
    drawSide();
  };
  list.addEventListener('input', onField);
  list.addEventListener('change', (e) => { if (['type', 'section_id', 'na_allowed'].includes(e.target.dataset.f)) onField(e); });

  S.main.querySelector('#cqa-add')?.addEventListener('click', () => {
    const id = `c${String(Date.now()).slice(-5)}`;
    const sec = S.draft.sections.find((s) => s.id !== 's4')?.id || S.draft.sections[0].id;
    const c = { id, section_id: sec, title: '', description: '', type: 'binary', weight: 10, na_allowed: false, na_condition: null, scale: null, flag_types: null, _new: true };
    const flagIdx = S.draft.criteria.findIndex((x) => x.type === 'flag');
    if (flagIdx >= 0) S.draft.criteria.splice(flagIdx, 0, c); else S.draft.criteria.push(c);
    S.open.add(id); draw();
    setTimeout(() => { const card = S.main.querySelector(`[data-cid="${id}"]`); card?.scrollIntoView({ behavior: 'smooth', block: 'center' }); card?.querySelector('[data-f="title"]')?.focus(); }, 80);
  });
  S.main.querySelector('#cqa-reset')?.addEventListener('click', () => { S.draft = clone(S.active); S.open.clear(); draw(); toast('Изменения отменены'); });
  S.main.querySelector('#cqa-save')?.addEventListener('click', save);
}

async function save() {
  if (problems().length) { toast('Сначала исправьте замечания справа', 'bad'); return; }
  const from = S.active.version; const prev = S.active;
  const body = clone(S.draft); body.criteria.forEach((c) => delete c._new);
  let saved;
  try { saved = await api.saveChecklist(body); } catch (err) { toast(err.message, 'bad'); return; }
  S.active = saved; S.draft = clone(saved); S.versions = await api.listChecklists();
  S.open.clear();
  S.main.querySelector('#cqa-cl-ver').textContent = `Версия ${saved.version}, активна`;
  draw();
  toast(`Сохранена версия ${saved.version}`);
  const n = (await api.listConversations().catch(() => [])).length;
  const word = n % 10 === 1 && n % 100 !== 11 ? 'диалог' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'диалога' : 'диалогов';
  const m = openModal({
    title: `Применить версию ${saved.version} к диалогам?`,
    sub: `Будет переоценено ${n} ${word} набора ${store.get().groupId} по новому чек-листу: около ${Math.max(1, Math.round(n / 6 * 0.4))} мин, ≈ ${fmtMoney(n * 0.06)}. Покажем, как изменились итоги и почему`,
    foot: '<button type="button" class="cqa-btn" data-close>Позже</button><button type="button" class="cqa-btn cqa-btn--primary" id="cqa-apply">Переоценить диалоги</button>',
  });
  m.el.querySelector('#cqa-apply').addEventListener('click', async (e) => {
    const b = e.currentTarget; b.disabled = true;
    const list = await api.listConversations();
    const { job_id } = await api.evaluateBatch({ conversation_ids: list.map((x) => x.conversation_id) });
    await new Promise((res) => { const t = setInterval(async () => { const j = await api.getJob(job_id); b.innerHTML = `<span class="cqa-spin"></span>Оценено ${j.done} из ${j.total}`; if (j.status !== 'running') { clearInterval(t); res(); } }, 250); });
    S.diff = { from, to: saved.version, prev, rows: await api.compare({ from_version: from, to_version: saved.version }) };
    m.close(); drawSide(); refreshShell();
    toast('Диалоги переоценены по новой версии');
    S.main.querySelector('#cqa-cl-side').scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

export async function render(main) {
  S.main = main; S.diff = null; S.open.clear();
  S.active = await api.getChecklist('default');
  S.draft = clone(S.active);
  S.versions = await api.listChecklists();
  main.innerHTML = `
    <div class="cqa-page-head"><h1>${esc(S.active.name)}</h1><span class="cqa-page-head__sub" id="cqa-cl-ver">Версия ${S.active.version}, активна</span>
      ${readOnly() ? '<span class="cqa-badge">Только просмотр</span>' : `<div class="cqa-page-head__actions"><button type="button" class="cqa-btn cqa-btn--sm" id="cqa-reset">Отменить изменения</button><button type="button" class="cqa-btn cqa-btn--sm" id="cqa-add">${icon('plus', 'cqa-ico--sm')}Добавить критерий</button><button type="button" class="cqa-btn cqa-btn--primary cqa-btn--sm" id="cqa-save" disabled>${icon('save', 'cqa-ico--sm')}Сохранить новую версию</button></div>`}
    </div>
    <div class="cqa-cl"><div class="cqa-cl__list" id="cqa-cl-list"></div><aside class="cqa-cl__side" id="cqa-cl-side"></aside></div>`;
  draw();
  bind();
}

export function destroy() {}

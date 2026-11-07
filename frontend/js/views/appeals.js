// Экран «Апелляции»: очередь обращений и решение проверяющего с пересчётом итога.
import { api } from '../api.js';
import { store, ROLES } from '../store.js';
import { icon } from '../icons.js';
import { esc, fmt2, fmtTimestamp, answerView, toast } from '../utils.js';
import { relTime } from '../ui.js';
import { refreshShell } from '../app.js';

const S = { main: null, list: [], id: null, tab: 'open', cls: new Map() };
const pad2 = (n) => String(n).padStart(2, '0');
const nf = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const pts = (x) => (x == null ? '—' : nf.format(Math.round(x * 100) / 100));

async function checklistFor(v) {
  const key = String(v || 'active');
  if (!S.cls.has(key)) S.cls.set(key, await api.getChecklist('default', v));
  return S.cls.get(key);
}

function visible() {
  let l = S.list.filter((a) => a.author_role === 'operator');
  if (store.get().role === 'operator') l = l.filter((a) => a.conversation.operator_name === ROLES.operator.person);
  return l.filter((a) => (S.tab === 'open' ? a.status === 'open' : a.status === 'resolved')).sort((a, b) => b.created_at.localeCompare(a.created_at));
}

function listHtml() {
  const l = visible();
  const all = S.list.filter((a) => a.author_role === 'operator' && (store.get().role !== 'operator' || a.conversation.operator_name === ROLES.operator.person));
  return `
    <div class="cqa-q__head"><h2>Апелляции</h2></div>
    <div class="cqa-q__tools"><div class="cqa-tabs" role="group">
      <button type="button" data-tab="open" aria-pressed="${S.tab === 'open'}">На рассмотрении<span class="cqa-n">${all.filter((a) => a.status === 'open').length}</span></button>
      <button type="button" data-tab="resolved" aria-pressed="${S.tab === 'resolved'}">Решённые<span class="cqa-n">${all.filter((a) => a.status === 'resolved').length}</span></button>
    </div></div>
    <div class="cqa-col__body cqa-scroll"><div class="cqa-stagger">
      ${l.length ? l.map((a, i) => `<button type="button" class="cqa-qi" data-id="${a.appeal_id}" style="--i:${i}" aria-current="${a.appeal_id === S.id}">
        <div class="cqa-qi__l1"><span class="cqa-qi__id">${esc(a.conversation_id)}</span>${a.status === 'open' ? '<span class="cqa-badge cqa-badge--warn">На рассмотрении</span>' : `<span class="cqa-badge cqa-badge--${a.decision.action === 'change' ? 'ok' : 'outline'}">${a.decision.action === 'change' ? 'Изменено' : 'Оставлено'}</span>`}</div>
        <div class="cqa-qi__l2">${esc(a.author_name || 'Оператор')}: «${esc(a.comment)}»</div>
        <div class="cqa-qi__l3"><span>${esc(a.criterion_title || a.criterion_id.replace('c', 'Критерий '))}</span><span>${esc(relTime(a.created_at))}</span></div>
      </button>`).join('') : `<div class="cqa-empty" style="padding:32px 16px"><b>${S.tab === 'open' ? 'Открытых апелляций нет' : 'Решённых пока нет'}</b></div>`}
    </div></div>`;
}

function ctxHtml(a) {
  return a.context.map((m) => {
    const hit = (a.criterion_result?.evidence || []).some((e) => e.index === m.index);
    if (m.speaker === 'system') return `<div class="cqa-sys" style="margin-left:0">${esc(fmtTimestamp(m.timestamp))}, ${esc(m.text.toLowerCase())}</div>`;
    return `<div class="cqa-msg cqa-msg--${m.speaker} ${hit ? 'is-hit' : ''}"><span class="cqa-msg__num">${pad2(m.index)}</span><div class="cqa-msg__bub"><div class="cqa-msg__h"><b>${m.speaker === 'operator' ? `${esc(a.conversation.operator_name || 'Оператор')}, оператор` : m.speaker === 'bot' ? 'Бот' : 'Клиент'}</b><span>${esc(fmtTimestamp(m.timestamp))}</span></div><div class="cqa-msg__t">${esc(m.text)}</div></div></div>`;
  }).join('');
}

function answerOptions(c, current) {
  const max = c.type === 'scale' ? Math.max(...c.scale.map((s) => s.value)) : null;
  const opts = c.type === 'scale' ? c.scale.map((s) => [String(s.value), `${s.value} из ${max}: ${s.description}`]) : c.type === 'flag' ? [['yes', 'Выявлено'], ['no', 'Не выявлено']] : [['yes', 'Да'], ['no', 'Нет']];
  if (c.na_allowed) opts.push(['na', `N/A: ${c.na_condition || 'неприменимо'}`]);
  return opts.filter(([v]) => v !== String(current)).map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join('');
}

async function detailHtml(a) {
  const cl = await checklistFor(a.checklist_version);
  const idx = cl.criteria.findIndex((c) => c.id === a.criterion_id);
  const c = cl.criteria[idx]; const r = a.criterion_result || {};
  const num = pad2(idx + 1);
  const scoreTxt = (ans, raw) => (ans === 'na' ? 'N/A' : c.type === 'flag' ? answerView(c, ans).label : `${answerView(c, ans).label}, ${pts(raw)} из ${c.weight}`);
  const canDecide = store.get().role === 'qa' && a.status === 'open';
  const d = a.decision;
  const history = [
    `<li class="is-ok"><b>Оценка AI</b>: ${esc(scoreTxt(a.original.answer, a.original.raw_points))}, итог ${fmt2(a.original.total)}</li>`,
    `<li class="is-ok"><b>Апелляция</b> от ${esc(a.author_name || 'оператора')}, ${esc(relTime(a.created_at))}</li>`,
    d ? `<li class="is-ok"><b>Решение</b> ${esc(d.reviewer)}: ${d.action === 'change' ? `изменено на «${esc(answerView(c, d.new_answer).label)}»` : 'оценка оставлена'}. Итог ${fmt2(d.total_before)} → <b>${fmt2(d.new_total)}</b></li>` : '<li><b>Решение</b>: ожидается</li>',
  ].join('');
  return `
    <div class="cqa-ap__detail cqa-enter">
      <div>
        <div class="cqa-c__l1"><h2>${num} ${esc(c.title)}</h2>${a.status === 'open' ? '<span class="cqa-badge cqa-badge--warn">На рассмотрении</span>' : '<span class="cqa-badge cqa-badge--ok">Решено</span>'}</div>
        <div class="cqa-c__meta"><span>${icon('chat', 'cqa-ico--sm')}<a href="#/dialogs/${encodeURIComponent(a.conversation_id)}">${esc(a.conversation_id)}</a></span><span>${icon('user', 'cqa-ico--sm')}${esc(a.author_name || 'Оператор')}</span><span>${icon('clock', 'cqa-ico--sm')}${esc(relTime(a.created_at))}</span></div>
      </div>
      <div class="cqa-ap__grid">
        <div class="cqa-ap__cell"><small>Оценка AI</small><b>${esc(scoreTxt(a.original.answer, a.original.raw_points))}</b></div>
        <div class="cqa-ap__cell"><small>Итог до решения</small><b>${fmt2(d ? d.total_before : a.current_total)}</b></div>
        <div class="cqa-ap__cell ${d ? 'cqa-ap__cell--new' : ''}"><small>Итог после решения</small><b>${d ? fmt2(d.new_total) : '—'}</b></div>
      </div>
      <div><div class="cqa-cr__label" style="margin-bottom:6px">Комментарий оператора</div><div class="cqa-quote">«${esc(a.comment)}»${a.proposed_answer != null ? `<small style="margin:6px 0 0">Предлагает ответ: <b>${esc(answerView(c, a.proposed_answer).label)}</b></small>` : ''}</div></div>
      <div><div class="cqa-cr__label" style="margin-bottom:6px">Обоснование AI</div><p style="line-height:1.6">${esc(r.explanation || r.na_reason || '')}</p></div>
      <div><div class="cqa-cr__label" style="margin-bottom:6px">Фрагмент диалога</div><div class="cqa-ctx">${ctxHtml(a)}</div></div>
      ${canDecide ? `
      <form class="cqa-card" id="cqa-decide" style="padding:16px 18px;display:flex;flex-direction:column;gap:14px">
        <h3>Решение проверяющего</h3>
        <div class="cqa-choice">
          <label><input type="radio" name="act" value="confirm" checked><span><b>Оставить оценку</b><small>Оценка AI верна</small></span></label>
          <label><input type="radio" name="act" value="change"><span><b>Изменить ответ</b><small>Итог пересчитается</small></span></label>
        </div>
        <div class="cqa-field" id="cqa-new" hidden><label for="cqa-new-ans">Новый ответ</label><select class="cqa-select" id="cqa-new-ans">${answerOptions(c, a.original.answer).replace(a.proposed_answer != null ? `value="${a.proposed_answer}"` : '\u0000', `value="${a.proposed_answer}" selected`)}</select></div>
        <div class="cqa-field"><label for="cqa-reason">Причина решения</label><textarea class="cqa-textarea" id="cqa-reason" placeholder="Её увидит оператор, она сохранится в истории"></textarea><span class="cqa-field__err" id="cqa-reason-err" hidden>Укажите причину решения</span></div>
        <div style="display:flex;justify-content:flex-end"><button type="submit" class="cqa-btn cqa-btn--primary">Сохранить решение</button></div>
      </form>` : d ? `<div><div class="cqa-cr__label" style="margin-bottom:6px">Причина решения</div><div class="cqa-quote">«${esc(d.reason)}»</div></div>` : '<div class="cqa-alert cqa-alert--warn">' + icon('info', 'cqa-ico--sm') + '<div><b>Ожидает решения</b><span class="cqa-alert__text">Апелляцию рассматривает специалист контроля качества</span></div></div>'}
      <div><div class="cqa-cr__label" style="margin-bottom:8px">История</div><ul class="cqa-history">${history}</ul></div>
    </div>`;
}

async function drawDetail() {
  const box = S.main.querySelector('#cqa-ap-detail');
  const a = S.list.find((x) => x.appeal_id === S.id);
  if (!a) { box.innerHTML = `<div class="cqa-empty" style="margin:auto"><div class="cqa-empty__icon">${icon('scale')}</div><h2>Выберите апелляцию</h2><p>Слева — обращения операторов, которые оспорили оценку</p></div>`; return; }
  box.innerHTML = await detailHtml(a);
  const form = box.querySelector('#cqa-decide');
  if (!form) return;
  form.querySelectorAll('[name="act"]').forEach((r) => r.addEventListener('change', () => { box.querySelector('#cqa-new').hidden = form.act.value !== 'change'; }));
  const ta = form.querySelector('#cqa-reason'); const err = form.querySelector('#cqa-reason-err');
  ta.addEventListener('input', () => { err.hidden = true; ta.removeAttribute('aria-invalid'); });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (ta.value.trim().length < 5) { err.hidden = false; ta.setAttribute('aria-invalid', 'true'); ta.focus(); return; }
    const cl = await checklistFor(a.checklist_version); const c = cl.criteria.find((x) => x.id === a.criterion_id);
    let nv = form.querySelector('#cqa-new-ans')?.value;
    if (c.type === 'scale' && nv !== 'na') nv = Number(nv);
    form.querySelector('[type="submit"]').disabled = true;
    try {
      const res = await api.decideAppeal(a.appeal_id, { action: form.act.value, new_answer: nv, reason: ta.value.trim(), reviewer: ROLES.qa.person });
      toast(`Решение сохранено. Итог: ${fmt2(res.decision.total_before)} → ${fmt2(res.decision.new_total)}`);
      await reload();
      refreshShell();
    } catch (er) { toast(er.message, 'bad'); form.querySelector('[type="submit"]').disabled = false; }
  });
}

async function reload() {
  S.list = await api.listAppeals();
  S.main.querySelector('.cqa-ap__list').innerHTML = listHtml();
  bindList();
  await drawDetail();
}

function bindList() {
  const col = S.main.querySelector('.cqa-ap__list');
  col.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => { S.tab = b.dataset.tab; col.innerHTML = listHtml(); bindList(); }));
  col.querySelectorAll('[data-id]').forEach((b) => b.addEventListener('click', () => { location.hash = `#/appeals/${b.dataset.id}`; }));
}

export async function render(main, { id } = {}) {
  S.main = main; S.cls.clear();
  S.list = await api.listAppeals();
  const cur = S.list.find((a) => a.appeal_id === id);
  S.tab = cur ? cur.status : 'open';
  S.id = id || visible()[0]?.appeal_id || null;
  if (S.id && !id) history.replaceState(null, '', `#/appeals/${S.id}`);
  main.innerHTML = `
    <div class="cqa-page-head"><h1>Апелляции</h1><span class="cqa-page-head__sub">${store.get().role === 'operator' ? 'Ваши обращения по оценкам' : 'Операторы оспаривают оценки, проверяющий принимает решение'}</span></div>
    <div class="cqa-ap"><section class="cqa-col cqa-ap__list" aria-label="Список апелляций"></section><section class="cqa-col cqa-scroll" id="cqa-ap-detail" aria-label="Апелляция"></section></div>`;
  main.querySelector('.cqa-ap__list').innerHTML = listHtml();
  bindList();
  await drawDetail();
}

export async function update({ id } = {}) {
  S.id = id;
  S.main.querySelectorAll('.cqa-ap__list [data-id]').forEach((b) => b.setAttribute('aria-current', String(b.dataset.id === id)));
  await drawDetail();
}

export function destroy() {}

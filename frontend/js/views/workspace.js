// Рабочее место «Диалоги» (дизайн B): очередь | переписка | оценка.
// Баллы показываются в весах чек-листа; пересчёт при N/A — в итоге и в окне «Как считается».
import { api } from '../api.js';
import { store, ROLES } from '../store.js';
import { icon } from '../icons.js';
import { esc, fmt2, fmtDuration, fmtTimestamp, fmtMoney, CHANNEL, LANG, GROSS, answerView, toast, debounce, turnNo, refs1 } from '../utils.js';
import { deriveTimeline, deriveBotContext, markHold, withOffsets } from '../derive.js';
import { countUp, growBars, openModal, relTime } from '../ui.js';
import { refreshShell } from '../app.js';

const S = { main: null, list: [], id: null, data: null, active: null, player: null, keys: null, loadSeq: 0 };
const pad2 = (n) => String(n).padStart(2, '0');
const nf = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const pts = (x) => (x == null ? '—' : nf.format(Math.round(x * 100) / 100));
const role = () => store.get().role;

/* ---------- Данные ---------- */
function rowsOf(ev) {
  if (!ev) return [];
  const fin = ev.final?.criteria;
  if (!fin) return ev.criteria;
  return fin.map((f) => {
    const o = ev.criteria.find((x) => x.criterion_id === f.criterion_id);
    const ch = ev.final.overrides.find((x) => x.criterion_id === f.criterion_id);
    return ch ? { ...o, ...f, evidence: o.evidence, explanation: o.explanation, inline_note: o.inline_note, original: o, changed: ch } : o;
  });
}
// SLA первой реакции (3 мин): операционный показатель, в баллы не входит. Клик — к первому ответу оператора.
function slaHtml(sla) {
  if (!sla) return '';
  const lim = fmtDuration(sla.threshold_sec);
  if (sla.wait_sec == null) return `<span class="cqa-sla" title="Оператор не успел ответить: клиент ушёл раньше">Первый ответ: нет, SLA ${lim}</span>`;
  const t = `Ожидание оператора после перевода чата, реплика #${turnNo(sla.first_reply_index)}. Порог первой реакции ${lim} — показатель SLA, в баллы не входит`;
  return `<button type="button" class="cqa-sla ${sla.breached ? 'cqa-sla--bad' : 'cqa-sla--ok'}" data-goto="${sla.first_reply_index}" title="${esc(t)}">Первый ответ ${fmtDuration(sla.wait_sec)} ${sla.breached ? `· SLA ${lim} нарушен` : `· SLA ${lim}`}</button>`;
}

function metaMap(cl) { const m = new Map(); cl.criteria.forEach((c, i) => m.set(c.id, { ...c, num: pad2(i + 1) })); return m; }
function totals(ev, cl) {
  const rows = rowsOf(ev); const meta = metaMap(cl);
  const app = rows.filter((r) => meta.get(r.criterion_id)?.type !== 'flag' && r.answer !== 'na');
  const W = app.reduce((s, r) => s + (meta.get(r.criterion_id)?.weight || 0), 0);
  const raw = app.reduce((s, r) => s + (r.raw_points || 0), 0);
  const na = rows.filter((r) => r.answer === 'na').map((r) => meta.get(r.criterion_id)).filter(Boolean);
  return { W, raw, na, total: ev.final?.total ?? ev.total };
}
const failed = (c, r) => { const v = answerView(c, r.answer).cls; return v === 'no' || v === 'mid'; };
const status = (it) => {
  const e = it.evaluation;
  if (!e) return ['Не оценён', ''];
  if (e.gross_error) return ['Нарушение', 'bad'];
  if (it.appeal_status === 'open') return ['Апелляция', 'warn'];
  if (e.needs_review) return ['Нужна проверка', 'warn'];
  if (e.review?.status === 'confirmed') return ['Проверено', 'ok'];
  return ['Оценён', 'outline'];
};

function filtered() {
  const f = store.get().queue; const q = f.q.trim().toLowerCase();
  let items = S.list;
  if (role() === 'operator') items = items.filter((x) => x.operator_name === ROLES.operator.person);
  items = items.filter((x) =>
    (f.view === 'all' || (f.view === 'risk' && x.evaluation?.gross_error) || (f.view === 'attention' && (x.evaluation?.needs_review || x.appeal_status === 'open' || !x.evaluation))) &&
    (!f.lang || x.language === f.lang) && (!f.channel || x.channel === f.channel) &&
    (!q || `${x.conversation_id} ${x.operator_name || ''} ${x.evaluation?.headline || ''}`.toLowerCase().includes(q)));
  const rank = (x) => (!x.evaluation ? 2 : x.evaluation.gross_error ? 0 : (x.evaluation.needs_review || x.appeal_status === 'open') ? 1 : 3);
  return [...items].sort((a, b) => rank(a) - rank(b) || (a.evaluation?.final_total ?? 999) - (b.evaluation?.final_total ?? 999));
}

/* ---------- Очередь ---------- */
function queueHtml() {
  const f = store.get().queue;
  const base = role() === 'operator' ? S.list.filter((x) => x.operator_name === ROLES.operator.person) : S.list;
  const counts = { all: base.length, risk: base.filter((x) => x.evaluation?.gross_error).length, attention: base.filter((x) => x.evaluation?.needs_review || x.appeal_status === 'open' || !x.evaluation).length };
  return `
    <div class="cqa-q__head"><h2>${role() === 'operator' ? 'Мои диалоги' : 'На проверку'}</h2><button type="button" class="cqa-btn cqa-btn--ghost cqa-btn--icon cqa-btn--sm" id="cqa-q-filter" aria-label="Фильтры" aria-expanded="false">${icon('filter')}</button></div>
    <div class="cqa-q__tools">
      <div class="cqa-tabs" role="group" aria-label="Показать">
        ${[['all', 'Все'], ['risk', 'Нарушения'], ['attention', 'Внимание']].map(([v, t]) => `<button type="button" data-view="${v}" aria-pressed="${f.view === v}">${t}<span class="cqa-n ${v === 'risk' && counts.risk ? 'cqa-n--bad' : ''}">${counts[v]}</span></button>`).join('')}
      </div>
      <label class="cqa-search">${icon('search', 'cqa-ico--sm')}<input class="cqa-input cqa-input--sm" id="cqa-q-search" placeholder="ID, оператор, суть" value="${esc(f.q)}" aria-label="Поиск в очереди"></label>
      <div class="cqa-q__filters" id="cqa-q-filters" ${f.lang || f.channel ? '' : 'hidden'}>
        <select class="cqa-select cqa-select--sm" id="cqa-q-lang" aria-label="Язык"><option value="">Все языки</option><option value="RU">Русский</option><option value="KZ">Қазақша</option><option value="EN">English</option></select>
        <select class="cqa-select cqa-select--sm" id="cqa-q-chan" aria-label="Канал"><option value="">Чаты и звонки</option><option value="agent chat">Чаты</option><option value="voice call">Звонки</option></select>
      </div>
    </div>
    <div class="cqa-col__body cqa-scroll" id="cqa-q-list"></div>`;
}

function queueItems() {
  const items = filtered();
  if (!items.length) return '<div class="cqa-empty" style="padding:32px 16px"><b>Ничего не найдено</b><span>Измените фильтр или поиск</span></div>';
  const attention = items.filter((x) => !x.evaluation || x.evaluation.gross_error || x.evaluation.needs_review || x.appeal_status === 'open');
  const rest = items.filter((x) => !attention.includes(x));
  const one = (x, i) => {
    const [label, tone] = status(x); const t = x.evaluation?.final_total;
    return `<button type="button" class="cqa-qi" data-id="${esc(x.conversation_id)}" style="--i:${Math.min(i, 12)}" aria-current="${x.conversation_id === S.id}">
      <div class="cqa-qi__l1"><span class="cqa-qi__id">${esc(x.conversation_id)}</span><span class="cqa-badge cqa-badge--${tone}">${label}</span></div>
      <div class="cqa-qi__l2" title="${esc(x.evaluation?.headline || '')}">${esc(x.evaluation?.headline || [x.operator_name, CHANNEL[x.channel], x.language].filter(Boolean).join(', '))}</div>
      <div class="cqa-qi__l3"><span class="cqa-qi__score ${t != null && t < 60 ? 'cqa-qi__score--low' : ''}">${t != null ? `${fmt2(t)} / 100` : '—'}</span><span>${esc([x.operator_name, x.language].filter(Boolean).join(', '))}${x.evaluation ? `, ${esc(relTime(x.evaluation.created_at))}` : ''}</span></div>
    </button>`;
  };
  let i = 0;
  return `${attention.length && rest.length ? '<div class="cqa-q__group">Требуют внимания</div>' : ''}<div class="cqa-stagger">${attention.map((x) => one(x, i++)).join('')}</div>${rest.length ? `${attention.length ? '<div class="cqa-q__group">Остальные</div>' : ''}<div class="cqa-stagger">${rest.map((x) => one(x, i++)).join('')}</div>` : ''}`;
}

function bindQueue() {
  const q = S.main.querySelector('.cqa-q');
  const f = store.get().queue;
  q.querySelector('#cqa-q-lang').value = f.lang; q.querySelector('#cqa-q-chan').value = f.channel;
  const redraw = () => { q.querySelector('#cqa-q-list').innerHTML = queueItems(); };
  q.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => { f.view = b.dataset.view; q.querySelectorAll('[data-view]').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); redraw(); }));
  q.querySelector('#cqa-q-search').addEventListener('input', debounce((e) => { f.q = e.target.value; redraw(); }, 120));
  q.querySelector('#cqa-q-lang').addEventListener('change', (e) => { f.lang = e.target.value; redraw(); });
  q.querySelector('#cqa-q-chan').addEventListener('change', (e) => { f.channel = e.target.value; redraw(); });
  q.querySelector('#cqa-q-filter').addEventListener('click', (e) => { const p = q.querySelector('#cqa-q-filters'); p.hidden = !p.hidden; e.currentTarget.setAttribute('aria-expanded', String(!p.hidden)); });
  q.querySelector('#cqa-q-list').addEventListener('click', (e) => {
    const b = e.target.closest('[data-id]'); if (!b) return;
    document.querySelector('.cqa-root').dataset.queueOpen = 'false';
    location.hash = `#/dialogs/${encodeURIComponent(b.dataset.id)}`;
  });
  redraw();
}

function markQueue() {
  S.main.querySelectorAll('.cqa-qi').forEach((b) => b.setAttribute('aria-current', String(b.dataset.id === S.id)));
  S.main.querySelector(`.cqa-qi[aria-current="true"]`)?.scrollIntoView({ block: 'nearest' });
}

/* ---------- Переписка ---------- */
function mapHtml(conv, ev, meta) {
  const tr = conv.transcript; const dur = Math.max(1, tr.at(-1)?.offset_sec || 1);
  const bad = new Set(rowsOf(ev).filter((r) => { const c = meta.get(r.criterion_id); return c && (failed(c, r) || (c.type === 'flag' && r.answer === 'yes')); }).flatMap((r) => (r.evidence || []).map((e) => e.index)));
  const segs = tr.map((m, i) => {
    const next = tr[i + 1]?.offset_sec ?? dur;
    const w = Math.max(1, next - m.offset_sec);
    const cls = bad.has(m.index) ? 'bad' : m.on_hold ? 'hold' : m.speaker;
    return `<span class="cqa-map__seg cqa-map__seg--${cls}" style="flex:${w} 1 0" data-goto="${m.index}" title="#${turnNo(m.index)} ${esc((m.text || '').slice(0, 60))}"></span>`;
  }).join('');
  const nBad = bad.size;
  return `<div class="cqa-map" aria-label="Карта разговора">
    <div class="cqa-map__track">${segs}</div>
    <div class="cqa-map__legend"><span><i class="cqa-map__seg--bot"></i>Бот</span><span><i class="cqa-map__seg--client"></i>Клиент</span><span><i class="cqa-map__seg--operator"></i>Оператор</span><span><i class="cqa-map__seg--bad"></i>Нарушение</span>${conv.transcript.some((m) => m.on_hold) ? '<span><i class="cqa-map__seg--hold"></i>Удержание</span>' : ''}<span class="cqa-map__events">${nBad ? `Проблемных реплик: ${nBad}` : ''}</span></div>
  </div>`;
}

function msgHtml(m, conv, tags, isCall, hit) {
  if (m.speaker === 'system') return `<div class="cqa-sys ${/закрыл|прерван|lost|покинул/i.test(m.text) ? 'cqa-sys--bad' : ''}" id="msg-${m.index}" data-msg="${m.index}">${esc(fmtTimestamp(m.timestamp))}, ${esc(m.text.replace(/\.$/, '').toLowerCase())}</div>`;
  const name = m.speaker === 'operator' ? `${conv.operator_name || 'Оператор'}, оператор` : m.speaker === 'bot' ? 'Бот AIM' : 'Клиент';
  const ts = isCall ? `<button type="button" class="cqa-seek" data-seek="${m.offset_sec}" title="Перейти к моменту записи">${esc(fmtTimestamp(m.timestamp))}</button>` : `<span>${esc(fmtTimestamp(m.timestamp))}</span>`;
  return `<div class="cqa-msg cqa-msg--${m.speaker} ${m.on_hold ? 'cqa-msg--hold' : ''} ${hit ? 'is-hit' : ''}" id="msg-${m.index}" data-msg="${m.index}">
    <span class="cqa-msg__num">${turnNo(m.index)}</span>
    <div><div class="cqa-msg__bub"><div class="cqa-msg__h"><b>${esc(name)}</b>${ts}</div>${m.on_hold ? '<div class="cqa-msg__hold">На удержании: клиент этого не слышит, в оценку не входит</div>' : ''}<div class="cqa-msg__t">${esc(m.text)}</div></div>
    ${tags.length ? `<div class="cqa-msg__tags">${tags.map((t) => `<button type="button" class="cqa-tag ${t.cls === 'mid' ? 'cqa-tag--mid' : ''}" data-crit-open="${t.id}">${t.num} ${esc(t.short)}</button>`).join('')}</div>` : ''}</div>
  </div>`;
}

const SHORT_BY_ID = { c01: 'Контакт', c02: 'Потребность', c03: 'Идентификация', c04: 'Консультация', c05: 'Эмпатия', c06: 'Ведение диалога', c07: 'Завершение' };
const SHORT = (c) => SHORT_BY_ID[c.id] || c.title.split(/[,:]/)[0].split(' ').slice(0, 2).join(' ');

function threadHtml(conv, ev, meta) {
  const rows = rowsOf(ev); const isCall = conv.channel === 'voice call';
  const tagsBy = new Map(); const hits = new Set(); const notes = new Map();
  for (const r of rows) {
    const c = meta.get(r.criterion_id); if (!c) continue;
    const isBad = failed(c, r) || (c.type === 'flag' && r.answer === 'yes');
    if (!isBad) continue;
    const cls = c.type === 'flag' ? 'no' : answerView(c, r.answer).cls;
    (r.evidence || []).forEach((e) => { hits.add(e.index); if (!tagsBy.has(e.index)) tagsBy.set(e.index, []); tagsBy.get(e.index).push({ id: c.id, num: c.num, short: c.type === 'flag' ? 'Грубое нарушение' : SHORT(c), cls }); });
    if (r.inline_note && r.evidence?.[0]) notes.set(r.evidence[0].index, { text: r.inline_note, kind: 'note' });
    else if (r.expected_range && r.answer === 'no' && !notes.has(r.expected_range.to)) notes.set(r.expected_range.to, { text: `Здесь ожидалось: ${c.title.toLowerCase()}`, kind: 'expected' });
  }
  const firstOp = conv.transcript.findIndex((m) => m.speaker === 'operator');
  const pre = firstOp > 0 ? conv.transcript.slice(0, firstOp) : [];
  const post = firstOp > 0 ? conv.transcript.slice(firstOp) : conv.transcript;
  const bc = deriveBotContext(conv.transcript);
  const preHasHits = pre.some((m) => hits.has(m.index));
  const one = (m) => msgHtml(m, conv, tagsBy.get(m.index) || [], isCall, hits.has(m.index)) + (notes.has(m.index) ? `<div class="cqa-inline ${notes.get(m.index).kind === 'expected' ? 'cqa-inline--expected' : ''}">${icon('info', 'cqa-ico--sm')}${esc(refs1(notes.get(m.index).text))}</div>` : '');
  return `
    <div class="cqa-day">Начало ${isCall ? 'звонка' : 'переписки'}</div>
    ${pre.length ? `<div class="cqa-botctx"><b>${bc.client.length ? 'Клиент написал боту:' : 'Переписка с ботом'}</b>${bc.client.map((m) => `<div class="cqa-botctx__q">«${esc(m.text)}»</div>`).join('')}
      <button type="button" class="cqa-link cqa-link--accent" id="cqa-pre-toggle" aria-expanded="${preHasHits}">${icon(preHasHits ? 'up' : 'down', 'cqa-ico--sm')}${preHasHits ? 'Скрыть' : 'Показать'} переписку с ботом, ${pre.length}</button></div>
      <div class="cqa-pre" id="cqa-pre" ${preHasHits ? '' : 'hidden'}>${pre.map(one).join('')}</div>` : ''}
    ${post.map(one).join('')}`;
}

function playerHtml(conv, ev, meta) {
  const dur = conv.transcript.at(-1)?.offset_sec || 0;
  const bad = [...new Set(rowsOf(ev).filter((r) => { const c = meta.get(r.criterion_id); return c && failed(c, r); }).flatMap((r) => (r.evidence || []).map((e) => e.index)))].map((i) => conv.transcript[i]).filter(Boolean);
  const demo = String(conv.audio_url || '').startsWith('demo://');
  return `<div class="cqa-player">
    <button type="button" class="cqa-btn cqa-btn--icon cqa-btn--sm" id="cqa-play" aria-label="Воспроизвести">${icon('play', 'cqa-ico--sm')}</button>
    <div class="cqa-player__track" id="cqa-track" role="slider" aria-label="Позиция записи" aria-valuemin="0" aria-valuemax="${dur}" tabindex="0"><div class="cqa-player__rail"><i class="cqa-player__fill" id="cqa-fill"></i></div>${bad.map((m) => `<span class="cqa-player__tick" style="left:${dur ? (m.offset_sec / dur) * 100 : 0}%" title="Реплика #${turnNo(m.index)}"></span>`).join('')}</div>
    <span class="cqa-player__time" id="cqa-time">0:00 / ${fmtDuration(dur)}</span>
    ${demo ? '<span class="cqa-badge" title="В демо-данных нет аудиофайла, позиция идёт по таймкодам">Демо без звука</span>' : `<audio id="cqa-audio" preload="metadata" src="${esc(conv.audio_url)}"></audio>`}
  </div>`;
}

function setupPlayer(col, conv) {
  const dur = conv.transcript.at(-1)?.offset_sec || 0;
  const audio = col.querySelector('#cqa-audio'); const fill = col.querySelector('#cqa-fill'); const time = col.querySelector('#cqa-time'); const play = col.querySelector('#cqa-play');
  let pos = 0; let timer = null;
  const total = () => (audio && Number.isFinite(audio.duration) ? audio.duration : dur);
  const playing = () => (audio ? !audio.paused : !!timer);
  const draw = () => {
    fill.style.width = `${total() ? (pos / total()) * 100 : 0}%`;
    time.textContent = `${fmtDuration(pos)} / ${fmtDuration(total())}`;
    col.querySelectorAll('.cqa-msg.is-playing').forEach((el) => el.classList.remove('is-playing'));
    const cur = [...conv.transcript].reverse().find((m) => m.offset_sec <= pos && m.speaker !== 'system');
    if (cur && playing()) col.querySelector(`#msg-${cur.index}`)?.classList.add('is-playing');
  };
  const setIcon = (on) => { play.innerHTML = icon(on ? 'pause' : 'play', 'cqa-ico--sm'); play.setAttribute('aria-label', on ? 'Пауза' : 'Воспроизвести'); };
  const seek = (s) => { pos = Math.max(0, Math.min(total(), s)); if (audio) audio.currentTime = pos; draw(); };
  if (audio) {
    audio.addEventListener('timeupdate', () => { pos = audio.currentTime; draw(); });
    audio.addEventListener('play', () => setIcon(true)); audio.addEventListener('pause', () => setIcon(false));
    audio.addEventListener('error', () => toast('Запись недоступна: проверьте ссылку audio_url', 'bad'));
  }
  play.addEventListener('click', () => {
    if (audio) { audio.paused ? audio.play() : audio.pause(); return; }
    if (timer) { clearInterval(timer); timer = null; setIcon(false); draw(); return; }
    setIcon(true);
    timer = setInterval(() => { pos += 1; if (pos >= dur) { clearInterval(timer); timer = null; setIcon(false); } draw(); }, 1000);
  });
  const track = col.querySelector('#cqa-track');
  track.addEventListener('click', (e) => { const r = track.getBoundingClientRect(); seek(((e.clientX - r.left) / r.width) * total()); });
  track.addEventListener('keydown', (e) => { if (e.key === 'ArrowRight') seek(pos + 5); if (e.key === 'ArrowLeft') seek(pos - 5); });
  draw();
  return { seek, stop: () => { clearInterval(timer); audio?.pause(); } };
}

function chatHtml(conv, ev, meta, ids) {
  const it = S.list.find((x) => x.conversation_id === conv.conversation_id) || {};
  const [label, tone] = status(it);
  const pos = ids.indexOf(conv.conversation_id);
  const prev = pos > 0 ? ids[pos - 1] : null; const next = pos >= 0 && pos < ids.length - 1 ? ids[pos + 1] : null;
  const nav = (id, dir) => (id ? `<a class="cqa-btn cqa-btn--icon cqa-btn--sm" href="#/dialogs/${encodeURIComponent(id)}" aria-label="${dir === 'up' ? 'Предыдущий' : 'Следующий'} диалог" title="${dir === 'up' ? 'Предыдущий (K)' : 'Следующий (J)'}">${icon(dir, 'cqa-ico--sm')}</a>` : `<span class="cqa-btn cqa-btn--icon cqa-btn--sm" aria-disabled="true" style="opacity:.4">${icon(dir, 'cqa-ico--sm')}</span>`);
  const isCall = conv.channel === 'voice call';
  return `
    <div class="cqa-c__head">
      <div class="cqa-c__l1"><h2>${esc(conv.conversation_id)}</h2><span class="cqa-badge cqa-badge--${tone}">${label}</span>
        <span class="cqa-c__nav">${pos >= 0 ? `<span class="cqa-num">${pos + 1} из ${ids.length}</span>` : ''}${nav(prev, 'up')}${nav(next, 'down')}</span></div>
      <div class="cqa-c__meta">
        ${conv.operator_name ? `<span>${icon('user', 'cqa-ico--sm')}Оператор ${esc(conv.operator_name)}</span>` : ''}
        <span>${icon(isCall ? 'phone' : 'chat', 'cqa-ico--sm')}${esc(CHANNEL[conv.channel] || conv.channel)}</span>
        <span>${icon('globe', 'cqa-ico--sm')}${esc(LANG[conv.language] || conv.language)}</span>
        <span>${icon('clock', 'cqa-ico--sm')}${fmtDuration(conv.transcript.at(-1)?.offset_sec)}</span>
        ${slaHtml(S.data?.sla)}
      </div>
      ${ev?.headline ? `<div class="cqa-c__headline">${esc(ev.headline)}</div>` : ''}
    </div>
    ${mapHtml(conv, ev, meta)}
    ${isCall ? playerHtml(conv, ev, meta) : ''}
    <div class="cqa-col__body cqa-scroll" id="cqa-thread-scroll"><div class="cqa-thread" id="cqa-thread">${threadHtml(conv, ev, meta)}</div></div>`;
}

/* ---------- Оценка ---------- */
function verdict(c, r) {
  if (r.answer === 'na') return ['na', 'N/A: критерий неприменим'];
  if (c.type === 'flag') return r.answer === 'yes' ? ['no', `Выявлено: ${(S.data.evaluation.gross_error?.types || []).map((t) => GROSS[t] || t).join(', ') || 'грубое нарушение'}`] : ['yes', 'Не выявлено'];
  if (c.type === 'binary') return r.answer === 'yes' ? ['yes', 'Да'] : ['no', 'Нет'];
  const max = Math.max(...c.scale.map((s) => s.value)); const v = Number(r.answer);
  const step = c.scale.find((s) => s.value === v);
  return [v >= max ? 'yes' : v === 0 ? 'no' : 'mid', `${v} из ${max} по шкале${step ? `: ${step.description.replace(/\.$/, '').toLowerCase()}` : ''}`];
}

function mathText(c, r) {
  if (c.type === 'flag') return 'Без баллов, отмечается отдельно от итога';
  if (r.answer === 'na') return `Вес ${c.weight} исключён из расчёта и распределён между применимыми критериями`;
  if (c.type === 'binary') return r.answer === 'yes' ? `«Да» даёт полный вес: ${c.weight} из ${c.weight}` : `«Нет» даёт 0 из ${c.weight}`;
  const max = Math.max(...c.scale.map((s) => s.value));
  return `${c.weight} × ${r.answer} / ${max} = ${pts(r.raw_points)} из ${c.weight}`;
}

function critHtml(c, r, open) {
  const ev = S.data.evaluation; const rl = role();
  const v = answerView(c, r.answer).cls; const isFlag = c.type === 'flag';
  const cls = r.answer === 'na' ? 'na' : isFlag ? (r.answer === 'yes' ? 'flag' : 'yes') : v;
  const p = r.answer === 'na' ? 'N/A' : isFlag ? (r.answer === 'yes' ? 'Выявлено' : 'Нет') : `${pts(r.raw_points)} <small>из ${c.weight}</small>`;
  const [vc, vt] = verdict(c, r);
  const ap = S.data.appeals.find((a) => a.criterion_id === c.id && a.evaluation_id === ev.evaluation_id && a.author_role === 'operator');
  let act = '';
  if (ap?.status === 'open') act = rl === 'qa' ? `<a class="cqa-link cqa-link--accent" href="#/appeals/${ap.appeal_id}">Рассмотреть апелляцию</a>` : '<span class="cqa-badge cqa-badge--warn">Апелляция на рассмотрении</span>';
  else if (ap?.status === 'resolved') act = `<span class="cqa-badge cqa-badge--ok">${ap.decision.action === 'change' ? 'Изменено по апелляции' : 'Апелляция отклонена'}</span>`;
  else if (rl === 'operator') act = `<button type="button" class="cqa-link" data-appeal="${c.id}">Оспорить оценку</button>`;
  else if (rl === 'qa') act = `<button type="button" class="cqa-link" data-override="${c.id}">Изменить</button>`;
  const evs = (r.evidence || []).map((e) => `<button type="button" class="cqa-ev ${r.evidence_valid === false ? 'cqa-ev--invalid' : ''}" data-goto="${e.index}"><span class="cqa-ev__idx">#${turnNo(e.index)}</span><span class="cqa-ev__q">${esc(e.quote || '')}</span></button>`).join('');
  const range = r.expected_range ? `<div class="cqa-cr__meta">${icon('target', 'cqa-ico--sm')}<span>Ожидалось ${r.expected_range.from === r.expected_range.to ? `в реплике #${turnNo(r.expected_range.from)}` : `между #${turnNo(r.expected_range.from)} и #${turnNo(r.expected_range.to)}`}</span><button type="button" class="cqa-link cqa-link--accent" data-range="${r.expected_range.from}-${r.expected_range.to}">Показать</button></div>` : '';
  const changed = r.changed ? `<div class="cqa-alert cqa-alert--ok">${icon('edit', 'cqa-ico--sm')}<div><b>Изменено проверяющим</b><span class="cqa-alert__text">Было: ${esc(answerView(c, r.original.answer).label)}. Стало: ${esc(answerView(c, r.answer).label)}</span></div></div>` : '';
  return `
    <div class="cqa-cr cqa-cr--${cls} ${r.changed ? 'cqa-cr--changed' : ''} ${open ? 'is-open' : ''}" data-crit="${c.id}">
      <button type="button" class="cqa-cr__row" aria-expanded="${open}"><span class="cqa-cr__n">${c.num}</span><span class="cqa-cr__t" title="${esc(c.title)}">${esc(c.title)}</span><span class="cqa-cr__p">${p}</span><span class="cqa-cr__chev">${icon('down', 'cqa-ico--sm')}</span></button>
      ${r.answer === 'na' && !open ? `<div class="cqa-cr__na">${esc(refs1(r.na_reason || ''))}</div>` : ''}
      <div class="cqa-cr__wrap"><div><div class="cqa-cr__body">
        <div class="cqa-cr__verdict cqa-cr__verdict--${vc}">${icon(vc === 'yes' ? 'check' : vc === 'na' ? 'info' : 'alert', 'cqa-ico--sm')}${esc(vt)}</div>
        ${changed}
        <p class="cqa-cr__expl">${esc(refs1(r.answer === 'na' ? r.na_reason : r.explanation || ''))}</p>
        ${evs ? `<div class="cqa-cr__label">Доказательство</div><div class="cqa-evs">${evs}</div>` : ''}
        ${range}
        <div class="cqa-cr__meta"><span>Уверенность AI</span><span class="cqa-conf ${r.confidence === 'low' ? 'cqa-conf--low' : ''}">${r.confidence === 'low' ? 'низкая' : 'высокая'}</span>${r.uncertainty_reason ? `<span class="cqa-faint">${esc(refs1(r.uncertainty_reason))}</span>` : ''}${r.evidence_valid === false ? '<span class="cqa-faint">цитата не найдена в реплике</span>' : ''}</div>
        <div class="cqa-cr__foot"><span>${esc(mathText(c, r))}</span><span class="cqa-push">${act}</span></div>
      </div></div></div>
    </div>`;
}

function evalHtml(conv, ev, cl) {
  const meta = metaMap(cl); const rows = rowsOf(ev); const T = totals(ev, cl); const rl = role();
  const g = ev.gross_error;
  const tone = g?.flag ? ['bad', 'Грубое нарушение'] : T.total == null ? ['outline', 'Не рассчитан'] : T.total >= 85 ? ['ok', 'Норма'] : T.total >= 60 ? ['warn', 'Есть замечания'] : ['bad', 'Критично'];
  const changed = ev.final?.overrides?.length && ev.final.total !== ev.total;
  const segs = rows.filter((r) => meta.get(r.criterion_id)?.type !== 'flag').map((r) => {
    const c = meta.get(r.criterion_id);
    if (r.answer === 'na') return `<span class="cqa-bar__seg cqa-bar__seg--na" data-crit-open="${c.id}" title="${c.num} ${esc(c.title)}: N/A, вес ${c.weight} перераспределён"></span>`;
    const share = c.weight ? (r.raw_points / c.weight) * 100 : 0;
    return `<span class="cqa-bar__seg ${answerView(c, r.answer).cls === 'mid' ? 'cqa-bar__seg--mid' : ''}" style="flex:${c.weight} 1 0" data-crit-open="${c.id}" title="${c.num} ${esc(c.title)}: ${pts(r.raw_points)} из ${c.weight}"><i class="cqa-bar__fill" data-w="${share}%"></i></span>`;
  }).join('');
  const low = rows.filter((r) => r.confidence === 'low' || r.evidence_valid === false).map((r) => meta.get(r.criterion_id)).filter(Boolean);
  const openAp = S.data.appeals.filter((a) => a.status === 'open' && a.evaluation_id === ev.evaluation_id && a.author_role === 'operator');
  const firstOpen = S.active || rows.find((r) => { const c = meta.get(r.criterion_id); return c && c.type !== 'flag' && failed(c, r); })?.criterion_id;
  const secs = (cl.sections?.length ? cl.sections : [{ id: null, title: 'Критерии' }]).map((s) => {
    const crits = cl.criteria.filter((c) => (s.id ? c.section_id === s.id : true));
    if (!crits.length) return '';
    const own = crits.map((c) => [meta.get(c.id), rows.find((r) => r.criterion_id === c.id)]).filter(([, r]) => r);
    const scored = own.filter(([c, r]) => c.type !== 'flag' && r.answer !== 'na');
    const sw = scored.reduce((sum, [c]) => sum + c.weight, 0); const sr = scored.reduce((sum, [, r]) => sum + r.raw_points, 0);
    const allFlag = own.every(([c]) => c.type === 'flag');
    return `<div class="cqa-sec"><b>${esc(s.title)}</b><span>${allFlag ? 'без баллов' : sw ? `${pts(sr)} из ${pts(sw)}` : 'N/A'}</span></div>${own.map(([c, r]) => critHtml(c, r, c.id === firstOpen)).join('')}`;
  }).join('');
  let acts = '';
  if (rl === 'qa') {
    acts = ev.review?.status === 'confirmed'
      ? `<div class="cqa-acts__done">${icon('check')}<span>Проверено: ${esc(ev.review.by)}, ${esc(relTime(ev.review.at))}</span><button type="button" class="cqa-link" id="cqa-unconfirm">Отменить</button></div>`
      : `<div class="cqa-acts"><button type="button" class="cqa-btn cqa-btn--outline cqa-btn--lg" id="cqa-override">Изменить оценку</button><button type="button" class="cqa-btn cqa-btn--primary cqa-btn--lg" id="cqa-confirm">Подтвердить проверку</button></div>`;
  } else if (rl === 'operator') {
    acts = `<div class="cqa-acts cqa-acts--single"><button type="button" class="cqa-btn cqa-btn--primary cqa-btn--lg" id="cqa-appeal-any">${icon('flag')}Оспорить оценку</button></div>`;
  }
  const rep = ev.reproducibility;
  const grossTxt = (g?.types || []).map((t) => GROSS[t] || t).join(', ').toLowerCase() || 'выявлено';
  const flags = [
    g?.flag ? `<li class="cqa-flag cqa-flag--bad">${icon('alert', 'cqa-ico--sm')}<span><b>Грубое нарушение:</b> ${esc(grossTxt)}. Не влияет на балл${g.evidence?.[0] ? `, <button type="button" class="cqa-link" data-goto="${g.evidence[0].index}">реплика #${turnNo(g.evidence[0].index)}</button>` : ''}</span></li>` : '',
    low.length ? `<li class="cqa-flag cqa-flag--warn">${icon('info', 'cqa-ico--sm')}<span><b>Проверьте вручную:</b> низкая уверенность модели, ${low.map((c) => esc(c.title.toLowerCase())).join(', ')}</span></li>` : '',
    openAp.length ? `<li class="cqa-flag cqa-flag--ok">${icon('scale', 'cqa-ico--sm')}<span><b>Апелляция оператора:</b> ${openAp.map((a) => esc((meta.get(a.criterion_id)?.title || '').toLowerCase())).join(', ')}${rl === 'qa' ? `, <a href="#/appeals/${openAp[0].appeal_id}">рассмотреть</a>` : ''}</span></li>` : '',
  ].join('');
  const longSummary = (ev.summary || '').length > 150;
  return `
    <div class="cqa-col__body cqa-scroll" id="cqa-crit-scroll">
    <div class="cqa-score ${tone[0] === 'bad' && !g?.flag ? 'cqa-score--low' : ''}">
      <div class="cqa-score__l1"><h2>Итоговый балл</h2><button type="button" class="cqa-link" id="cqa-howto">${icon('help', 'cqa-ico--sm')}Как считается</button></div>
      <div class="cqa-score__num"><b id="cqa-total" data-v="${T.total ?? ''}">${T.total == null ? '—' : fmt2(T.total)}</b><span>/ 100</span><span class="cqa-badge cqa-badge--${tone[0]}">${tone[1]}</span></div>
      <div class="cqa-bar" role="img" aria-label="Вклад критериев в итог: ${pts(T.raw)} из ${pts(T.W)}" title="Ширина отрезка — вес критерия, заливка — набранные баллы${T.na.length ? ', пунктир — N/A' : ''}">${segs}</div>
      <div class="cqa-score__raw">${T.total == null ? 'Нет применимых критериев с весом: итог не рассчитывается' : `${pts(T.raw)} из ${pts(T.W)} возможных баллов${T.na.length ? `, N/A: ${T.na.map((c) => c.title.toLowerCase()).join(', ')}` : ''}`}${changed ? `<br>После решения проверяющего: <s>${fmt2(ev.total)}</s> → <b>${fmt2(ev.final.total)}</b>` : ''}</div>
      ${ev.summary ? `<p class="cqa-summary ${longSummary ? 'is-clamped' : ''}" id="cqa-summary">${esc(refs1(ev.summary))}</p>${longSummary ? '<button type="button" class="cqa-link cqa-link--accent" id="cqa-summary-more" aria-controls="cqa-summary" aria-expanded="false">Читать полностью</button>' : ''}` : ''}
      ${flags ? `<ul class="cqa-flags">${flags}</ul>` : ''}
    </div>
      <div class="cqa-crit-h"><h2>Критерии оценки</h2><span>${esc(cl.name)}, версия ${ev.checklist_version}</span></div>
      ${secs}
      <div style="height:12px"></div>
    </div>
    ${acts}
    <div class="cqa-evfoot"><span>${esc(ev.model?.name || '')}, ${ev.usage?.duration_ms ? `${fmt2(ev.usage.duration_ms / 1000)} с` : ''}, ${fmtMoney(ev.usage?.cost_usd)} за оценку</span>
      ${rep ? `<span class="${rep.agreement === 1 ? 'cqa-ok' : ''}">${icon('check', 'cqa-ico--sm')} Повторный прогон: совпадение ${Math.round(rep.agreement * 100)} %</span>` : rl !== 'operator' ? '<button type="button" class="cqa-link" id="cqa-repeat">Проверить воспроизводимость</button>' : ''}</div>`;
}

function evaluatingHtml(cl) {
  return `<div class="cqa-score"><div class="cqa-score__l1"><h2>Идёт оценка</h2></div><p class="cqa-muted" style="margin-top:6px">Модель читает диалог и проверяет каждый критерий чек-листа</p></div>
    <div class="cqa-evaluating" id="cqa-evaluating">${cl.criteria.map((c, i) => `<div class="cqa-evaluating__row" data-i="${i}"><span class="cqa-cr__n">${pad2(i + 1)}</span><span>${esc(c.title)}</span><span class="cqa-spin"></span></div>`).join('')}</div>`;
}

function emptyEvalHtml() {
  return `<div class="cqa-empty" style="margin:auto"><div class="cqa-empty__icon">${icon('target')}</div><h2>Диалог ещё не оценён</h2><p>Модель проверит каждый критерий и укажет реплики-доказательства. Это займёт несколько секунд</p>${role() === 'qa' ? `<button type="button" class="cqa-btn cqa-btn--primary" id="cqa-eval-now">${icon('sparkle')}Оценить диалог</button>` : '<p class="cqa-faint">Оценку запускает специалист контроля качества</p>'}</div>`;
}

/* ---------- Взаимодействия ---------- */
function light(critId, on) {
  const r = rowsOf(S.data?.evaluation).find((x) => x.criterion_id === critId);
  S.main.querySelectorAll(`[data-crit="${critId}"]`).forEach((el) => el.classList.toggle('is-lit', on));
  (r?.evidence || []).forEach((e) => S.main.querySelector(`#msg-${e.index}`)?.classList.toggle('is-lit', on));
}

function scrollToMsg(index, { flash = true } = {}) {
  const el = S.main.querySelector(`#msg-${index}`);
  if (!el) return;
  const pre = S.main.querySelector('#cqa-pre');
  if (pre && pre.contains(el)) pre.hidden = false;
  document.querySelector('.cqa-root').dataset.wsTab = 'chat';
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  if (flash) { el.classList.remove('is-flash'); void el.offsetWidth; el.classList.add('is-flash'); setTimeout(() => el.classList.remove('is-flash'), 1800); }
  const m = S.data.conversation.transcript[index];
  if (S.player && m) S.player.seek(m.offset_sec);
}

function openCrit(id, { scroll = true } = {}) {
  S.active = id;
  const el = S.main.querySelector(`.cqa-cr[data-crit="${id}"]`);
  if (!el) return;
  document.querySelector('.cqa-root').dataset.wsTab = 'eval';
  S.main.querySelectorAll('.cqa-cr.is-open').forEach((x) => { if (x !== el) { x.classList.remove('is-open'); x.querySelector('.cqa-cr__row').setAttribute('aria-expanded', 'false'); } });
  el.classList.add('is-open'); el.querySelector('.cqa-cr__row').setAttribute('aria-expanded', 'true');
  el.querySelector('.cqa-cr__na')?.remove();
  if (scroll) setTimeout(() => el.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 60);
  light(id, true); setTimeout(() => light(id, false), 1400);
}

function bindColumns() {
  const ws = S.main.querySelector('.cqa-ws');
  ws.onclick = async (e) => {
    const t = e.target;
    const go = t.closest('[data-goto]'); if (go) { scrollToMsg(Number(go.dataset.goto)); return; }
    const co = t.closest('[data-crit-open]'); if (co) { openCrit(co.dataset.critOpen); return; }
    const rg = t.closest('[data-range]');
    if (rg) { const [a, b] = rg.dataset.range.split('-').map(Number); scrollToMsg(b, { flash: true }); if (a !== b) S.main.querySelector(`#msg-${a}`)?.classList.add('is-lit'); setTimeout(() => S.main.querySelector(`#msg-${a}`)?.classList.remove('is-lit'), 1600); return; }
    const sk = t.closest('[data-seek]'); if (sk && S.player) { S.player.seek(Number(sk.dataset.seek)); return; }
    const row = t.closest('.cqa-cr__row');
    if (row) { const box = row.closest('.cqa-cr'); if (box.classList.contains('is-open')) { box.classList.remove('is-open'); row.setAttribute('aria-expanded', 'false'); S.active = null; } else openCrit(box.dataset.crit, { scroll: false }); return; }
    if (t.closest('#cqa-pre-toggle')) { const p = S.main.querySelector('#cqa-pre'); p.hidden = !p.hidden; const b = t.closest('#cqa-pre-toggle'); b.setAttribute('aria-expanded', String(!p.hidden)); b.innerHTML = `${icon(p.hidden ? 'down' : 'up', 'cqa-ico--sm')}${p.hidden ? 'Показать' : 'Скрыть'} переписку с ботом, ${p.children.length}`; return; }
    const ap = t.closest('[data-appeal]'); if (ap) { appealModal(ap.dataset.appeal); return; }
    const ov = t.closest('[data-override]'); if (ov) { overrideModal(ov.dataset.override); return; }
    if (t.closest('#cqa-appeal-any')) { appealModal(S.active); return; }
    if (t.closest('#cqa-override')) { overrideModal(S.active); return; }
    if (t.closest('#cqa-howto')) { howtoModal(); return; }
    if (t.closest('#cqa-summary-more')) { const p = S.main.querySelector('#cqa-summary'); const b = t.closest('#cqa-summary-more'); const open = p.classList.toggle('is-clamped'); b.textContent = open ? 'Читать полностью' : 'Свернуть'; b.setAttribute('aria-expanded', String(!open)); return; }
    if (t.closest('#cqa-confirm')) { const b = t.closest('#cqa-confirm'); b.disabled = true; await api.reviewEvaluation(S.data.evaluation.evaluation_id, { action: 'confirm', reviewer: ROLES.qa.person }); toast('Проверка подтверждена'); await reloadAll(); return; }
    if (t.closest('#cqa-unconfirm')) { await api.reviewEvaluation(S.data.evaluation.evaluation_id, { action: 'unconfirm' }); await reloadAll(); return; }
    if (t.closest('#cqa-repeat')) { const b = t.closest('#cqa-repeat'); b.innerHTML = '<span class="cqa-spin" style="width:11px;height:11px"></span> Повторный прогон'; const ev = await api.repeatEvaluation(S.data.evaluation.evaluation_id); toast(`Повторный прогон: совпадение ${Math.round(ev.reproducibility.agreement * 100)} %`); await loadConversation(S.id, { keepScroll: true }); return; }
    if (t.closest('#cqa-eval-now')) { await evaluateNow(); }
  };
  ws.onmouseover = (e) => { const c = e.target.closest('.cqa-cr[data-crit]'); if (c) light(c.dataset.crit, true); };
  ws.onmouseout = (e) => { const c = e.target.closest('.cqa-cr[data-crit]'); if (c) light(c.dataset.crit, false); };
}

async function evaluateNow() {
  const col = S.main.querySelector('.cqa-e'); const cl = S.data.checklist;
  col.innerHTML = evaluatingHtml(cl);
  const rows = [...col.querySelectorAll('.cqa-evaluating__row')];
  let i = 0;
  const tick = setInterval(() => { if (i < rows.length - 1) { rows[i].classList.add('is-done'); rows[i].querySelector('.cqa-spin').outerHTML = icon('check', 'cqa-ico--sm'); i += 1; } }, 160);
  try { await api.evaluate(S.id); toast('Оценка готова'); } catch (err) { toast(err.message, 'bad'); }
  clearInterval(tick);
  await reloadAll();
}

/* ---------- Модальные окна ---------- */
function answerControl(c, current) {
  const opts = c.type === 'scale' ? c.scale.map((s) => [String(s.value), `${s.value} из ${Math.max(...c.scale.map((x) => x.value))}: ${s.description}`]) : c.type === 'flag' ? [['yes', 'Выявлено'], ['no', 'Не выявлено']] : [['yes', 'Да'], ['no', 'Нет']];
  if (c.na_allowed) opts.push(['na', `N/A: ${c.na_condition || 'неприменимо'}`]);
  return `<select class="cqa-select" id="cqa-m-ans">${opts.map(([v, t]) => `<option value="${v}" ${String(current) === v ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>`;
}

function critOptions(selected) {
  const meta = metaMap(S.data.checklist); const rows = rowsOf(S.data.evaluation);
  return S.data.checklist.criteria.map((c) => { const r = rows.find((x) => x.criterion_id === c.id); return `<option value="${c.id}" ${c.id === selected ? 'selected' : ''}>${meta.get(c.id).num} ${esc(c.title)}: ${esc(answerView(c, r?.answer).label)}</option>`; }).join('');
}

function appealModal(critId) {
  const rows = rowsOf(S.data.evaluation);
  const first = critId || rows.find((r) => { const c = metaMap(S.data.checklist).get(r.criterion_id); return c && failed(c, r); })?.criterion_id || S.data.checklist.criteria[0].id;
  const m = openModal({
    title: 'Оспорить оценку',
    sub: 'Проверяющий увидит оценку AI, ваш комментарий и фрагмент диалога',
    body: `<div class="cqa-field"><label for="cqa-m-crit">Критерий</label><select class="cqa-select" id="cqa-m-crit">${critOptions(first)}</select></div>
      <div class="cqa-field"><label for="cqa-m-ans">Какой ответ вы считаете верным</label><div id="cqa-m-ans-box"></div><span class="cqa-field__hint">Необязательно: проверяющий увидит ваше предложение рядом с оценкой AI</span></div>
      <div class="cqa-field"><label for="cqa-m-text">Почему вы не согласны</label><textarea class="cqa-textarea" id="cqa-m-text" aria-describedby="cqa-m-err" placeholder="Например: в реплике #09 я подвёл итог и предложил помощь"></textarea><span class="cqa-field__err" id="cqa-m-err" role="alert" hidden>Опишите, с чем вы не согласны, хотя бы одним предложением</span></div>`,
    foot: '<button type="button" class="cqa-btn" data-close>Отмена</button><button type="button" class="cqa-btn cqa-btn--primary" id="cqa-m-ok">Отправить апелляцию</button>',
  });
  const meta = metaMap(S.data.checklist);
  const drawAns = (cid) => { m.el.querySelector('#cqa-m-ans-box').innerHTML = answerControl(meta.get(cid), '__none__').replace('<select class="cqa-select" id="cqa-m-ans">', '<select class="cqa-select" id="cqa-m-ans"><option value="">Не предлагаю</option>'); };
  drawAns(first);
  m.el.querySelector('#cqa-m-crit').addEventListener('change', (e) => drawAns(e.target.value));
  const ta = m.el.querySelector('#cqa-m-text'); const err = m.el.querySelector('#cqa-m-err');
  ta.addEventListener('input', () => { err.hidden = true; ta.removeAttribute('aria-invalid'); });
  m.el.querySelector('#cqa-m-ok').addEventListener('click', async (e) => {
    if (ta.value.trim().length < 10) { err.hidden = false; ta.setAttribute('aria-invalid', 'true'); ta.focus(); return; }
    e.currentTarget.disabled = true;
    const cid = m.el.querySelector('#cqa-m-crit').value; const raw = m.el.querySelector('#cqa-m-ans').value;
    const proposed = raw === '' ? null : (meta.get(cid).type === 'scale' && raw !== 'na' ? Number(raw) : raw);
    try { await api.createAppeal({ evaluation_id: S.data.evaluation.evaluation_id, criterion_id: cid, comment: ta.value.trim(), proposed_answer: proposed, author_role: 'operator' }); m.close(); toast('Апелляция отправлена проверяющему'); await reloadAll(); }
    catch (er) { toast(er.message, 'bad'); e.currentTarget.disabled = false; }
  });
}

function overrideModal(critId) {
  const meta = metaMap(S.data.checklist); const rows = rowsOf(S.data.evaluation);
  let cid = critId || S.active || S.data.checklist.criteria[0].id;
  const m = openModal({
    title: 'Изменить оценку',
    sub: 'Решение проверяющего сохраняется в истории; итог пересчитается по правилам чек-листа',
    body: `<div class="cqa-field"><label for="cqa-m-crit">Критерий</label><select class="cqa-select" id="cqa-m-crit">${critOptions(cid)}</select></div>
      <div class="cqa-field"><label for="cqa-m-ans">Новый ответ</label><div id="cqa-m-ans-box"></div></div>
      <div class="cqa-field"><label for="cqa-m-text">Причина изменения</label><textarea class="cqa-textarea" id="cqa-m-text" placeholder="Например: личность подтверждена кодом из SMS в реплике #06"></textarea><span class="cqa-field__err" id="cqa-m-err" hidden>Укажите причину: она видна оператору и в истории</span></div>`,
    foot: '<button type="button" class="cqa-btn" data-close>Отмена</button><button type="button" class="cqa-btn cqa-btn--primary" id="cqa-m-ok">Сохранить решение</button>',
  });
  const box = m.el.querySelector('#cqa-m-ans-box');
  const draw = () => { const r = rows.find((x) => x.criterion_id === cid); box.innerHTML = answerControl(meta.get(cid), r?.answer); };
  draw();
  m.el.querySelector('#cqa-m-crit').addEventListener('change', (e) => { cid = e.target.value; draw(); });
  const ta = m.el.querySelector('#cqa-m-text'); const err = m.el.querySelector('#cqa-m-err');
  ta.addEventListener('input', () => { err.hidden = true; ta.removeAttribute('aria-invalid'); });
  m.el.querySelector('#cqa-m-ok').addEventListener('click', async (e) => {
    if (ta.value.trim().length < 5) { err.hidden = false; ta.setAttribute('aria-invalid', 'true'); ta.focus(); return; }
    const c = meta.get(cid); const raw = m.el.querySelector('#cqa-m-ans').value;
    const val = c.type === 'scale' && raw !== 'na' ? Number(raw) : raw;
    e.currentTarget.disabled = true;
    try {
      const before = S.data.evaluation.final?.total ?? S.data.evaluation.total;
      const ap = await api.overrideCriterion({ evaluation_id: S.data.evaluation.evaluation_id, criterion_id: cid, new_answer: val, reason: ta.value.trim() });
      m.close(); toast(`Итог: ${fmt2(before)} → ${fmt2(ap.decision.new_total)}`);
      S.active = cid; await reloadAll();
    } catch (er) { toast(er.message, 'bad'); e.currentTarget.disabled = false; }
  });
}

function howtoModal() {
  const cl = S.data.checklist; const meta = metaMap(cl); const rows = rowsOf(S.data.evaluation); const T = totals(S.data.evaluation, cl);
  const body = rows.map((r) => {
    const c = meta.get(r.criterion_id); if (!c) return '';
    const na = r.answer === 'na'; const flag = c.type === 'flag';
    return `<tr class="${na ? 'na' : ''}"><td>${c.num}</td><td>${esc(c.title)}</td><td class="r">${flag ? '—' : c.weight}</td><td>${esc(answerView(c, r.answer).label)}</td><td class="r">${flag ? (r.answer === 'yes' ? 'флаг' : '—') : na ? 'исключён' : pts(r.raw_points)}</td></tr>`;
  }).join('');
  openModal({
    title: 'Как считается итог', wide: true,
    sub: 'Строго по правилам чек-листа: стр. 2, «Порядок расчёта»',
    body: `<table class="cqa-calc"><thead><tr><th>№</th><th>Критерий</th><th class="r">Вес</th><th>Ответ</th><th class="r">Баллы</th></tr></thead><tbody>${body}</tbody>
      <tfoot><tr><td></td><td>Итого по применимым критериям</td><td class="r">${pts(T.W)}</td><td></td><td class="r">${pts(T.raw)}</td></tr></tfoot></table>
      <div class="cqa-formula">${T.total == null ? 'Применимых критериев с весом нет: итог не рассчитывается.' : `Итог = ${pts(T.raw)} ÷ ${pts(T.W)} × 100 = <b>${fmt2(T.total)}</b>`}<br>
      «Да» даёт полный вес, «Нет» — 0. Шкала: вес × ступень ÷ максимум ступени. Критерии с N/A исключаются, их вес пропорционально распределяется между остальными, чтобы максимум остался 100. Итог округляется до двух знаков. Грубые нарушения отмечаются отдельно и на балл не влияют.</div>`,
  });
}

/* ---------- Загрузка ---------- */
async function loadConversation(id, { keepScroll = false } = {}) {
  const seq = ++S.loadSeq;
  const chat = S.main.querySelector('.cqa-c'); const ev = S.main.querySelector('.cqa-e');
  const critScroll = keepScroll ? S.main.querySelector('#cqa-crit-scroll')?.scrollTop : 0;
  const threadScroll = keepScroll ? S.main.querySelector('#cqa-thread-scroll')?.scrollTop : 0;
  // Состояние загрузки: колонки приглушаются, пока приходят данные (на сервере это заметно)
  const busy = setTimeout(() => [chat, ev].forEach((el) => { el.setAttribute('aria-busy', 'true'); el.classList.add('is-loading'); }), 120);
  const unbusy = () => { clearTimeout(busy); [chat, ev].forEach((el) => { el.removeAttribute('aria-busy'); el.classList.remove('is-loading'); }); };
  let data;
  try { data = await api.getConversation(id); } catch (err) { unbusy(); toast(err.message, 'bad'); return; }
  if (seq !== S.loadSeq) return;
  unbusy();
  const conv = { ...data.conversation, transcript: withOffsets(markHold(data.conversation.transcript)) };
  const checklist = await api.getChecklist(data.evaluation?.checklist_id || 'default', data.evaluation?.checklist_version);
  if (seq !== S.loadSeq) return;
  S.data = { conversation: conv, evaluation: data.evaluation, appeals: data.appeals || [], checklist, sla: data.sla || null };
  if (!keepScroll) S.active = null;
  const meta = metaMap(checklist);
  const ids = filtered().map((x) => x.conversation_id);
  S.player?.stop?.();
  chat.innerHTML = chatHtml(conv, data.evaluation, meta, ids);
  chat.classList.remove('cqa-enter'); void chat.offsetWidth; chat.classList.add('cqa-enter');
  S.player = conv.channel === 'voice call' ? setupPlayer(chat, conv) : null;
  ev.innerHTML = data.evaluation ? evalHtml(conv, data.evaluation, checklist) : emptyEvalHtml();
  if (data.evaluation) {
    const tot = ev.querySelector('#cqa-total');
    if (!keepScroll && tot?.dataset.v) countUp(tot, Number(tot.dataset.v));
    growBars(ev);
  }
  if (keepScroll) {
    const cs = S.main.querySelector('#cqa-crit-scroll'); if (cs) cs.scrollTop = critScroll;
    const ts = S.main.querySelector('#cqa-thread-scroll'); if (ts) ts.scrollTop = threadScroll;
  }
}

async function reloadAll() {
  S.list = await api.listConversations();
  S.main.querySelector('#cqa-q-list').innerHTML = queueItems();
  const q = S.main.querySelector('.cqa-q__tools'); // счётчики вкладок
  if (q) { const tmp = document.createElement('div'); tmp.innerHTML = queueHtml(); q.querySelector('.cqa-tabs').innerHTML = tmp.querySelector('.cqa-tabs').innerHTML; q.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => { store.get().queue.view = b.dataset.view; q.querySelectorAll('[data-view]').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); S.main.querySelector('#cqa-q-list').innerHTML = queueItems(); })); }
  markQueue();
  await loadConversation(S.id, { keepScroll: true });
  refreshShell();
}

function headHtml() {
  const rl = role();
  const title = rl === 'operator' ? 'Мои диалоги' : rl === 'manager' ? 'Диалоги' : 'Проверка диалогов';
  const n = S.list.filter((x) => x.evaluation).length;
  return `<div class="cqa-page-head">
      <button type="button" class="cqa-btn cqa-btn--icon cqa-q-toggle" id="cqa-q-toggle" aria-label="Очередь диалогов">${icon('inbox')}</button>
      <h1>${title}</h1><span class="cqa-page-head__sub">Набор ${esc(store.get().groupId)}, ${n} из ${S.list.length} оценено</span>
      ${rl === 'qa' ? `<div class="cqa-page-head__actions">
        <label class="cqa-label" for="cqa-group">Набор</label>
        <input class="cqa-input cqa-input--sm" id="cqa-group" value="${esc(store.get().groupId)}" title="group_id набора у организаторов" style="width:96px" autocomplete="off">
        <button type="button" class="cqa-btn cqa-btn--sm" id="cqa-import">${icon('download', 'cqa-ico--sm')}Загрузить</button>
        <button type="button" class="cqa-btn cqa-btn--outline cqa-btn--sm" id="cqa-batch">${icon('sparkle', 'cqa-ico--sm')}Оценить все</button>
      </div>` : ''}
    </div>
    <div class="cqa-ws-tabs"><div class="cqa-seg" role="group" aria-label="Показать"><button type="button" data-wstab="chat" aria-pressed="true">Диалог</button><button type="button" data-wstab="eval" aria-pressed="false">Оценка</button></div></div>`;
}

function bindHead() {
  const m = S.main; const rootEl = document.querySelector('.cqa-root');
  m.querySelector('#cqa-q-toggle')?.addEventListener('click', () => { rootEl.dataset.queueOpen = rootEl.dataset.queueOpen === 'true' ? 'false' : 'true'; });
  m.querySelectorAll('[data-wstab]').forEach((b) => b.addEventListener('click', () => { rootEl.dataset.wsTab = b.dataset.wstab; }));
  m.querySelector('#cqa-import')?.addEventListener('click', async (e) => {
    const g = m.querySelector('#cqa-group').value.trim(); const b = e.currentTarget;
    if (!g) { toast('Укажите group_id набора', 'bad'); return; }
    b.disabled = true; b.innerHTML = '<span class="cqa-spin"></span>Загрузка';
    try { const r = await api.importDataset(g); store.set({ groupId: g }); toast(r.note || `Загружено диалогов: ${r.imported}`); await reloadAll(); } catch (er) { toast(er.message, 'bad'); }
    b.disabled = false; b.innerHTML = `${icon('download', 'cqa-ico--sm')}Загрузить`;
  });
  m.querySelector('#cqa-batch')?.addEventListener('click', async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try {
      const { job_id } = await api.evaluateBatch({ conversation_ids: S.list.map((x) => x.conversation_id) });
      let seen = -1; await new Promise((res) => { const t = setInterval(async () => { const j = await api.getJob(job_id); b.innerHTML = `<span class="cqa-spin"></span>${j.done} из ${j.total}`; if (j.done !== seen) { seen = j.done; refreshShell(); } if (j.status !== 'running') { clearInterval(t); res(); } }, 300); });
      toast('Все диалоги оценены'); await reloadAll();
    } catch (er) { toast(er.message, 'bad'); }
    b.disabled = false; b.innerHTML = `${icon('sparkle', 'cqa-ico--sm')}Оценить все`;
  });
  // Мобильные вкладки отражают текущую колонку
  const sync = () => m.querySelectorAll('[data-wstab]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.wstab === (rootEl.dataset.wsTab || 'chat'))));
  new MutationObserver(sync).observe(rootEl, { attributes: true, attributeFilter: ['data-ws-tab'] });
}

/* ---------- Экспорт ---------- */
export async function render(main, { id } = {}) {
  S.main = main; S.active = null;
  main.classList.add('cqa-main--fill');
  const rootEl = document.querySelector('.cqa-root'); rootEl.dataset.wsTab = 'chat'; rootEl.dataset.queueOpen = 'false';
  S.list = await api.listConversations();
  const ids = filtered().map((x) => x.conversation_id);
  S.id = id && S.list.some((x) => x.conversation_id === id) ? id : ids[0];
  if (!S.id) { main.innerHTML = `${headHtml()}<div class="cqa-card"><div class="cqa-empty"><div class="cqa-empty__icon">${icon('inbox')}</div><h2>Диалогов пока нет</h2><p>Загрузите набор по group_id, чтобы начать проверку</p></div></div>`; bindHead(); return; }
  if (!id) history.replaceState(null, '', `#/dialogs/${encodeURIComponent(S.id)}`);
  main.innerHTML = `${headHtml()}<div class="cqa-ws"><section class="cqa-col cqa-q" aria-label="Очередь">${queueHtml()}</section><section class="cqa-col cqa-c" aria-label="Диалог"></section><section class="cqa-col cqa-e" aria-label="Оценка"></section></div>`;
  bindHead(); bindQueue(); bindColumns(); markQueue();
  await loadConversation(S.id);
  S.keys = (e) => {
    if (e.target.closest?.('input, textarea, select') || document.querySelector('.cqa-modal-back, .cqa-cmd-back')) return;
    const order = filtered().map((x) => x.conversation_id); const p = order.indexOf(S.id);
    if (e.key === 'j' && p < order.length - 1) location.hash = `#/dialogs/${encodeURIComponent(order[p + 1])}`;
    else if (e.key === 'k' && p > 0) location.hash = `#/dialogs/${encodeURIComponent(order[p - 1])}`;
    else if (e.key === '/') { e.preventDefault(); S.main.querySelector('#cqa-q-search')?.focus(); }
    else if (e.key === 'Escape') { S.main.querySelectorAll('.cqa-cr.is-open').forEach((x) => x.classList.remove('is-open')); S.active = null; }
    else if (/^[1-9]$/.test(e.key) && S.data?.evaluation) { const c = S.data.checklist.criteria[Number(e.key) - 1]; if (c) openCrit(c.id); }
  };
  document.addEventListener('keydown', S.keys);
}

export async function update({ id } = {}) {
  if (!id || id === S.id) return;
  S.id = id; markQueue();
  await loadConversation(id);
}

export function destroy() {
  S.main?.classList.remove('cqa-main--fill');
  document.removeEventListener('keydown', S.keys);
  S.player?.stop?.(); S.player = null;
  const rootEl = document.querySelector('.cqa-root'); delete rootEl.dataset.wsTab; delete rootEl.dataset.queueOpen;
}

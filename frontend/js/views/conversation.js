// Экран «Карточка диалога»: транскрипт + результат оценки.
// Связь «критерий ↔ реплика» работает в обе стороны: наведение подсвечивает, клик переводит.
import { api } from '../api.js';
import { store } from '../store.js';
import { icon } from '../icons.js';
import { esc, fmt2, fmtDuration, fmtTimestamp, fmtMoney, fmt0, CHANNEL, LANG, ROLE, GROSS, answerView, scoreTone, toast } from '../utils.js';
import { deriveTimeline, deriveBotContext, markHold, withOffsets } from '../derive.js';
import { orderedIds } from './list.js';
import { refreshAppealsCount } from '../app.js';

let ctx = null;          // { conv, ev, checklist, appeals, main }
let keyHandler = null;
let player = null;

const pad2 = (n) => String(n).padStart(2, '0');

function critMeta(checklist) {
  const map = new Map();
  checklist.criteria.forEach((c, i) => map.set(c.id, { ...c, num: pad2(i + 1) }));
  return map;
}

function rowsOf(ev) {
  if (!ev) return [];
  const finals = ev.final?.criteria;
  if (!finals) return ev.criteria;
  return finals.map((f) => {
    const o = ev.criteria.find((x) => x.criterion_id === f.criterion_id);
    const changed = ev.final.overrides.find((x) => x.criterion_id === f.criterion_id);
    return changed ? { ...o, ...f, evidence: o.evidence, explanation: o.explanation, original: o } : o;
  });
}

/* ---------- Транскрипт ---------- */

function marksByMessage(ev, meta) {
  const by = new Map();
  for (const r of rowsOf(ev)) {
    const c = meta.get(r.criterion_id); if (!c) continue;
    const v = answerView(c, r.answer);
    for (const e of r.evidence || []) {
      if (!by.has(e.index)) by.set(e.index, []);
      if (!by.get(e.index).some((x) => x.id === c.id)) by.get(e.index).push({ id: c.id, num: c.num, cls: v.cls, title: c.title });
    }
  }
  return by;
}

function msgHtml(m, marks, isCall) {
  const cls = [`cqa-msg`, `cqa-msg--${m.speaker}`, m.on_hold ? 'cqa-msg--hold' : ''].join(' ');
  const mk = (marks.get(m.index) || []).map((x) => `<button type="button" class="cqa-mark cqa-mark--${x.cls}" data-mark="${x.id}" title="${esc(x.title)}">${x.num}</button>`).join('');
  const roleIcon = { client: 'user', operator: 'headset', bot: 'bot', system: 'info' }[m.speaker];
  return `
    <div class="${cls}" id="msg-${m.index}" data-msg="${m.index}">
      <div class="cqa-msg__gutter">${mk}</div>
      <div class="cqa-msg__body">
        <div class="cqa-msg__meta">
          ${icon(roleIcon, 'cqa-ico--sm')}<span class="cqa-msg__role">${ROLE[m.speaker] || m.speaker}</span>
          <span class="cqa-msg__ts ${isCall ? 'cqa-msg__ts--link' : ''}" ${isCall ? `data-seek="${m.offset_sec}" title="Перейти к моменту записи"` : ''}>${esc(fmtTimestamp(m.timestamp))}</span>
          ${m.on_hold ? `<span class="cqa-hold-note">${icon('pause_c', 'cqa-ico--sm')}На удержании, клиент не слышит</span>` : ''}
          <span class="cqa-msg__idx">#${m.index}</span>
        </div>
        <div class="cqa-msg__text">${esc(m.text)}</div>
      </div>
    </div>`;
}

function transcriptHtml(conv, ev, meta) {
  const marks = marksByMessage(ev, meta);
  const isCall = conv.channel === 'voice call';
  const bc = ev?.bot_context?.client ? ev.bot_context : deriveBotContext(conv.transcript);
  const firstOp = conv.transcript.findIndex((m) => m.speaker === 'operator');
  const pre = firstOp > 0 ? conv.transcript.slice(0, firstOp) : [];
  const post = firstOp > 0 ? conv.transcript.slice(firstOp) : conv.transcript;
  const clientLines = (bc.client || []).filter((m) => m.text);
  const preHasMarks = pre.some((m) => marks.has(m.index));
  return `
    ${pre.length ? `
      <div class="cqa-botctx">
        <div class="cqa-botctx__head">${icon('bot')}<span>До подключения оператора клиент написал</span></div>
        ${clientLines.length ? `<ul class="cqa-botctx__quotes">${clientLines.map((m) => `<li>${esc(m.text)}</li>`).join('')}</ul>` : '<p class="cqa-faint" style="margin-top:6px">Клиент ничего не написал по существу</p>'}
        <button type="button" class="cqa-btn cqa-btn--ghost cqa-btn--sm cqa-botctx__more" id="cqa-toggle-pre">${icon('down')}Показать переписку с ботом, ${pre.length} ${plural(pre.length, ['сообщение', 'сообщения', 'сообщений'])}</button>
      </div>
      <div id="cqa-pre" class="${preHasMarks ? '' : 'cqa-collapsed'}">${pre.map((m) => msgHtml(m, marks, isCall)).join('')}</div>` : ''}
    ${post.map((m) => msgHtml(m, marks, isCall)).join('')}`;
}

function plural(n, f) {
  const a = Math.abs(n) % 100; const b = a % 10;
  if (a > 10 && a < 20) return f[2];
  if (b > 1 && b < 5) return f[1];
  if (b === 1) return f[0];
  return f[2];
}

/* ---------- Плеер (звонки) ---------- */

function playerHtml(conv, ev, meta) {
  const dur = conv.transcript.at(-1)?.offset_sec || 0;
  const bad = rowsOf(ev).filter((r) => { const c = meta.get(r.criterion_id); return c && ['no'].includes(answerView(c, r.answer).cls); });
  const ticks = [...new Set(bad.flatMap((r) => (r.evidence || []).map((e) => e.index)))]
    .map((i) => conv.transcript[i]).filter(Boolean)
    .map((m) => `<span class="cqa-player__tick" style="left:${dur ? (m.offset_sec / dur) * 100 : 0}%" title="Реплика #${m.index}"></span>`).join('');
  const demo = String(conv.audio_url || '').startsWith('demo://');
  return `
    <div class="cqa-player" id="cqa-player">
      <button type="button" class="cqa-btn cqa-btn--icon" id="cqa-play" aria-label="Воспроизвести">${icon('play')}</button>
      <div class="cqa-player__track" id="cqa-track" role="slider" aria-label="Позиция записи" tabindex="0">
        <div class="cqa-player__rail"><div class="cqa-player__fill" id="cqa-fill"></div></div>${ticks}
      </div>
      <span class="cqa-player__time" id="cqa-time">0:00 / ${fmtDuration(dur)}</span>
      ${demo ? '<span class="cqa-badge" title="В демо-данных нет аудиофайла: плеер показывает позицию по таймкодам">Демо без звука</span>' : ''}
      ${demo ? '' : `<audio id="cqa-audio" preload="metadata" src="${esc(conv.audio_url)}"></audio>`}
    </div>`;
}

function setupPlayer(main, conv) {
  const dur = conv.transcript.at(-1)?.offset_sec || 0;
  const audio = main.querySelector('#cqa-audio');
  let pos = 0; let timer = null;
  const fill = main.querySelector('#cqa-fill'); const time = main.querySelector('#cqa-time'); const play = main.querySelector('#cqa-play');
  const total = () => (audio && Number.isFinite(audio.duration) ? audio.duration : dur);
  const draw = () => {
    fill.style.width = `${total() ? (pos / total()) * 100 : 0}%`;
    time.textContent = `${fmtDuration(pos)} / ${fmtDuration(total())}`;
    const current = [...conv.transcript].reverse().find((m) => m.offset_sec <= pos);
    main.querySelectorAll('.cqa-msg.is-playing').forEach((el) => el.classList.remove('is-playing'));
    if (current && (audio ? !audio.paused : timer)) main.querySelector(`#msg-${current.index}`)?.classList.add('is-playing');
  };
  const setPlaying = (on) => { play.innerHTML = icon(on ? 'pause' : 'play'); play.setAttribute('aria-label', on ? 'Пауза' : 'Воспроизвести'); };
  const seek = (sec) => { pos = Math.max(0, Math.min(total(), sec)); if (audio) audio.currentTime = pos; draw(); };
  if (audio) {
    audio.addEventListener('timeupdate', () => { pos = audio.currentTime; draw(); });
    audio.addEventListener('play', () => setPlaying(true));
    audio.addEventListener('pause', () => setPlaying(false));
    audio.addEventListener('error', () => toast('Запись недоступна — проверьте ссылку audio_url', 'bad'));
  }
  play.addEventListener('click', () => {
    if (audio) { audio.paused ? audio.play() : audio.pause(); return; }
    if (timer) { clearInterval(timer); timer = null; setPlaying(false); draw(); return; }
    setPlaying(true);
    timer = setInterval(() => { pos += 1; if (pos >= dur) { clearInterval(timer); timer = null; setPlaying(false); } draw(); }, 1000);
  });
  main.querySelector('#cqa-track').addEventListener('click', (ev) => {
    const r = ev.currentTarget.getBoundingClientRect();
    seek(((ev.clientX - r.left) / r.width) * total());
  });
  draw();
  return { seek, stop: () => { clearInterval(timer); audio?.pause(); } };
}

/* ---------- Лента событий ---------- */

function timelineHtml(conv, ev, meta) {
  const tl = ev?.timeline?.length ? ev.timeline : deriveTimeline(conv.transcript, ev);
  const rows = rowsOf(ev);
  const c03 = rows.find((r) => r.criterion_id === 'c03');
  const c07 = rows.find((r) => r.criterion_id === 'c07');
  const items = [...tl];
  if (c03?.answer === 'no') items.push({ type: 'identity_missing', index: c03.expected_range?.from ?? c03.evidence?.[0]?.index ?? 0 });
  items.sort((a, b) => a.index - b.index);
  const label = (s) => ({
    escalation: ['Передан оператору', ''],
    operator_greeting: ['Ответ оператора', 'ok'],
    identity_verified: ['Личность подтверждена', 'ok'],
    identity_missing: ['Личность не подтверждена', 'missing'],
    risk: [`Нарушение: ${(s.kinds || []).map((k) => GROSS[k] || k).join(', ') || 'грубая ошибка'}`, 'bad'],
    hold: ['Удержание', 'warn'],
    end: [{ farewell: 'Завершение', call_ended: 'Звонок завершён', client_left: 'Клиент ушёл', connection_lost: 'Обрыв связи', operator_closed: 'Оператор закрыл чат' }[s.kind] || 'Завершение', c07?.answer === 'no' ? 'bad' : (c07?.answer === 'na' ? 'warn' : 'ok')],
  }[s.type] || [s.type, '']);
  return items.map((s, i) => {
    const [text, tone] = label(s);
    const m = conv.transcript[s.index];
    return `${i ? '<span class="cqa-tl-line"></span>' : ''}<button type="button" class="cqa-tl-step ${tone ? `cqa-tl-step--${tone}` : ''}" data-goto="${s.index}"><span class="cqa-tl-dot"></span>${esc(text)}${m ? `<span class="cqa-tl-time">${esc(fmtTimestamp(m.timestamp))}</span>` : ''}</button>`;
  }).join('');
}

/* ---------- Результат ---------- */

function weightsHtml(ev, meta) {
  const rows = rowsOf(ev).filter((r) => meta.get(r.criterion_id)?.type !== 'flag');
  const na = rows.filter((r) => r.answer === 'na');
  const segs = rows.map((r) => {
    const c = meta.get(r.criterion_id);
    if (r.answer === 'na') return `<span class="cqa-weights__seg cqa-weights__seg--na" data-wseg="${c.id}" title="${c.num} ${esc(c.title)}: N/A, вес ${c.weight} перераспределён"></span>`;
    const share = r.effective_weight ? (r.points / r.effective_weight) * 100 : 0;
    const tone = answerView(c, r.answer).cls;
    return `<span class="cqa-weights__seg ${tone === 'mid' ? 'cqa-weights__seg--mid' : ''}" style="flex:${r.effective_weight} 1 0" data-wseg="${c.id}" title="${c.num} ${esc(c.title)}: ${fmt2(r.points)} из ${fmt2(r.effective_weight)}"><span class="cqa-weights__fill" style="width:${share}%"></span></span>`;
  }).join('');
  return `
    <div>
      <div class="cqa-weights" aria-label="Вклад критериев в итог">${segs}</div>
      <div class="cqa-weights__legend"><span>Ширина отрезка — вес критерия, заливка — набранные баллы</span><span>${na.length ? `N/A: ${na.map((r) => meta.get(r.criterion_id).num).join(', ')}, вес перераспределён` : 'Все критерии применимы'}</span></div>
    </div>`;
}

function mathLine(c, r, ev) {
  if (c.type === 'flag') return 'Без баллов, отмечается отдельно';
  if (r.answer === 'na') return `Вес ${c.weight} исключён и распределён между применимыми критериями`;
  const base = c.type === 'scale' ? `${r.answer} из ${Math.max(...c.scale.map((s) => s.value))} → ${fmt2(r.raw_points)} из ${c.weight}` : `${r.answer === 'yes' ? 'Да' : 'Нет'} → ${fmt2(r.raw_points)} из ${c.weight}`;
  const k = r.weight ? r.effective_weight / r.weight : 1;
  return Math.abs(k - 1) < 1e-6 ? `${base} баллов` : `${base}, с учётом N/A вес ${c.weight} → ${fmt2(r.effective_weight)}, итог ${fmt2(r.points)}`;
}

function critHtml(c, r, ev, appeals, role) {
  const v = answerView(c, r.answer);
  const isFlag = c.type === 'flag';
  const open = (v.cls === 'no' || v.cls === 'mid' || (isFlag && r.answer === 'yes') || r.confidence === 'low' || r.original);
  const ap = appeals.find((a) => a.criterion_id === c.id && a.evaluation_id === ev.evaluation_id);
  const ev_ = (r.evidence || []).map((e) => `<button type="button" class="cqa-ev ${r.evidence_valid === false ? 'cqa-ev--invalid' : ''}" data-goto="${e.index}"><span class="cqa-ev__idx">#${e.index}</span><span class="cqa-ev__quote">${esc(e.quote || '')}</span></button>`).join('');
  const range = r.expected_range ? `<button type="button" class="cqa-banner__link" data-range="${r.expected_range.from}-${r.expected_range.to}">Действие ожидалось ${r.expected_range.from === r.expected_range.to ? `в реплике #${r.expected_range.from}` : `в репликах #${r.expected_range.from}–#${r.expected_range.to}`}</button>` : '';
  let apHtml = '';
  if (ap?.status === 'open') apHtml = role === 'reviewer' ? `<a class="cqa-btn cqa-btn--sm" href="#/appeals">${icon('scale')}Рассмотреть апелляцию</a>` : `<span class="cqa-badge cqa-badge--info">Апелляция на рассмотрении</span>`;
  else if (ap?.status === 'resolved') apHtml = `<span class="cqa-badge">${ap.decision.action === 'change' ? 'Изменено по апелляции' : 'Апелляция отклонена'}</span>`;
  else if (role === 'operator' && !isFlag) apHtml = `<button type="button" class="cqa-btn cqa-btn--sm" data-appeal="${c.id}">${icon('flag')}Оспорить</button>`;
  const orig = r.original ? `<s class="cqa-faint">${esc(answerView(c, r.original.answer).label)}</s> ` : '';
  return `
    <div class="cqa-crit ${open ? 'is-open' : ''} ${isFlag && r.answer === 'yes' ? 'cqa-crit--flagged' : ''}" data-crit="${c.id}">
      <button type="button" class="cqa-crit__row" aria-expanded="${open}">
        <span class="cqa-crit__num">${c.num}</span>
        <span class="cqa-crit__title" title="${esc(c.title)}">${esc(c.title)}</span>
        <span>${orig}<span class="cqa-chip cqa-chip--${v.cls}">${esc(v.label)}</span></span>
        <span class="cqa-crit__pts">${isFlag || r.answer === 'na' ? '<span class="cqa-faint">—</span>' : fmt2(r.points)}</span>
        <span class="cqa-crit__chev">${icon('down', 'cqa-ico--sm')}</span>
      </button>
      <div class="cqa-crit__body">
        ${r.answer === 'na' ? `<p class="cqa-crit__expl"><span class="cqa-muted">Почему N/A.</span> ${esc(r.na_reason || '')}</p>` : `<p class="cqa-crit__expl">${esc(r.explanation || '')}</p>`}
        ${isFlag && r.answer === 'yes' && ev.gross_error?.types?.length ? `<div class="cqa-flags">${ev.gross_error.types.map((t) => `<span class="cqa-badge cqa-badge--danger">${esc(GROSS[t] || t)}</span>`).join('')}</div>` : ''}
        ${ev_ ? `<div class="cqa-evlist">${ev_}</div>` : ''}
        ${range}
        ${r.evidence_valid === false ? `<div class="cqa-banner cqa-banner--warn">${icon('alert')}<span class="cqa-banner__text">Цитата не найдена в указанной реплике, вывод нужно проверить</span></div>` : ''}
        <div class="cqa-crit__foot">
          <span>${esc(mathLine(c, r, ev))}</span>
          ${r.confidence === 'low' ? '<span class="cqa-badge cqa-badge--warn">Низкая уверенность</span>' : ''}
          ${apHtml}
        </div>
      </div>
    </div>`;
}

function resultHtml(ev, checklist, meta, appeals, role) {
  const rows = rowsOf(ev);
  const total = ev.final?.total ?? ev.total;
  const tone = scoreTone(total);
  const changed = ev.final?.overrides?.length && ev.final.total !== ev.total;
  const lowConf = rows.filter((r) => r.confidence === 'low' || r.evidence_valid === false).map((r) => meta.get(r.criterion_id)).filter(Boolean);
  const openAp = appeals.filter((a) => a.status === 'open' && a.evaluation_id === ev.evaluation_id);
  const g = ev.gross_error;
  const bySection = (checklist.sections || [{ id: null, title: '' }]).map((s) => {
    const crits = checklist.criteria.filter((c) => (s.id ? c.section_id === s.id : true));
    if (!crits.length) return '';
    const sec = ev.sections?.find((x) => x.section_id === s.id);
    const items = crits.map((c) => { const r = rows.find((x) => x.criterion_id === c.id); return r ? critHtml(meta.get(c.id), r, ev, appeals, role) : ''; }).join('');
    const scored = crits.some((c) => c.type !== 'flag');
    return `<div><div class="cqa-section__head"><b>${esc(s.title)}</b>${scored && sec && sec.max_points ? `<span class="cqa-num">${fmt2(sec.points)} из ${fmt2(sec.max_points)}</span>` : scored ? '<span>N/A</span>' : ''}</div>${items}</div>`;
  }).join('');
  return `
    <div class="cqa-total ${tone === 'low' ? 'cqa-total--low' : ''}">
      <span class="cqa-total__num">${total == null ? '—' : fmt2(total)}</span><span class="cqa-total__of">${total == null ? 'итог не рассчитывается: нет применимых критериев' : 'из 100'}</span>
      ${changed ? `<span class="cqa-total__change">После апелляции<br><s>${fmt2(ev.total)}</s> → <b>${fmt2(ev.final.total)}</b></span>` : ''}
    </div>
    ${weightsHtml(ev, meta)}
    ${g?.flag ? `<div class="cqa-banner cqa-banner--danger">${icon('alert')}<div><b>Грубое нарушение: ${esc((g.types || []).map((t) => GROSS[t] || t).join(', ') || 'выявлено')}</b><div class="cqa-banner__text">Отмечается отдельно и не влияет на балл. ${g.evidence?.[0] ? `<button type="button" class="cqa-banner__link" data-goto="${g.evidence[0].index}">Показать реплику #${g.evidence[0].index}</button>` : ''}</div></div></div>` : ''}
    ${lowConf.length ? `<div class="cqa-banner cqa-banner--warn">${icon('info')}<div><b>Нужна проверка человеком</b><div class="cqa-banner__text">Низкая уверенность модели: ${lowConf.map((c) => `${c.num} ${esc(c.title)}`).join(', ')}</div></div></div>` : ''}
    ${openAp.length ? `<div class="cqa-banner cqa-banner--info">${icon('scale')}<div><b>Открытых апелляций: ${openAp.length}</b><div class="cqa-banner__text">${openAp.map((a) => `${meta.get(a.criterion_id)?.num} ${esc(meta.get(a.criterion_id)?.title || '')}`).join(', ')}${role === 'reviewer' ? ' · <a href="#/appeals">Перейти к рассмотрению</a>' : ''}</div></div></div>` : ''}
    ${bySection}
    <dl class="cqa-meta">
      <div><dt>Модель</dt><dd>${esc(ev.model?.name || '—')}</dd></div>
      <div><dt>Стоимость оценки</dt><dd>${fmtMoney(ev.usage?.cost_usd)}</dd></div>
      <div><dt>Токены</dt><dd>${fmt0(ev.usage?.input_tokens)} вход, ${fmt0(ev.usage?.output_tokens)} выход</dd></div>
      <div><dt>Время оценки</dt><dd>${ev.usage?.duration_ms ? `${fmt2(ev.usage.duration_ms / 1000)} с` : '—'}</dd></div>
      <div><dt>Чек-лист</dt><dd>${esc(checklist.name)}, версия ${ev.checklist_version}</dd></div>
      <div><dt>Оценено</dt><dd>${ev.created_at ? new Date(ev.created_at).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : '—'}</dd></div>
    </dl>`;
}

function evaluatingHtml(checklist) {
  return `<div class="cqa-evaluating" aria-live="polite">
    <p class="cqa-muted">Модель читает диалог и проверяет критерии чек-листа</p>
    ${checklist.criteria.map((c, i) => `<div class="cqa-evaluating__row"><span class="cqa-crit__num">${pad2(i + 1)}</span><span>${esc(c.title)}</span><span class="cqa-spin" style="justify-self:end"></span></div>`).join('')}
  </div>`;
}

function emptyResultHtml(role) {
  return `<div class="cqa-empty">
    <div class="cqa-empty__icon">${icon('target')}</div>
    <h2>Диалог ещё не оценён</h2>
    <p>Оценка займёт несколько секунд: модель проверит каждый критерий и укажет реплики-доказательства</p>
    <button type="button" class="cqa-btn cqa-btn--primary" id="cqa-eval-now">${icon('sparkle')}Оценить диалог</button>
  </div>`;
}

/* ---------- Взаимодействия ---------- */

function scrollToMsg(main, index, { flash = true } = {}) {
  const pre = main.querySelector('#cqa-pre');
  const el = main.querySelector(`#msg-${index}`);
  if (!el) return;
  if (pre && pre.contains(el)) pre.classList.remove('cqa-collapsed');
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  if (flash) { el.classList.remove('is-flash'); void el.offsetWidth; el.classList.add('is-flash'); setTimeout(() => el.classList.remove('is-flash'), 1600); }
  if (player && el.dataset.msg && ctx.conv.channel === 'voice call') {
    const m = ctx.conv.transcript[index]; if (m) player.seek(m.offset_sec);
  }
}

function light(main, critId, on) {
  const rows = rowsOf(ctx.ev);
  const r = rows.find((x) => x.criterion_id === critId);
  main.querySelectorAll(`[data-crit="${critId}"]`).forEach((el) => el.classList.toggle('is-lit', on));
  main.querySelectorAll(`[data-wseg="${critId}"]`).forEach((el) => el.classList.toggle('is-lit', on));
  (r?.evidence || []).forEach((e) => main.querySelector(`#msg-${e.index}`)?.classList.toggle('is-lit', on));
  main.querySelectorAll(`[data-mark="${critId}"]`).forEach((el) => el.classList.toggle('is-lit', on));
}

function openCrit(main, critId, { scroll = true } = {}) {
  const el = main.querySelector(`.cqa-crit[data-crit="${critId}"]`);
  if (!el) return;
  el.classList.add('is-open');
  el.querySelector('.cqa-crit__row').setAttribute('aria-expanded', 'true');
  if (scroll) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function bind(main) {
  const res = main.querySelector('#cqa-result');
  const tr = main.querySelector('#cqa-transcript');

  main.querySelector('.cqa-conv').addEventListener('click', (ev) => {
    const go = ev.target.closest('[data-goto]');
    if (go) { scrollToMsg(main, Number(go.dataset.goto)); return; }
    const rg = ev.target.closest('[data-range]');
    if (rg) {
      const [a, b] = rg.dataset.range.split('-').map(Number);
      for (let i = a; i <= b; i++) main.querySelector(`#msg-${i}`)?.classList.add('is-expected');
      scrollToMsg(main, a, { flash: false });
      setTimeout(() => main.querySelectorAll('.is-expected').forEach((x) => x.classList.remove('is-expected')), 2400);
      return;
    }
    const mark = ev.target.closest('[data-mark]');
    if (mark) { openCrit(main, mark.dataset.mark); return; }
    const seg = ev.target.closest('[data-wseg]');
    if (seg) { openCrit(main, seg.dataset.wseg); return; }
    const seek = ev.target.closest('[data-seek]');
    if (seek && player) { player.seek(Number(seek.dataset.seek)); return; }
    const row = ev.target.closest('.cqa-crit__row');
    if (row) {
      const box = row.closest('.cqa-crit'); const open = !box.classList.contains('is-open');
      box.classList.toggle('is-open', open); row.setAttribute('aria-expanded', String(open)); return;
    }
    const apBtn = ev.target.closest('[data-appeal]');
    if (apBtn) { appealModal(main, apBtn.dataset.appeal); return; }
    if (ev.target.closest('#cqa-toggle-pre')) {
      const pre = main.querySelector('#cqa-pre'); const hidden = pre.classList.toggle('cqa-collapsed');
      ev.target.closest('#cqa-toggle-pre').innerHTML = `${icon(hidden ? 'down' : 'up')}${hidden ? 'Показать' : 'Скрыть'} переписку с ботом`;
    }
  });

  res?.addEventListener('mouseover', (ev) => { const c = ev.target.closest('[data-crit]'); if (c) light(main, c.dataset.crit, true); });
  res?.addEventListener('mouseout', (ev) => { const c = ev.target.closest('[data-crit]'); if (c) light(main, c.dataset.crit, false); });
  tr?.addEventListener('mouseover', (ev) => { const m = ev.target.closest('[data-mark]'); if (m) light(main, m.dataset.mark, true); });
  tr?.addEventListener('mouseout', (ev) => { const m = ev.target.closest('[data-mark]'); if (m) light(main, m.dataset.mark, false); });
  main.querySelector('.cqa-weights')?.addEventListener('mouseover', (ev) => { const s = ev.target.closest('[data-wseg]'); if (s) light(main, s.dataset.wseg, true); });
  main.querySelector('.cqa-weights')?.addEventListener('mouseout', (ev) => { const s = ev.target.closest('[data-wseg]'); if (s) light(main, s.dataset.wseg, false); });
}

function appealModal(main, critId) {
  const meta = critMeta(ctx.checklist);
  const options = ctx.checklist.criteria.filter((c) => c.type !== 'flag').map((c) => `<option value="${c.id}" ${c.id === critId ? 'selected' : ''}>${meta.get(c.id).num} ${esc(c.title)}</option>`).join('');
  const back = document.createElement('div');
  back.className = 'cqa-modal-back';
  back.innerHTML = `
    <form class="cqa-modal" role="dialog" aria-modal="true" aria-labelledby="cqa-ap-title">
      <div class="cqa-modal__head"><h2 id="cqa-ap-title">Оспорить оценку</h2><p class="cqa-muted" style="margin-top:4px">Проверяющий увидит оценку, ваш комментарий и фрагмент диалога</p></div>
      <div class="cqa-modal__body">
        <div class="cqa-field"><label for="cqa-ap-crit">Критерий</label><select class="cqa-select" id="cqa-ap-crit">${options}</select></div>
        <div class="cqa-field"><label for="cqa-ap-text">Комментарий</label><textarea class="cqa-textarea" id="cqa-ap-text" placeholder="Например: в реплике #8 я назвал следующий шаг и попрощался"></textarea><span class="cqa-field__err" id="cqa-ap-err" hidden>Опишите, с чем вы не согласны</span></div>
      </div>
      <div class="cqa-modal__foot"><button type="button" class="cqa-btn" data-close>Отмена</button><button type="submit" class="cqa-btn cqa-btn--primary">Отправить апелляцию</button></div>
    </form>`;
  document.querySelector('.cqa-root').appendChild(back);
  const close = () => back.remove();
  back.addEventListener('click', (e) => { if (e.target === back || e.target.closest('[data-close]')) close(); });
  back.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  const ta = back.querySelector('#cqa-ap-text'); const err = back.querySelector('#cqa-ap-err');
  ta.addEventListener('input', () => { err.hidden = true; });
  ta.focus();
  back.querySelector('form').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (ta.value.trim().length < 5) { err.hidden = false; ta.focus(); return; }
    const btn = back.querySelector('[type="submit"]'); btn.disabled = true;
    try {
      await api.createAppeal({ evaluation_id: ctx.ev.evaluation_id, criterion_id: back.querySelector('#cqa-ap-crit').value, comment: ta.value.trim() });
      close(); toast('Апелляция отправлена проверяющему');
      await load(main, ctx.conv.conversation_id); refreshAppealsCount();
    } catch (er) { toast(er.message, 'bad'); btn.disabled = false; }
  });
}

/* ---------- Рендер ---------- */

async function load(main, id) {
  const data = await api.getConversation(id);
  const conv = { ...data.conversation, transcript: withOffsets(markHold(data.conversation.transcript)) };
  const ev = data.evaluation;
  const checklist = await api.getChecklist(ev?.checklist_id || 'default', ev?.checklist_version);
  ctx = { conv, ev, checklist, appeals: data.appeals || [], main };
  const meta = critMeta(checklist);
  const role = store.get().role;
  const ids = await orderedIds();
  const pos = ids.indexOf(id);
  const prev = pos > 0 ? ids[pos - 1] : null; const next = pos >= 0 && pos < ids.length - 1 ? ids[pos + 1] : null;
  const isCall = conv.channel === 'voice call';
  const opName = conv.operator_name || null;

  main.innerHTML = `
    <div class="cqa-conv">
      <div class="cqa-conv__bar">
        <a class="cqa-btn cqa-btn--ghost cqa-btn--sm" href="#/list">${icon('back')}Диалоги</a>
        <div class="cqa-conv__title">
          <h1>${esc(conv.conversation_id)}</h1>
          <span class="cqa-badge">${icon(isCall ? 'phone' : 'chat', 'cqa-ico--sm')}${esc(CHANNEL[conv.channel] || conv.channel)}</span>
          <span class="cqa-badge">${esc(LANG[conv.language] || conv.language)}</span>
          ${opName ? `<span class="cqa-muted">Оператор ${esc(opName)}</span>` : ''}
        </div>
        <div class="cqa-conv__nav">
          ${ev && role === 'reviewer' ? `<button type="button" class="cqa-btn cqa-btn--sm" id="cqa-reeval">${icon('refresh')}Переоценить</button>` : ''}
          <span class="cqa-num">${pos >= 0 ? `${pos + 1} из ${ids.length}` : ''}</span>
          <a class="cqa-btn cqa-btn--sm cqa-btn--icon" ${prev ? `href="#/c/${encodeURIComponent(prev)}"` : 'aria-disabled="true" style="opacity:.4;pointer-events:none"'} aria-label="Предыдущий диалог" title="Предыдущий (K)">${icon('up')}</a>
          <a class="cqa-btn cqa-btn--sm cqa-btn--icon" ${next ? `href="#/c/${encodeURIComponent(next)}"` : 'aria-disabled="true" style="opacity:.4;pointer-events:none"'} aria-label="Следующий диалог" title="Следующий (J)">${icon('down')}</a>
        </div>
      </div>
      <nav class="cqa-timeline" aria-label="События диалога">${timelineHtml(conv, ev, meta)}</nav>
      <div class="cqa-conv__grid">
        <section class="cqa-pane" aria-label="Транскрипт">
          <div class="cqa-pane__head"><h2>Транскрипт</h2><span class="cqa-faint">${conv.transcript.length} ${plural(conv.transcript.length, ['реплика', 'реплики', 'реплик'])}</span><span class="cqa-faint" style="margin-left:auto">Номера слева — критерии, которые ссылаются на реплику</span></div>
          ${isCall ? playerHtml(conv, ev, meta) : ''}
          <div class="cqa-pane__body cqa-scroll"><div class="cqa-transcript" id="cqa-transcript">${transcriptHtml(conv, ev, meta)}</div></div>
        </section>
        <section class="cqa-pane" aria-label="Результат оценки">
          <div class="cqa-pane__head"><h2>Оценка</h2>${ev ? `<span class="cqa-faint">${esc(checklist.name)}, версия ${ev.checklist_version}</span>` : ''}</div>
          <div class="cqa-pane__body cqa-scroll"><div class="cqa-result" id="cqa-result">${ev ? resultHtml(ev, checklist, meta, ctx.appeals, role) : emptyResultHtml(role)}</div></div>
        </section>
      </div>
    </div>`;

  player?.stop?.();
  player = isCall ? setupPlayer(main, conv) : null;
  bind(main);

  const evalNow = async (btnSel) => {
    const res = main.querySelector('#cqa-result');
    res.innerHTML = evaluatingHtml(checklist);
    try { await api.evaluate(id); toast('Оценка готова'); await load(main, id); }
    catch (err) { toast(err.message, 'bad'); res.innerHTML = emptyResultHtml(role); }
  };
  main.querySelector('#cqa-eval-now')?.addEventListener('click', evalNow);
  main.querySelector('#cqa-reeval')?.addEventListener('click', evalNow);

  if (keyHandler) document.removeEventListener('keydown', keyHandler);
  keyHandler = (e) => {
    if (e.target.matches('input, textarea, select') || document.querySelector('.cqa-modal-back')) return;
    if (e.key === 'Escape') location.hash = '#/list';
    else if (e.key === 'j' && next) location.hash = `#/c/${encodeURIComponent(next)}`;
    else if (e.key === 'k' && prev) location.hash = `#/c/${encodeURIComponent(prev)}`;
    else if (/^[1-9]$/.test(e.key)) { const c = checklist.criteria[Number(e.key) - 1]; if (c) { openCrit(main, c.id); light(main, c.id, true); setTimeout(() => light(main, c.id, false), 1200); } }
  };
  document.addEventListener('keydown', keyHandler);
}

let unsub = null;
export async function render(main, { id }) {
  await load(main, id);
  unsub = store.subscribe(() => load(main, id));
}

export function destroy() {
  document.removeEventListener('keydown', keyHandler);
  player?.stop?.(); player = null;
  unsub?.(); unsub = null;
}

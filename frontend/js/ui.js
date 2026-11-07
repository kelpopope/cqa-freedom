// UI-помощники: модальные окна, поповеры, анимация чисел и полос.
import { icon } from './icons.js';
import { esc } from './utils.js';

const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const rootEl = () => document.querySelector('.cqa-root');

/** Плавно «набегает» число от 0 до значения. */
export function countUp(el, to, { decimals = 2, duration = 700, suffix = '' } = {}) {
  if (!el || to == null || Number.isNaN(to)) return;
  const fmt = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  if (reduced()) { el.textContent = fmt.format(to) + suffix; return; }
  const t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / duration);
    const e = 1 - (1 - k) ** 3;
    el.textContent = fmt.format(to * e) + suffix;
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/** Полосы с data-w="55%" растут от нуля после отрисовки. */
export function growBars(scope) {
  const els = scope.querySelectorAll('[data-w]');
  requestAnimationFrame(() => requestAnimationFrame(() => els.forEach((el) => { el.style.width = el.dataset.w; })));
}

/** Модальное окно. body/foot — HTML. Возвращает { el, close }. */
export function openModal({ title, sub = '', body = '', foot = '', wide = false, onClose } = {}) {
  const back = document.createElement('div');
  back.className = 'cqa-modal-back';
  back.innerHTML = `
    <div class="cqa-modal ${wide ? 'cqa-modal--wide' : ''}" role="dialog" aria-modal="true" aria-labelledby="cqa-m-title">
      <div class="cqa-modal__head"><div><h2 id="cqa-m-title">${esc(title)}</h2>${sub ? `<p>${sub}</p>` : ''}</div><button type="button" class="cqa-btn cqa-btn--ghost cqa-btn--icon" data-close aria-label="Закрыть">${icon('x')}</button></div>
      <div class="cqa-modal__body">${body}</div>
      ${foot ? `<div class="cqa-modal__foot">${foot}</div>` : ''}
    </div>`;
  const prevFocus = document.activeElement;
  rootEl().appendChild(back);
  const close = () => { back.remove(); document.removeEventListener('keydown', onKey); prevFocus?.focus?.(); onClose?.(); };
  const onKey = (e) => {
    if (e.key === 'Escape') close();
    if (e.key === 'Tab') {
      const f = [...back.querySelectorAll('button, [href], input, select, textarea')].filter((x) => !x.disabled);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f.at(-1).focus(); }
      else if (!e.shiftKey && document.activeElement === f.at(-1)) { e.preventDefault(); f[0].focus(); }
    }
  };
  document.addEventListener('keydown', onKey);
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  back.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', close));
  setTimeout(() => (back.querySelector('[autofocus]') || back.querySelector('input, textarea, select') || back.querySelector('.cqa-modal__foot .cqa-btn--primary') || back.querySelector('[data-close]'))?.focus(), 30);
  return { el: back, close };
}

/** Поповер-меню у кнопки. items: [{ value, label, hint, checked }] */
export function openPopover(anchor, items, onPick, { align = 'left', up = false } = {}) {
  document.querySelectorAll('.cqa-pop').forEach((p) => p.remove());
  const pop = document.createElement('div');
  pop.className = 'cqa-pop';
  pop.setAttribute('role', 'menu');
  pop.innerHTML = items.map((it) => `<button type="button" class="cqa-pop__item" role="menuitemradio" aria-checked="${!!it.checked}" data-v="${esc(it.value)}"><span>${esc(it.label)}${it.hint ? `<small>${esc(it.hint)}</small>` : ''}</span>${it.checked ? `<span style="margin-left:auto">${icon('check', 'cqa-ico--sm')}</span>` : ''}</button>`).join('');
  rootEl().appendChild(pop);
  const r = anchor.getBoundingClientRect();
  const pr = pop.getBoundingClientRect();
  pop.style.position = 'fixed';
  pop.style.left = `${Math.max(8, Math.min(window.innerWidth - pr.width - 8, align === 'right' ? r.right - pr.width : r.left))}px`;
  pop.style.top = up ? `${Math.max(8, r.top - pr.height - 6)}px` : `${r.bottom + 6}px`;
  const close = () => { pop.remove(); document.removeEventListener('mousedown', outside, true); document.removeEventListener('keydown', key); };
  const outside = (e) => { if (!pop.contains(e.target) && !anchor.contains(e.target)) close(); };
  const key = (e) => { if (e.key === 'Escape') { close(); anchor.focus(); } };
  setTimeout(() => { document.addEventListener('mousedown', outside, true); document.addEventListener('keydown', key); }, 0);
  pop.querySelectorAll('[data-v]').forEach((b) => b.addEventListener('click', () => { close(); onPick(b.dataset.v); }));
  pop.querySelector('[aria-checked="true"]')?.focus() || pop.querySelector('button')?.focus();
  return close;
}

export function relTime(iso) {
  if (!iso) return '';
  // Время без пояса (SQLite datetime('now')) — это UTC; ISO с поясом разбирается как есть.
  const s = String(iso);
  const d = new Date(/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s) ? `${s.replace(' ', 'T')}Z` : s); const now = new Date();
  const mins = Math.round((now - d) / 60000);
  if (mins < 1) return 'только что';
  if (mins < 60) return `${mins} мин назад`;
  const sameDay = d.toDateString() === now.toDateString();
  const t = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  return sameDay ? `сегодня, ${t}` : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }) + `, ${t}`;
}

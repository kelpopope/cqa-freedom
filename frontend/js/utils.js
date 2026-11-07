// Общие помощники: экранирование, форматы, подписи.
import { CONFIG } from './config.js';
import { icon } from './icons.js';

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const nf2 = new Intl.NumberFormat(CONFIG.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const nf0 = new Intl.NumberFormat(CONFIG.locale, { maximumFractionDigits: 0 });
export const fmt2 = (x) => (x == null ? '—' : nf2.format(x));
export const fmt0 = (x) => (x == null ? '—' : nf0.format(x));
export const fmtMoney = (x) => (x == null ? '—' : `$${nf2.format(x)}`);

export function fmtDuration(sec) {
  if (sec == null) return '—';
  const m = Math.floor(sec / 60); const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function fmtTimestamp(ts) {
  const s = String(ts || '');
  if (/^\d{1,2}:\d{2}$/.test(s)) return s;
  // ISO у звонков: показываем исходное время из метки, без пересчёта в часовой пояс браузера.
  const m = s.match(/T(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : s;
}

export const LANG = { RU: 'Русский', KZ: 'Қазақша', EN: 'English' };
export const CHANNEL = { 'agent chat': 'Чат', 'voice call': 'Звонок' };
export const ROLE = { client: 'Клиент', operator: 'Оператор', bot: 'Бот', system: 'Система' };
export const GROSS = { insult: 'Оскорбление', private_service: 'Услуга в обход компании', confidential_disclosure: 'Раскрытие данных' };

export function scoreTone(total) {
  if (total == null) return 'na';
  if (total >= 85) return 'ok';
  if (total >= 60) return 'mid';
  return 'low';
}

/** Как показать ответ по критерию: подпись + класс чипа */
export function answerView(criterion, answer) {
  if (answer === 'na') return { label: 'N/A', cls: 'na' };
  if (criterion.type === 'flag') return answer === 'yes' ? { label: 'Выявлено', cls: 'no' } : { label: 'Нет', cls: 'yes' };
  if (criterion.type === 'binary') return answer === 'yes' ? { label: 'Да', cls: 'yes' } : { label: 'Нет', cls: 'no' };
  const max = Math.max(...criterion.scale.map((s) => s.value));
  const v = Number(answer);
  return { label: `${v} из ${max}`, cls: v >= max ? 'yes' : v === 0 ? 'no' : 'mid' };
}

export function scoreBar(total, { size = 'md' } = {}) {
  if (total == null) return `<span class="cqa-faint">—</span>`;
  const tone = scoreTone(total);
  return `<span class="cqa-score cqa-score--${tone} cqa-score--${size}"><span class="cqa-score__bar"><span class="cqa-score__fill" style="width:${Math.max(2, total)}%"></span></span><span>${fmt2(total)}</span></span>`;
}

export function debounce(fn, ms = 200) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

let toastBox;
export function toast(text, tone = 'ok') {
  if (!toastBox) {
    toastBox = document.createElement('div');
    toastBox.className = 'cqa-toasts';
    toastBox.setAttribute('role', 'status');
    document.querySelector('.cqa-root').appendChild(toastBox);
  }
  const el = document.createElement('div');
  el.className = `cqa-toast cqa-toast--${tone}`;
  el.innerHTML = `${icon(tone === 'ok' ? 'check' : 'alert')}<span>${esc(text)}</span>`;
  toastBox.appendChild(el);
  setTimeout(() => el.classList.add('is-leaving'), 3200);
  setTimeout(() => el.remove(), 3450);
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

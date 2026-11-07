// Состояние приложения: роль, тема, фильтры очереди. Роль и тема переживают перезагрузку.
const KEY = 'cqa.prefs.v2';

function load() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; }
}

export const ROLES = {
  manager: { label: 'Руководитель', hint: 'Обзор качества и рисков', person: 'Айгерим Нурланова', initials: 'АН' },
  qa: { label: 'Контроль качества', hint: 'Проверка оценок и апелляций', person: 'Елена Морозова', initials: 'ЕМ' },
  operator: { label: 'Оператор', hint: 'Свои оценки и апелляции', person: 'Кирилл', initials: 'К' },
};

const saved = load();
const state = {
  role: ROLES[saved.role] ? saved.role : 'qa',
  theme: saved.theme === 'dark' ? 'dark' : 'light',
  queue: { view: 'all', q: '', lang: '', channel: '' },
  groupId: typeof saved.groupId === 'string' && saved.groupId ? saved.groupId : 'DEV',
};

const subs = new Set();

export const store = {
  get: () => state,
  set(patch) {
    Object.assign(state, patch);
    try { localStorage.setItem(KEY, JSON.stringify({ role: state.role, theme: state.theme, groupId: state.groupId })); } catch { /* приватный режим */ }
    subs.forEach((fn) => fn(state, patch));
  },
  subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
};

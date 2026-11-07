// Небольшое состояние приложения: роль, тема, фильтры списка. Переживает перезагрузку (localStorage).
const KEY = 'cqa.prefs.v1';

function load() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; }
}

const state = {
  role: 'reviewer',        // 'operator' | 'reviewer'
  theme: 'light',
  filters: { q: '', lang: '', channel: '', risk: false, review: false, status: '' },
  sort: 'risk',
  ...load(),
};

const subs = new Set();

export const store = {
  get: () => state,
  set(patch) {
    Object.assign(state, patch);
    try { localStorage.setItem(KEY, JSON.stringify({ role: state.role, theme: state.theme, sort: state.sort })); } catch { /* приватный режим */ }
    subs.forEach((fn) => fn(state));
  },
  subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
};

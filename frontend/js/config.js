// Настройки компонента. Хост (например, форма Dynamics 365) может задать window.CQA_CONFIG до загрузки.
// Режим: ?mock=1 / ?live=1 в адресе переопределяет значение по умолчанию.
const params = new URLSearchParams(location.search);
const defaults = {
  apiBase: '/api',
  // Моки по умолчанию, когда интерфейс открыт отдельно (dev-сервер), живой API — когда его раздаёт FastAPI.
  useMocks: location.pathname.includes('/frontend/'),
  locale: 'ru-RU',
  // Оформление: 'classic' (светлое, боковое меню) или 'v2' (тёмное, верхнее меню) — v2.html задаёт его сам.
  skin: 'classic',
};
export const CONFIG = { ...defaults, ...(window.CQA_CONFIG || {}) };
if (params.has('mock')) CONFIG.useMocks = params.get('mock') !== '0';
if (params.has('live')) CONFIG.useMocks = false;
if (params.has('skin')) CONFIG.skin = params.get('skin');

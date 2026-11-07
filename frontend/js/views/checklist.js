// Экран «Чек-лист» — следующий шаг (B7). Пока показывает текущую конфигурацию только для чтения.
import { api } from '../api.js';
import { icon } from '../icons.js';
import { esc } from '../utils.js';

const TYPE = { binary: 'Да / Нет', scale: 'Шкала', flag: 'Флаг, без баллов' };

export async function render(main) {
  const cl = await api.getChecklist('default');
  main.innerHTML = `
    <div class="cqa-page-head"><div><h1>${esc(cl.name)}</h1><p>Версия ${cl.version}. Редактирование появится на следующем шаге</p></div></div>
    <div class="cqa-list">
      <table class="cqa-table">
        <thead><tr><th>№</th><th>Критерий</th><th>Тип</th><th class="ta-r">Вес</th><th>N/A</th></tr></thead>
        <tbody>${cl.criteria.map((c, i) => `<tr style="cursor:default"><td class="cqa-faint cqa-num">${String(i + 1).padStart(2, '0')}</td><td><div class="cqa-cell-id">${esc(c.title)}</div><div class="cqa-cell-sub">${esc(c.description)}</div></td><td>${TYPE[c.type]}</td><td class="ta-r cqa-num">${c.type === 'flag' ? '—' : c.weight}</td><td>${c.na_allowed ? `<span class="cqa-chip cqa-chip--na" title="${esc(c.na_condition || '')}">Разрешён</span>` : '<span class="cqa-faint">Нет</span>'}</td></tr>`).join('')}</tbody>
      </table>
    </div>`;
}

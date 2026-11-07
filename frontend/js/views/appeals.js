// Экран «Апелляции» — следующий шаг (B9). Пока — список обращений.
import { api } from '../api.js';
import { icon } from '../icons.js';
import { esc } from '../utils.js';

export async function render(main) {
  const list = await api.listAppeals();
  main.innerHTML = `
    <div class="cqa-page-head"><div><h1>Апелляции</h1><p>Рассмотрение и решение появятся на следующем шаге</p></div></div>
    ${list.length ? `<div class="cqa-list"><table class="cqa-table"><thead><tr><th>Диалог</th><th>Критерий</th><th>Комментарий</th><th>Статус</th></tr></thead><tbody>
      ${list.map((a) => `<tr data-id="${esc(a.conversation_id)}"><td class="cqa-cell-id">${esc(a.conversation_id)}</td><td>${esc(a.criterion_id)}</td><td>${esc(a.comment)}</td><td>${a.status === 'open' ? '<span class="cqa-badge cqa-badge--info">На рассмотрении</span>' : '<span class="cqa-badge">Решено</span>'}</td></tr>`).join('')}
    </tbody></table></div>` : `<div class="cqa-empty"><div class="cqa-empty__icon">${icon('scale')}</div><b>Апелляций нет</b></div>`}`;
  main.querySelectorAll('tr[data-id]').forEach((tr) => tr.addEventListener('click', () => { location.hash = `#/c/${encodeURIComponent(tr.dataset.id)}`; }));
}

// Экран «Обзор» для руководителя: картина качества за 10 секунд.
import { api } from '../api.js';
import { store } from '../store.js';
import { icon } from '../icons.js';
import { esc, fmt2, fmtDuration, GROSS, LANG } from '../utils.js';
import { countUp, growBars } from '../ui.js';

const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);
const pct1 = (x) => `${(x * 100).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} %`;

// Сверка с ручной разметкой (GET /api/metrics/accuracy). Эталон есть только у DEV — для других наборов блока нет.
function calibration(acc, s) {
  if (!acc || !acc.labeled) return '';
  const titles = new Map(s.criteria.map((c) => [c.criterion_id, c.title]));
  const crit = Object.entries(acc.per_criterion || {});
  const g = acc.gross || {};
  const stat = (v, label, hint) => `<div class="cqa-calib__stat"><b class="cqa-num">${v}</b><span>${label}</span><small>${hint}</small></div>`;
  return `
    <section class="cqa-card" style="margin-top:14px">
      <div class="cqa-card__head"><h2>Точность AI-оценки</h2><span class="cqa-faint" style="margin-left:auto;font-size:13px">сверка с ручной разметкой команды, ${acc.labeled} диалогов ${esc(acc.group_id)}</span></div>
      <div class="cqa-card__body cqa-calib">
        <div class="cqa-calib__stats">
          ${stat(pct1(acc.overall), 'Совпадение ответов', `${acc.labeled * crit.length} ответов по ${crit.length} критериям`)}
          ${acc.overall_confident != null ? stat(pct1(acc.overall_confident), 'В однозначных случаях', 'без спорных случаев, помеченных при разметке') : ''}
          ${acc.kappa != null ? stat(acc.kappa.toLocaleString('ru-RU', { maximumFractionDigits: 2 }), 'Согласие, каппа Коэна', 'выше 0,8 — почти полное согласие') : ''}
          ${g.tp != null ? stat(`${g.tp} из ${g.tp + g.fn}`, 'Грубые нарушения найдены', `ложных срабатываний: ${g.fp}`) : ''}
          ${acc.total_mae != null ? stat(`±${fmt2(acc.total_mae)}`, 'Отклонение итога', 'баллов в среднем от эталона') : ''}
          ${acc.per_language ? stat(Object.entries(acc.per_language).map(([l, v]) => `${l} ${Math.round(v * 100)}`).join(' · '), 'Паритет языков', 'совпадение, %') : ''}
        </div>
        <div class="cqa-calib__rows">
          ${crit.map(([id, v]) => `<div class="cqa-calib__row"><span>${esc(titles.get(id) || (id === 'c08' ? 'Грубые ошибки' : id))}</span><div class="cqa-langs__bar"><i data-w="${v * 100}%"></i></div><b class="cqa-num">${pct1(v)}</b></div>`).join('')}
        </div>
      </div>
      <p class="cqa-faint" style="font-size:12px;padding:0 20px 16px">Эталон — ручная разметка DEV, спорные ответы помечены отдельно. Модель на этих ответах не обучалась: они служат проверкой здравого смысла. Пересчитывается по последним оценкам.</p>
    </section>`;
}

export async function render(main) {
  const [s, acc] = await Promise.all([api.stats(), api.accuracy ? api.accuracy().catch(() => null) : null]);
  const worst = s.criteria.filter((c) => c.applicable).slice(0, 6);
  const maxRate = Math.max(0.01, ...worst.map((c) => c.fail_rate));
  const B = s.buckets; const nb = B.low + B.mid + B.high || 1;
  main.innerHTML = `
    <div class="cqa-page-head"><h1>Обзор качества</h1><span class="cqa-page-head__sub">Набор ${esc(store.get().groupId)}, оценено ${s.evaluated} из ${s.total} диалогов</span>
      <div class="cqa-page-head__actions"><a class="cqa-btn cqa-btn--sm" href="#/dialogs">${icon('chat', 'cqa-ico--sm')}Все диалоги</a></div></div>

    <div class="cqa-kpis cqa-stagger">
      <div class="cqa-card cqa-kpi cqa-kpi--lead" style="--i:0"><div class="cqa-kpi__label">Средний балл</div><div class="cqa-kpi__value"><span data-count="${s.avg ?? ''}">${s.avg == null ? '—' : fmt2(s.avg)}</span> <small>/ 100</small></div><div class="cqa-kpi__hint">по ${s.evaluated} оценённым диалогам</div></div>
      <a class="cqa-card cqa-kpi cqa-kpi--bad" style="--i:1" href="#/dialogs" data-view="risk"><div class="cqa-kpi__label">Грубые нарушения</div><div class="cqa-kpi__value"><span data-count="${s.gross}" data-dec="0">${s.gross}</span> <small>${pct(s.gross, s.evaluated)} %</small></div><div class="cqa-kpi__hint">требуют реакции руководителя</div></a>
      <a class="cqa-card cqa-kpi" style="--i:2" href="#/dialogs" data-view="attention"><div class="cqa-kpi__label">Требуют проверки</div><div class="cqa-kpi__value"><span data-count="${s.needs_review}" data-dec="0">${s.needs_review}</span></div><div class="cqa-kpi__hint">низкая уверенность или апелляция</div></a>
      <div class="cqa-card cqa-kpi" style="--i:3"><div class="cqa-kpi__label">Проверено людьми</div><div class="cqa-kpi__value"><span data-count="${s.reviewed}" data-dec="0">${s.reviewed}</span> <small>из ${s.evaluated}</small></div><div class="cqa-kpi__hint">открытых апелляций: ${s.appeals_open}</div></div>
    </div>

    <div class="cqa-grid2">
      <section class="cqa-card">
        <div class="cqa-card__head"><h2>Где операторы теряют баллы</h2><span class="cqa-faint" style="margin-left:auto;font-size:13px">доля диалогов с неполным баллом</span></div>
        <div class="cqa-card__body"><div class="cqa-rank">
          ${worst.map((c, i) => `<div class="cqa-rank__row"><span class="cqa-rank__n">${String(i + 1).padStart(2, '0')}</span><div><div class="cqa-rank__t"><span>${esc(c.title)}</span><small>${c.failed} из ${c.applicable}${c.na ? `, N/A ${c.na}` : ''}</small></div><div class="cqa-rank__bar"><i data-w="${(c.fail_rate / maxRate) * 100}%" style="opacity:${0.45 + 0.55 * (c.fail_rate / maxRate)}"></i></div></div><span class="cqa-rank__v">${pct(c.failed, c.applicable)} %</span></div>`).join('')}
        </div></div>
      </section>
      <section class="cqa-card">
        <div class="cqa-card__head"><h2>Распределение итогов</h2></div>
        <div class="cqa-card__body">
          <div class="cqa-dist" aria-label="Распределение итогов"><i style="flex:${B.low} 1 0;background:var(--cqa-bad)"></i><i style="flex:${B.mid} 1 0;background:var(--cqa-warn-dot)"></i><i style="flex:${B.high} 1 0;background:var(--cqa-green)"></i></div>
          <div class="cqa-dist__legend">
            <div><b>${B.low}</b><span style="background:var(--cqa-bad)"></span>ниже 60</div>
            <div><b>${B.mid}</b><span style="background:var(--cqa-warn-dot)"></span>60–84</div>
            <div><b>${B.high}</b><span style="background:var(--cqa-green)"></span>85 и выше</div>
          </div>
          <div class="cqa-langs">
            ${s.by_language.filter((l) => l.n).map((l) => `<div class="cqa-langs__row"><span>${esc(LANG[l.language] || l.language)}</span><div class="cqa-langs__bar"><i data-w="${l.avg ?? 0}%"></i></div><b class="cqa-num" style="text-align:right">${fmt2(l.avg)}</b></div>`).join('')}
          </div>
          <p class="cqa-faint" style="font-size:12px;margin-top:10px">Средний балл по языку диалога: один чек-лист для RU, KZ и EN</p>
          ${s.sla?.measured ? `<div class="cqa-sla-sum"><div><b class="cqa-num ${s.sla.breached ? 'cqa-bad-text' : ''}">${s.sla.breached} из ${s.sla.measured}</b><span>нарушений SLA первой реакции (${fmtDuration(s.sla.threshold_sec)})</span></div><div><b class="cqa-num">${fmtDuration(s.sla.avg_wait_sec)}</b><span>в среднем до ответа оператора, максимум ${fmtDuration(s.sla.max_wait_sec)}</span></div></div>` : ''}
        </div>
      </section>
    </div>

    <section class="cqa-card" style="margin-top:14px">
      <div class="cqa-card__head"><h2>Требуют реакции</h2><span class="cqa-badge cqa-badge--bad">${s.risky.length}</span><a class="cqa-link cqa-link--accent" style="margin-left:auto" href="#/dialogs" data-view="risk">Открыть в проверке</a></div>
      <div class="cqa-risk-list">
        ${s.risky.length ? s.risky.map((r) => `<a class="cqa-risk-row" href="#/dialogs/${encodeURIComponent(r.conversation_id)}"><div><b>${esc(r.conversation_id)}</b><small>${esc(r.headline || '')}</small></div><span class="cqa-badge cqa-badge--bad">${esc((r.gross_types || []).map((t) => GROSS[t] || t).join(', ') || 'Нарушение')}</span><span class="cqa-num" style="font-weight:600">${fmt2(r.total)}</span></a>`).join('') : '<div class="cqa-empty" style="padding:28px">Грубых нарушений нет</div>'}
      </div>
    </section>${calibration(acc, s)}`;

  main.querySelectorAll('[data-count]').forEach((el) => { if (el.dataset.count !== '') countUp(el, Number(el.dataset.count), { decimals: el.dataset.dec === '0' ? 0 : 2 }); });
  growBars(main);
  main.querySelectorAll('[data-view]').forEach((a) => a.addEventListener('click', () => { store.get().queue.view = a.dataset.view; }));
}

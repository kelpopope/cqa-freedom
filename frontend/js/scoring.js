// Расчёт баллов по правилам чек-листа (docs/API_CONTRACT.md §3).
// Используется в режиме моков и для подсказки «откуда взялся балл».
// В живом режиме источник правды — бэкенд (scoring.py), фронт только отображает.

const round2 = (x) => Math.round((x + Number.EPSILON) * 100) / 100;

export function rawPoints(criterion, answer) {
  if (criterion.type === 'flag' || answer === 'na' || answer == null) return 0;
  if (criterion.type === 'binary') return answer === 'yes' ? criterion.weight : 0;
  if (criterion.type === 'scale') {
    const max = Math.max(...criterion.scale.map((s) => s.value));
    const v = Number(answer);
    return max && Number.isFinite(v) ? (criterion.weight * v) / max : 0;
  }
  return 0;
}

/**
 * @param checklist  чек-лист (criteria, sections)
 * @param answers    { [criterion_id]: answer }  — "yes" | "no" | "na" | число
 * @returns { criteria: {id: {raw_points, points, weight, effective_weight, applicable}}, sections, total, applicableWeight, naIds }
 */
export function computeScores(checklist, answers) {
  const scored = checklist.criteria.filter((c) => c.type !== 'flag');
  const applicable = scored.filter((c) => answers[c.id] !== 'na' && answers[c.id] != null);
  const W = applicable.reduce((s, c) => s + c.weight, 0);
  const k = W > 0 ? 100 / W : 0;

  const criteria = {};
  for (const c of checklist.criteria) {
    const isApplicable = applicable.includes(c);
    const raw = rawPoints(c, answers[c.id]);
    criteria[c.id] = {
      raw_points: round2(raw),
      points: isApplicable ? round2(raw * k) : 0,
      weight: c.weight,
      effective_weight: isApplicable ? round2(c.weight * k) : 0,
      applicable: isApplicable,
    };
  }

  const sections = (checklist.sections || []).map((s) => {
    const own = applicable.filter((c) => c.section_id === s.id);
    return {
      section_id: s.id,
      points: round2(own.reduce((sum, c) => sum + rawPoints(c, answers[c.id]) * k, 0)),
      max_points: round2(own.reduce((sum, c) => sum + c.weight * k, 0)),
    };
  });

  const total = W > 0 ? round2(applicable.reduce((sum, c) => sum + rawPoints(c, answers[c.id]) * k, 0)) : null;
  const naIds = scored.filter((c) => answers[c.id] === 'na').map((c) => c.id);
  return { criteria, sections, total, applicableWeight: W, naIds };
}

export function scaleMax(criterion) {
  return criterion.type === 'scale' ? Math.max(...criterion.scale.map((s) => s.value)) : null;
}

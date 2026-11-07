// Mock-API: ведёт себя как бэкенд по docs/API_CONTRACT.md, данные живут в памяти вкладки.
import { conversations as seedConversations, answerSets, convMeta, initiallyUnevaluated, initialAppeals } from './data.js';
import { computeScores } from '../scoring.js';
import { deriveTimeline, deriveBotContext, markHold, withOffsets } from '../derive.js';

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const clone = (x) => (x == null ? x : JSON.parse(JSON.stringify(x)));
const nowIso = () => new Date().toISOString();
let seq = 1;
const uid = (p) => `${p}_${Date.now().toString(36)}${(seq++).toString(36)}`;

const db = { conversations: new Map(), checklists: [], evaluations: new Map(), history: new Map(), appeals: [], jobs: new Map(), ready: null };

async function loadChecklist() {
  for (const u of ['../data/checklists/default.json', '/data/checklists/default.json']) {
    try { const r = await fetch(u); if (r.ok) return await r.json(); } catch { /* следующий */ }
  }
  throw new Error('Не найден data/checklists/default.json');
}

const activeChecklist = (id = 'default') => {
  const all = db.checklists.filter((c) => c.checklist_id === id);
  return all.find((c) => c.is_active) || all.at(-1);
};
const checklistOf = (ev) => db.checklists.find((c) => c.checklist_id === ev.checklist_id && c.version === ev.checklist_version) || activeChecklist();
const evById = (id) => [...db.evaluations.values()].find((x) => x.evaluation_id === id);

function validateEvidence(conv, ev) {
  const norm = (s) => s.toLowerCase().replace(/[«»"“”.,!?—–:-]/g, ' ').replace(/\s+/g, ' ').trim();
  return (ev || []).every((x) => {
    const m = conv.transcript[x.index];
    return m && (!x.quote || norm(m.text).includes(norm(x.quote).slice(0, 40)));
  });
}

function fallbackAnswer(c) {
  if (c.type === 'scale') return Math.max(...c.scale.map((s) => s.value));
  if (c.type === 'flag') return 'no';
  return c.na_allowed ? 'na' : 'yes';
}

function buildEvaluation(conv, checklist, overrides = {}) {
  const set = answerSets[conv.conversation_id] || {};
  const meta = convMeta[conv.conversation_id] || {};
  const answers = {};
  for (const c of checklist.criteria) answers[c.id] = overrides[c.id] ?? set[c.id]?.answer ?? fallbackAnswer(c);
  const scores = computeScores(checklist, answers);
  const criteria = checklist.criteria.map((c) => {
    const src = set[c.id] || {};
    const known = !!set[c.id];
    const evidence = src.evidence || [];
    const evidence_valid = validateEvidence(conv, evidence);
    const a = answers[c.id];
    return {
      criterion_id: c.id,
      answer: a,
      raw_points: scores.criteria[c.id].raw_points,
      points: scores.criteria[c.id].points,
      weight: c.weight,
      effective_weight: scores.criteria[c.id].effective_weight,
      explanation: src.explanation || (known ? null : 'Новый критерий: в демо-режиме модель не вызывается, поэтому стоит условный ответ. На сервере здесь будет реальная оценка.'),
      evidence,
      expected_range: src.expected_range || null,
      inline_note: src.inline_note || null,
      na_reason: a === 'na' ? (src.na_reason || 'Условие неприменимости выполнено.') : null,
      evidence_valid,
      confidence: src.confidence || (!known ? 'low' : evidence_valid ? 'high' : 'low'),
      uncertainty_reason: src.uncertainty_reason || (!known ? 'Критерий добавлен после оценки, ответ не проверен моделью' : null),
      source: c.type === 'flag' ? 'llm+rules' : 'llm',
      overridden: overrides[c.id] !== undefined,
    };
  });
  const flag = checklist.criteria.find((c) => c.type === 'flag');
  const flagRow = flag && criteria.find((x) => x.criterion_id === flag.id);
  const isFlag = flagRow?.answer === 'yes';
  const ev = {
    evaluation_id: uid('ev'),
    conversation_id: conv.conversation_id,
    checklist_id: checklist.checklist_id,
    checklist_version: checklist.version,
    status: 'done',
    model: { provider: 'openai', name: 'gpt-6.1-sol', temperature: 0, seed: 42, prompt_version: 'v3' },
    usage: { input_tokens: 5200 + conv.transcript.length * 40, output_tokens: 1150, cost_usd: 0.07, duration_ms: 3400 + conv.transcript.length * 40 },
    created_at: new Date(Date.now() - (meta.ago || 5) * 60000).toISOString(),
    headline: meta.headline || null,
    summary: meta.summary || null,
    criteria,
    sections: scores.sections,
    total: scores.total,
    applicable_weight: scores.applicableWeight,
    gross_error: { flag: isFlag, types: isFlag ? (set[flag.id]?.flag_types || []) : [], evidence: isFlag ? flagRow.evidence : [], rules_agree: true },
    needs_review: criteria.some((x) => x.confidence === 'low' || !x.evidence_valid),
    bot_context: deriveBotContext(conv.transcript),
    review: null,
    reproducibility: null,
  };
  ev.timeline = deriveTimeline(conv.transcript, ev);
  return ev;
}

function withFinal(cid) {
  const ev = db.evaluations.get(cid);
  if (!ev) return null;
  const changes = db.appeals.filter((a) => a.evaluation_id === ev.evaluation_id && a.status === 'resolved' && a.decision?.action === 'change');
  if (!changes.length) return { ...ev, final: { total: ev.total, overrides: [] } };
  const overrides = Object.fromEntries(changes.map((a) => [a.criterion_id, a.decision.new_answer]));
  const re = buildEvaluation(db.conversations.get(cid), checklistOf(ev), overrides);
  return {
    ...ev,
    final: {
      total: re.total,
      applicable_weight: re.applicable_weight,
      criteria: re.criteria,
      sections: re.sections,
      overrides: changes.map((a) => ({ criterion_id: a.criterion_id, from: a.original.answer, to: a.decision.new_answer, appeal_id: a.appeal_id, by: a.author_role })),
    },
  };
}

async function init() {
  if (db.ready) return db.ready;
  db.ready = (async () => {
    const cl = await loadChecklist();
    db.checklists.push({ ...cl, version: cl.version || 1, is_active: true, created_at: nowIso() });
    for (const c of seedConversations) db.conversations.set(c.conversation_id, { ...clone(c), transcript: withOffsets(markHold(clone(c.transcript))) });
    for (const c of db.conversations.values()) {
      if (initiallyUnevaluated.includes(c.conversation_id)) continue;
      const ev = buildEvaluation(c, activeChecklist());
      db.evaluations.set(c.conversation_id, ev);
      db.history.set(c.conversation_id, [ev]);
    }
    db.evaluations.get('demo-kz-001').review = { status: 'confirmed', by: 'Елена Морозова', at: nowIso() };
    db.evaluations.get('demo-ru-001').review = { status: 'confirmed', by: 'Елена Морозова', at: nowIso() };
    for (const a of initialAppeals) {
      const ev = db.evaluations.get(a.conversation_id);
      const row = ev.criteria.find((x) => x.criterion_id === a.criterion_id);
      db.appeals.push({ ...clone(a), evaluation_id: ev.evaluation_id, original: { answer: row.answer, points: row.points, raw_points: row.raw_points, total: ev.total }, decision: null });
    }
  })();
  return db.ready;
}

function listItem(c) {
  const ev = withFinal(c.conversation_id);
  const ap = db.appeals.filter((a) => a.conversation_id === c.conversation_id && a.author_role === 'operator');
  return {
    conversation_id: c.conversation_id, group_id: c.group_id, channel: c.channel, language: c.language,
    turns: c.transcript.length, operator_name: c.operator_name || null, duration_sec: c.transcript.at(-1)?.offset_sec ?? 0,
    evaluation: ev ? {
      evaluation_id: ev.evaluation_id, total: ev.total, final_total: ev.final.total, headline: ev.headline,
      gross_error: ev.gross_error.flag, gross_types: ev.gross_error.types, needs_review: ev.needs_review,
      checklist_version: ev.checklist_version, status: ev.status, created_at: ev.created_at, review: ev.review,
    } : null,
    appeal_status: ap.some((a) => a.status === 'open') ? 'open' : ap.length ? 'resolved' : null,
  };
}

function appealView(a) {
  const conv = db.conversations.get(a.conversation_id);
  const ev = evById(a.evaluation_id) || db.evaluations.get(a.conversation_id);
  const row = ev?.criteria.find((x) => x.criterion_id === a.criterion_id);
  const idx = (row?.evidence || []).map((x) => x.index);
  const from = Math.max(0, (idx.length ? Math.min(...idx) : 0) - 1);
  const to = Math.min(conv.transcript.length - 1, (idx.length ? Math.max(...idx) : 2) + 1);
  return {
    ...clone(a),
    conversation: { conversation_id: conv.conversation_id, operator_name: conv.operator_name, channel: conv.channel, language: conv.language },
    context: clone(conv.transcript.slice(from, to + 1)),
    criterion_result: clone(row),
    criterion_title: ev ? checklistOf(ev).criteria.find((c) => c.id === a.criterion_id)?.title : null,
    checklist_version: ev?.checklist_version,
    current_total: withFinal(a.conversation_id)?.final.total,
  };
}

export const mockApi = {
  mode: 'mock',

  async health() { await init(); return { ok: true, llm: { provider: 'openai', name: 'gpt-6.1-sol' }, mode: 'mock' }; },

  async importDataset(group_id) {
    await init(); await wait(700);
    return { group_id, imported: db.conversations.size, note: 'Демо-режим: используются встроенные диалоги' };
  },

  async listConversations() { await init(); await wait(90); return [...db.conversations.values()].map(listItem); },

  async getConversation(cid) {
    await init(); await wait(60);
    const conversation = db.conversations.get(cid);
    if (!conversation) throw new Error('Диалог не найден');
    return {
      conversation: clone(conversation),
      evaluation: clone(withFinal(cid)),
      appeals: db.appeals.filter((a) => a.conversation_id === cid).map(clone),
      history: (db.history.get(cid) || []).map((e) => ({ evaluation_id: e.evaluation_id, checklist_version: e.checklist_version, total: e.total, created_at: e.created_at })),
    };
  },

  async listChecklists() { await init(); return clone(db.checklists); },
  async getChecklist(id = 'default', version) {
    await init();
    return clone(version ? db.checklists.find((c) => c.checklist_id === id && c.version === Number(version)) : activeChecklist(id));
  },
  async saveChecklist(cl) {
    await init(); await wait(300);
    const versions = db.checklists.filter((c) => c.checklist_id === cl.checklist_id);
    const next = { ...clone(cl), version: Math.max(0, ...versions.map((v) => v.version)) + 1, is_active: true, created_at: nowIso() };
    versions.forEach((v) => { v.is_active = false; });
    db.checklists.push(next);
    return clone(next);
  },

  async evaluate(cid, { checklistId = 'default' } = {}) {
    await init();
    await wait(900 + Math.random() * 600);
    const ev = buildEvaluation(db.conversations.get(cid), activeChecklist(checklistId));
    ev.created_at = nowIso();
    db.evaluations.set(cid, ev);
    db.history.set(cid, [...(db.history.get(cid) || []), ev]);
    return clone(withFinal(cid));
  },

  async repeatEvaluation(evaluationId) {
    await init(); await wait(1200);
    const ev = evById(evaluationId);
    const again = buildEvaluation(db.conversations.get(ev.conversation_id), checklistOf(ev));
    const diff = ev.criteria.filter((x) => again.criteria.find((y) => y.criterion_id === x.criterion_id)?.answer !== x.answer).map((x) => x.criterion_id);
    ev.reproducibility = { runs: 2, agreement: 1 - diff.length / ev.criteria.length, differing_criteria: diff, checked_at: nowIso() };
    return clone(withFinal(ev.conversation_id));
  },

  async evaluateBatch({ conversation_ids } = {}) {
    await init();
    const ids = conversation_ids?.length ? conversation_ids : [...db.conversations.keys()];
    const job = { job_id: uid('job'), total: ids.length, done: 0, failed: 0, status: 'running' };
    db.jobs.set(job.job_id, job);
    (async () => {
      for (const cid of ids) {
        const ev = buildEvaluation(db.conversations.get(cid), activeChecklist());
        await wait(220);
        db.evaluations.set(cid, ev);
        db.history.set(cid, [...(db.history.get(cid) || []), ev]);
        job.done += 1;
      }
      job.status = 'done';
    })();
    return { job_id: job.job_id };
  },
  async getJob(id) { await wait(40); return clone(db.jobs.get(id)); },

  async reviewEvaluation(evaluationId, { action, reviewer = 'Елена Морозова' }) {
    await init(); await wait(250);
    const ev = evById(evaluationId);
    ev.review = action === 'confirm' ? { status: 'confirmed', by: reviewer, at: nowIso() } : null;
    return clone(withFinal(ev.conversation_id));
  },

  async listAppeals(status) {
    await init(); await wait(70);
    return db.appeals.filter((a) => !status || a.status === status).map(appealView);
  },

  async createAppeal({ evaluation_id, criterion_id, comment, proposed_answer = null, author_role = 'operator', author_name }) {
    await init(); await wait(200);
    const ev = evById(evaluation_id);
    const row = ev.criteria.find((x) => x.criterion_id === criterion_id);
    const conv = db.conversations.get(ev.conversation_id);
    const ap = {
      appeal_id: uid('ap'), evaluation_id, conversation_id: ev.conversation_id, criterion_id, comment, proposed_answer,
      author_role, author_name: author_name || (author_role === 'operator' ? conv.operator_name : 'Елена Морозова'),
      created_at: nowIso(), status: 'open',
      original: { answer: row.answer, points: row.points, raw_points: row.raw_points, total: withFinal(ev.conversation_id).final.total }, decision: null,
    };
    db.appeals.push(ap);
    return appealView(ap);
  },

  async decideAppeal(id, { action, new_answer, reason, reviewer = 'Елена Морозова' }) {
    await init(); await wait(250);
    const ap = db.appeals.find((a) => a.appeal_id === id);
    const before = withFinal(ap.conversation_id).final.total;
    ap.status = 'resolved';
    ap.decision = { action, new_answer: action === 'change' ? new_answer : ap.original.answer, reason, reviewer, decided_at: nowIso(), total_before: before };
    ap.decision.new_total = withFinal(ap.conversation_id).final.total;
    return appealView(ap);
  },

  /** Ручное изменение оценки проверяющим = апелляция от проверяющего, сразу решённая. */
  async overrideCriterion({ evaluation_id, criterion_id, new_answer, reason }) {
    const ap = await this.createAppeal({ evaluation_id, criterion_id, comment: reason, author_role: 'reviewer' });
    return this.decideAppeal(ap.appeal_id, { action: 'change', new_answer, reason });
  },

  async compare({ from_version, to_version }) {
    await init(); await wait(300);
    const from = db.checklists.find((c) => c.version === Number(from_version));
    const to = db.checklists.find((c) => c.version === Number(to_version));
    return [...db.conversations.values()].filter((c) => db.evaluations.has(c.conversation_id)).map((c) => {
      const a = buildEvaluation(c, from); const b = buildEvaluation(c, to);
      const changed = b.criteria.filter((x) => { const y = a.criteria.find((z) => z.criterion_id === x.criterion_id); return !y || y.answer !== x.answer || y.raw_points !== x.raw_points; }).map((x) => x.criterion_id);
      const removed = a.criteria.filter((x) => !b.criteria.some((y) => y.criterion_id === x.criterion_id)).map((x) => x.criterion_id);
      return { conversation_id: c.conversation_id, from_total: a.total, to_total: b.total, changed_criteria: [...changed, ...removed] };
    });
  },

  async stats() {
    await init(); await wait(120);
    const items = [...db.conversations.values()].map(listItem);
    const evs = [...db.conversations.keys()].map(withFinal).filter(Boolean);
    const totals = evs.map((e) => e.final.total).filter((x) => x != null);
    const cl = activeChecklist();
    const criteria = cl.criteria.filter((c) => c.type !== 'flag').map((c) => {
      let applicable = 0; let lost = 0; let na = 0; let failed = 0;
      for (const e of evs) {
        const r = (e.final.criteria || e.criteria).find((x) => x.criterion_id === c.id);
        if (!r) continue;
        if (r.answer === 'na') { na += 1; continue; }
        applicable += 1;
        if (r.raw_points < c.weight) { failed += 1; lost += c.weight - r.raw_points; }
      }
      return { criterion_id: c.id, title: c.title, weight: c.weight, applicable, failed, na, fail_rate: applicable ? failed / applicable : 0, lost_points: lost };
    }).sort((a, b) => b.fail_rate - a.fail_rate);
    const byLang = ['RU', 'KZ', 'EN'].map((l) => {
      const t = evs.filter((e) => db.conversations.get(e.conversation_id).language === l).map((e) => e.final.total).filter((x) => x != null);
      return { language: l, n: t.length, avg: t.length ? t.reduce((s, x) => s + x, 0) / t.length : null };
    });
    return {
      total: items.length,
      evaluated: evs.length,
      avg: totals.length ? totals.reduce((s, x) => s + x, 0) / totals.length : null,
      gross: evs.filter((e) => e.gross_error.flag).length,
      needs_review: items.filter((i) => i.evaluation?.needs_review || i.appeal_status === 'open').length,
      reviewed: evs.filter((e) => e.review?.status === 'confirmed').length,
      appeals_open: db.appeals.filter((a) => a.status === 'open').length,
      buckets: { low: totals.filter((x) => x < 60).length, mid: totals.filter((x) => x >= 60 && x < 85).length, high: totals.filter((x) => x >= 85).length },
      by_language: byLang,
      criteria,
      risky: items.filter((i) => i.evaluation?.gross_error).map((i) => ({ conversation_id: i.conversation_id, operator_name: i.operator_name, total: i.evaluation.final_total, gross_types: i.evaluation.gross_types, headline: i.evaluation.headline })),
    };
  },
};

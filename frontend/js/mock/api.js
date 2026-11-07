// Mock-API: ведёт себя как бэкенд по docs/API_CONTRACT.md, данные живут в памяти вкладки.
import { conversations as seedConversations, answerSets, initiallyUnevaluated, initialAppeals } from './data.js';
import { computeScores } from '../scoring.js';
import { deriveTimeline, deriveBotContext, markHold, withOffsets } from '../derive.js';

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const clone = (x) => JSON.parse(JSON.stringify(x));
const nowIso = () => new Date().toISOString();
let seq = 1;
const id = (p) => `${p}_${Date.now().toString(36)}${(seq++).toString(36)}`;

const db = {
  conversations: new Map(),
  checklists: [],          // все версии
  evaluations: new Map(),  // conversation_id -> evaluation (последняя)
  history: new Map(),      // conversation_id -> [evaluation]
  appeals: [],
  jobs: new Map(),
  ready: null,
};

async function loadChecklist() {
  const urls = ['../data/checklists/default.json', '/data/checklists/default.json'];
  for (const u of urls) {
    try { const r = await fetch(u); if (r.ok) return await r.json(); } catch { /* следующий */ }
  }
  throw new Error('Не найден data/checklists/default.json');
}

function activeChecklist(checklistId = 'default') {
  const all = db.checklists.filter((c) => c.checklist_id === checklistId);
  return all.find((c) => c.is_active) || all.at(-1);
}

function prepareConversation(c) {
  return { ...c, transcript: withOffsets(markHold(c.transcript)) };
}

function validateEvidence(conv, ev) {
  return (ev || []).every((x) => {
    const m = conv.transcript[x.index];
    if (!m) return false;
    if (!x.quote) return true;
    const norm = (s) => s.toLowerCase().replace(/[«»"“”.,!?—–-]/g, ' ').replace(/\s+/g, ' ').trim();
    return norm(m.text).includes(norm(x.quote).slice(0, 40));
  });
}

function buildEvaluation(conv, checklist, overrides = {}) {
  const set = answerSets[conv.conversation_id] || {};
  const answers = {};
  for (const c of checklist.criteria) {
    const a = overrides[c.id] ?? set[c.id]?.answer;
    // Новый критерий без «ответа модели» в моках: ставим нейтральное значение.
    answers[c.id] = a ?? (c.type === 'scale' ? Math.max(...c.scale.map((s) => s.value)) : c.type === 'flag' ? 'no' : (c.na_allowed ? 'na' : 'yes'));
  }
  const scores = computeScores(checklist, answers);
  const criteria = checklist.criteria.map((c) => {
    const src = set[c.id] || {};
    const evidence = src.evidence || [];
    const evidence_valid = validateEvidence(conv, evidence);
    return {
      criterion_id: c.id,
      answer: answers[c.id],
      raw_points: scores.criteria[c.id].raw_points,
      points: scores.criteria[c.id].points,
      weight: c.weight,
      effective_weight: scores.criteria[c.id].effective_weight,
      explanation: src.explanation || (answers[c.id] === 'na' ? null : 'Демо-режим: для этого критерия нет заготовленного обоснования.'),
      evidence,
      expected_range: src.expected_range || null,
      na_reason: answers[c.id] === 'na' ? (src.na_reason || 'Условие неприменимости выполнено.') : null,
      evidence_valid,
      confidence: src.confidence || (evidence_valid ? 'high' : 'low'),
      source: c.type === 'flag' ? 'llm+rules' : 'llm',
      overridden: overrides[c.id] !== undefined,
    };
  });
  const flag = checklist.criteria.find((c) => c.type === 'flag');
  const flagRow = flag ? criteria.find((x) => x.criterion_id === flag.id) : null;
  const gross = {
    flag: flagRow?.answer === 'yes',
    types: flagRow?.answer === 'yes' ? (set[flag.id]?.flag_types || []) : [],
    evidence: flagRow?.answer === 'yes' ? flagRow.evidence : [],
    rules_agree: true,
  };
  const evaluation = {
    evaluation_id: id('ev'),
    conversation_id: conv.conversation_id,
    checklist_id: checklist.checklist_id,
    checklist_version: checklist.version,
    status: 'done',
    model: { provider: 'openai', name: 'gpt-6.1-sol' },
    usage: { input_tokens: 5200 + conv.transcript.length * 40, output_tokens: 1150, cost_usd: 0.07, duration_ms: 3800 },
    created_at: nowIso(),
    criteria,
    sections: scores.sections,
    total: scores.total,
    gross_error: gross,
    needs_review: criteria.some((x) => x.confidence === 'low' || !x.evidence_valid),
    bot_context: deriveBotContext(conv.transcript),
  };
  evaluation.timeline = deriveTimeline(conv.transcript, evaluation);
  return evaluation;
}

function withFinal(conversationId) {
  const ev = db.evaluations.get(conversationId);
  if (!ev) return null;
  const resolved = db.appeals.filter((a) => a.conversation_id === conversationId && a.status === 'resolved' && a.decision?.action === 'change' && a.evaluation_id === ev.evaluation_id);
  if (!resolved.length) return { ...ev, final: { total: ev.total, overrides: [] } };
  const overrides = Object.fromEntries(resolved.map((a) => [a.criterion_id, a.decision.new_answer]));
  const checklist = db.checklists.find((c) => c.checklist_id === ev.checklist_id && c.version === ev.checklist_version);
  const conv = db.conversations.get(conversationId);
  const re = buildEvaluation(conv, checklist, overrides);
  return {
    ...ev,
    final: {
      total: re.total,
      criteria: re.criteria,
      overrides: resolved.map((a) => ({ criterion_id: a.criterion_id, from: a.original.answer, to: a.decision.new_answer, appeal_id: a.appeal_id })),
    },
  };
}

async function init() {
  if (db.ready) return db.ready;
  db.ready = (async () => {
    const cl = await loadChecklist();
    db.checklists.push({ ...cl, version: cl.version || 1, is_active: true });
    for (const c of seedConversations) db.conversations.set(c.conversation_id, prepareConversation(clone(c)));
    for (const c of db.conversations.values()) {
      if (initiallyUnevaluated.includes(c.conversation_id)) continue;
      const ev = buildEvaluation(c, activeChecklist());
      db.evaluations.set(c.conversation_id, ev);
      db.history.set(c.conversation_id, [ev]);
    }
    for (const a of initialAppeals) {
      const ev = db.evaluations.get(a.conversation_id);
      const row = ev.criteria.find((x) => x.criterion_id === a.criterion_id);
      db.appeals.push({ ...clone(a), evaluation_id: ev.evaluation_id, original: { answer: row.answer, points: row.points, total: ev.total }, decision: null });
    }
  })();
  return db.ready;
}

function listItem(c) {
  const ev = withFinal(c.conversation_id);
  const ap = db.appeals.filter((a) => a.conversation_id === c.conversation_id);
  return {
    conversation_id: c.conversation_id,
    group_id: c.group_id,
    channel: c.channel,
    language: c.language,
    turns: c.transcript.length,
    operator_name: c.operator_name || null,
    duration_sec: c.transcript.at(-1)?.offset_sec ?? 0,
    evaluation: ev ? {
      evaluation_id: ev.evaluation_id, total: ev.total, final_total: ev.final.total,
      gross_error: ev.gross_error.flag, gross_types: ev.gross_error.types,
      needs_review: ev.needs_review, checklist_version: ev.checklist_version, status: ev.status,
    } : null,
    appeal_status: ap.some((a) => a.status === 'open') ? 'open' : ap.length ? 'resolved' : null,
  };
}

export const mockApi = {
  mode: 'mock',

  async health() { await init(); return { ok: true, llm: { provider: 'openai', name: 'gpt-6.1-sol' }, mode: 'mock' }; },

  async importDataset(group_id) {
    await init(); await wait(700);
    return { group_id, imported: db.conversations.size, note: 'Демо-режим: используются встроенные диалоги' };
  },

  async listConversations() {
    await init(); await wait(120);
    return [...db.conversations.values()].map(listItem);
  },

  async getConversation(cid) {
    await init(); await wait(80);
    const conversation = db.conversations.get(cid);
    if (!conversation) throw new Error('Диалог не найден');
    return {
      conversation: clone(conversation),
      evaluation: clone(withFinal(cid)),
      appeals: clone(db.appeals.filter((a) => a.conversation_id === cid)),
      history: clone(db.history.get(cid) || []).map((e) => ({ evaluation_id: e.evaluation_id, checklist_version: e.checklist_version, total: e.total, created_at: e.created_at })),
    };
  },

  async listChecklists() { await init(); return clone(db.checklists); },
  async getChecklist(checklistId = 'default', version) {
    await init();
    const cl = version ? db.checklists.find((c) => c.checklist_id === checklistId && c.version === Number(version)) : activeChecklist(checklistId);
    return clone(cl);
  },
  async saveChecklist(checklist) {
    await init(); await wait(250);
    const versions = db.checklists.filter((c) => c.checklist_id === checklist.checklist_id);
    const next = { ...clone(checklist), version: Math.max(0, ...versions.map((v) => v.version)) + 1, is_active: true };
    versions.forEach((v) => { v.is_active = false; });
    db.checklists.push(next);
    return clone(next);
  },

  async evaluate(cid, { checklistId = 'default' } = {}) {
    await init();
    const conv = db.conversations.get(cid);
    await wait(900 + Math.random() * 700);
    const ev = buildEvaluation(conv, activeChecklist(checklistId));
    db.evaluations.set(cid, ev);
    db.history.set(cid, [...(db.history.get(cid) || []), ev]);
    return clone(withFinal(cid));
  },

  async evaluateBatch({ conversation_ids } = {}) {
    await init();
    const ids = conversation_ids?.length ? conversation_ids : [...db.conversations.keys()];
    const job = { job_id: id('job'), total: ids.length, done: 0, failed: 0, status: 'running' };
    db.jobs.set(job.job_id, job);
    (async () => {
      for (const cid of ids) { await this.evaluate(cid); job.done += 1; }
      job.status = 'done';
    })();
    return { job_id: job.job_id };
  },
  async getJob(jobId) { await wait(60); return clone(db.jobs.get(jobId)); },

  async listAppeals(status) {
    await init(); await wait(80);
    return clone(db.appeals.filter((a) => !status || a.status === status)).map((a) => {
      const conv = db.conversations.get(a.conversation_id);
      const ev = db.evaluations.get(a.conversation_id);
      const row = ev?.criteria.find((x) => x.criterion_id === a.criterion_id);
      const idx = (row?.evidence || []).map((x) => x.index);
      const from = Math.max(0, Math.min(...(idx.length ? idx : [0])) - 1);
      const to = Math.min(conv.transcript.length - 1, Math.max(...(idx.length ? idx : [2])) + 1);
      return { ...a, context: conv.transcript.slice(from, to + 1), criterion_result: row };
    });
  },

  async createAppeal({ evaluation_id, criterion_id, comment }) {
    await init(); await wait(200);
    const ev = [...db.evaluations.values()].find((x) => x.evaluation_id === evaluation_id);
    const row = ev.criteria.find((x) => x.criterion_id === criterion_id);
    const ap = {
      appeal_id: id('ap'), evaluation_id, conversation_id: ev.conversation_id, criterion_id, comment,
      author_role: 'operator', created_at: nowIso(), status: 'open',
      original: { answer: row.answer, points: row.points, total: ev.total }, decision: null,
    };
    db.appeals.push(ap);
    return clone(ap);
  },

  async decideAppeal(appealId, { action, new_answer, reason }) {
    await init(); await wait(250);
    const ap = db.appeals.find((a) => a.appeal_id === appealId);
    ap.status = 'resolved';
    ap.decision = { action, new_answer: action === 'change' ? new_answer : ap.original.answer, reason, reviewer_role: 'reviewer', decided_at: nowIso() };
    const fin = withFinal(ap.conversation_id);
    ap.decision.new_total = fin.final.total;
    return clone(ap);
  },

  async compare({ from_version, to_version }) {
    await init(); await wait(300);
    const from = db.checklists.find((c) => c.version === Number(from_version));
    const to = db.checklists.find((c) => c.version === Number(to_version));
    return [...db.conversations.values()].filter((c) => db.evaluations.has(c.conversation_id)).map((c) => {
      const a = buildEvaluation(c, from); const b = buildEvaluation(c, to);
      const changed = b.criteria.filter((x) => { const y = a.criteria.find((z) => z.criterion_id === x.criterion_id); return !y || y.answer !== x.answer || y.points !== x.points; }).map((x) => x.criterion_id);
      return { conversation_id: c.conversation_id, from_total: a.total, to_total: b.total, changed_criteria: changed };
    });
  },
};

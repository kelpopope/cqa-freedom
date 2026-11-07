// Единая точка доступа к данным. Режим выбирается в config.js:
// моки (интерфейс без сервера) или живой API (FastAPI, docs/API_CONTRACT.md).
import { CONFIG } from './config.js';
import { mockApi } from './mock/api.js';

async function http(method, path, body) {
  const res = await fetch(`${CONFIG.apiBase}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* пустой ответ */ }
  if (!res.ok) throw new Error(data?.error || `Сервер ответил ${res.status}`);
  return data;
}

const qs = (o) => {
  const p = Object.entries(o || {}).filter(([, v]) => v !== undefined && v !== null && v !== '');
  return p.length ? `?${new URLSearchParams(p)}` : '';
};

const liveApi = {
  mode: 'live',
  health: () => http('GET', '/health'),
  importDataset: (group_id) => http('POST', '/datasets/import', { group_id }),
  listConversations: (params) => http('GET', `/conversations${qs(params)}`),
  getConversation: (id) => http('GET', `/conversations/${encodeURIComponent(id)}`),
  listChecklists: () => http('GET', '/checklists'),
  getChecklist: (id = 'default', version) => http('GET', `/checklists/${id}${qs({ version })}`),
  saveChecklist: (cl) => http('PUT', `/checklists/${cl.checklist_id}`, cl),
  evaluate: (conversation_id, { checklistId = 'default' } = {}) => http('POST', '/evaluate', { conversation_id, checklist_id: checklistId }),
  repeatEvaluation: (evaluation_id) => http('POST', '/evaluate/repeat', { evaluation_id }),
  evaluateBatch: (body) => http('POST', '/evaluate/batch', { checklist_id: 'default', ...body }),
  getJob: (id) => http('GET', `/jobs/${id}`),
  reviewEvaluation: (id, body) => http('POST', `/evaluations/${id}/review`, body),
  listAppeals: (status) => http('GET', `/appeals${qs({ status })}`),
  createAppeal: (body) => http('POST', '/appeals', body),
  decideAppeal: (id, body) => http('POST', `/appeals/${id}/decision`, body),
  overrideCriterion: (body) => http('POST', '/appeals/override', body),
  compare: (params) => http('GET', `/evaluations/compare${qs({ checklist_id: 'default', ...params })}`),
  stats: (params) => http('GET', `/stats${qs(params)}`),
};

export const api = CONFIG.useMocks ? mockApi : liveApi;

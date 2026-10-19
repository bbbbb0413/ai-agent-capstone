// backend /api/** 호출. 인증은 로그인 때 받은 HttpOnly 쿠키로 한다 (SSE도 같은 쿠키를 쓴다)

async function request(method, path, body) {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error.message || `${response.status} ${response.statusText}`)
  }
  return response.status === 202 || response.status === 204 ? response.json().catch(() => ({})) : response.json()
}

export const api = {
  login: (username, password) => request('POST', '/api/auth/login', { username, password }),
  tenants: () => request('GET', '/api/tenants/me'),
  incidents: () => request('GET', '/api/incidents'),
  incident: (id) => request('GET', `/api/incidents/${id}`),
  createTask: (tenantId, question, incidentId) => request('POST', '/api/ai/tasks', { tenantId, question, incidentId }),
  task: (id) => request('GET', `/api/ai/tasks/${id}`),
  tasks: () => request('GET', '/api/ai/tasks'),
  approve: (id, decision, comment) => request('POST', `/api/ai/tasks/${id}/approval`, { decision, comment }),
  stream: (id) => new EventSource(`/api/ai/tasks/${id}/stream`),
}

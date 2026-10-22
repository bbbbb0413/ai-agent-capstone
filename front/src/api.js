// backend /api/** 호출. 인증은 로그인 때 받은 HttpOnly 쿠키로 한다 (SSE도 같은 쿠키를 쓴다)

let onUnauthorized = () => {}

/** 쿠키가 만료되거나 지워져 401이 오면 부를 함수 (main.js에서 로그인 화면 이동을 넣는다) */
export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler
}

async function request(method, path, body) {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (response.status === 401 && path !== '/api/auth/login') {
    onUnauthorized()
    throw new Error('로그인이 만료되었다. 다시 로그인한다')
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error.message || `${response.status} ${response.statusText}`)
  }
  return response.status === 202 || response.status === 204 ? response.json().catch(() => ({})) : response.json()
}

export const api = {
  login: (username, password) => request('POST', '/api/auth/login', { username, password }),
  logout: () => request('POST', '/api/auth/logout'),
  me: () => request('GET', '/api/auth/me'),
  tenants: () => request('GET', '/api/tenants/me'),
  impact: (tenant) => request('GET', `/api/tenants/${encodeURIComponent(tenant)}/impact`),
  incidents: () => request('GET', '/api/incidents'),
  incident: (id) => request('GET', `/api/incidents/${id}`),
  incidentNotices: (id) => request('GET', `/api/incidents/${id}/notices`),
  incidentEscalations: (id) => request('GET', `/api/incidents/${id}/escalations`),
  slackSettings: (tenant) => request('GET', `/api/tenants/${encodeURIComponent(tenant)}/slack`),
  saveSlackSettings: (tenant, body) => request('PUT', `/api/tenants/${encodeURIComponent(tenant)}/slack`, body),
  slackDeliveries: (id) => request('GET', `/api/incidents/${id}/slack-deliveries`),
  slackPreview: (id, escalation) => request('GET', `/api/incidents/${id}/escalations/${escalation}/slack-preview`),
  approveSlack: (id, escalation, body) => request('POST', `/api/incidents/${id}/escalations/${escalation}/slack-delivery`, body),
  slackTrackingSettings: (tenant) => request('GET', `/api/tenants/${encodeURIComponent(tenant)}/slack-tracking`),
  saveSlackTrackingSettings: (tenant, body) => request('PUT', `/api/tenants/${encodeURIComponent(tenant)}/slack-tracking`, body),
  slackTracking: (incident) => request('GET', `/api/incidents/${incident}/slack-tracking`),
  updateSlackTracking: (incident, id, action) => request('POST', `/api/incidents/${incident}/slack-deliveries/${id}/tracking`, { action }),
  createTask: (tenantId, question, incidentId) => request('POST', '/api/ai/tasks', { tenantId, question, incidentId }),
  task: (id) => request('GET', `/api/ai/tasks/${id}`),
  taskEvents: (id) => request('GET', `/api/ai/tasks/${id}/events`),
  tasks: () => request('GET', '/api/ai/tasks'),
  approve: (id, decision, comment, approvalId) => request('POST', `/api/ai/tasks/${id}/approval`, { decision, comment, approvalId }),
  stream: (id) => new EventSource(`/api/ai/tasks/${id}/stream`),
  settings: () => request('GET', '/api/settings'),
  setAutoApprove: (enabled) => request('PUT', '/api/settings/auto-approve', { enabled }),
}

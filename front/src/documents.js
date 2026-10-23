// 문서 화면의 표시 규칙 (SPEC-document-store 8장). 권한 판단은 backend가 다시 한다. 화면은 버튼을 숨기는 데만 쓴다

export const COMMON = '_common'
export const ACCEPT = '.md,.txt,.pdf,.docx'
export const TYPE_LABELS = { runbook: '런북', sla: 'SLA 계약서' }
const FORMAT_LABELS = { md: 'Markdown', txt: '텍스트', pdf: 'PDF', docx: 'Word' }

/** 볼 수 있는 범위: 공통 + 고객사들(관리자는 전부, 엔지니어는 담당 고객사) */
export function scopes(tenants) {
  return [{ id: COMMON, name: 'MSP 공통' }, ...tenants.map((t) => ({ id: t.id, name: t.name }))]
}

/** 바꿀 수 있나: 공통은 관리자만, 고객사 문서는 관리자와 담당 엔지니어 */
export function canEdit(user, tenantId) {
  if (!user) return false
  if (user.role === 'MSP_ADMIN') return true
  return tenantId !== COMMON && user.role === 'MSP_ENGINEER' && (user.tenants || []).includes(tenantId)
}

/** 범위에서 고를 수 있는 문서 종류. 공통 문서는 표준 런북뿐이다 */
export function typesFor(tenantId) {
  return tenantId === COMMON ? ['runbook'] : ['runbook', 'sla']
}

export const formatLabel = (format) => FORMAT_LABELS[format] || format

/** 파일 이름에서 문서 ID 후보: 확장자를 떼고 영문 소문자·숫자·-만 남긴다. 한글 이름이면 비운다 */
export function suggestDocId(filename) {
  const base = String(filename || '').replace(/\.[^.]+$/, '').toLowerCase()
  const id = base.replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').replace(/-{2,}/g, '-')
  return id.length >= 2 ? id.slice(0, 63) : ''
}

/**
 * 버전의 색인 상태 표시. 최신 버전이 실패했으면 검색이 어느 버전으로 계속되는지도 알린다.
 * tone은 StatusBadge 대신 쓰는 badge 색이다.
 */
export function indexState(version, activeVersion) {
  switch (version?.indexStatus) {
    case 'PENDING':
      return { label: '색인 대기', tone: 'info', note: '' }
    case 'INDEXED':
      return { label: '색인 완료', tone: 'ok', note: version.chunkCount ? `조각 ${version.chunkCount}개` : '' }
    case 'FAILED': {
      const search = activeVersion ? ` — 검색은 v${activeVersion}` : ' — 검색에 나오지 않는다'
      return { label: version.retryable ? '색인 실패(다시 시도 예정)' : '색인 실패', tone: 'bad',
        note: (version.indexError || '') + search }
    }
    case 'SUPERSEDED':
      return { label: '새 버전으로 대체', tone: 'neutral', note: '' }
    default:
      return { label: '-', tone: 'neutral', note: '' }
  }
}

/** 대기 중인 버전이 있으면 화면이 몇 초마다 다시 읽는다 */
export const hasPending = (versions) => versions.some((v) => v?.indexStatus === 'PENDING')

export function formatSize(bytes) {
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)}MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)}KB`
  return `${bytes}B`
}

export const slackStatus = (status) => ({
  QUEUED: '승인 완료 · 발송 대기', SENDING: '발송 중', SENT: 'Slack 발송 완료',
  FAILED: '발송 실패', UNKNOWN: '수신 여부 확인 필요', CANCELLED: '설정 변경으로 취소됨',
}[status] || '발송 전 검토 필요')

export const canReviewSlack = (status) => !status || ['FAILED', 'CANCELLED'].includes(status)

export const ATTACHMENT_ACCEPT = '.png,.jpg,.jpeg,.txt,.log,.json,.jsonl,.ndjson,.csv'
export function attachmentError(files) {
  if (files.length > 3) return '파일은 최대 3개까지 첨부할 수 있습니다.'
  if (files.reduce((n, f) => n + f.size, 0) > 8 * 1024 * 1024) return '첨부 파일 합계는 8MB 이하여야 합니다.'
  for (const file of files) {
    if (!file.size) return '빈 파일은 첨부할 수 없습니다.'
    if (!/\.(png|jpe?g|txt|log|json|jsonl|ndjson|csv)$/i.test(file.name)) return 'PNG/JPEG 이미지 또는 텍스트 로그를 선택하세요.'
    const limit = /\.(png|jpe?g)$/i.test(file.name) ? 4 * 1024 * 1024 : 512 * 1024
    if (file.size > limit) return '이미지는 4MB, 텍스트 로그는 512KB까지 첨부할 수 있습니다.'
  }
  return ''
}
export const fileSize = size => size < 1024 * 1024 ? `${Math.ceil(size / 1024)}KB` : `${(size / 1024 / 1024).toFixed(1)}MB`

const pad = (n) => String(n).padStart(2, '0')

/** 10/19 17:48 */
export function shortDateTime(value) {
  const d = new Date(value)
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 09:22:44 (seconds가 false면 09:22) */
export function clockTime(value, seconds = true) {
  const d = new Date(value)
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  return seconds ? `${hm}:${pad(d.getSeconds())}` : hm
}

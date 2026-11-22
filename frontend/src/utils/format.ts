/**
 * 日期 / 分数 / 计数的展示格式化。
 *
 * 刻意不引 dayjs：契约里的日期是 int `YYYYMMDD`，时间戳是 14 位 int `YYYYMMDDHHMMSS`，
 * 用字符串拆位最稳，也不会出现时区偏移。
 */

export const EMPTY = '—'

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n)
}

function isDigits(value: unknown, len: number): value is number {
  return typeof value === 'number' && Number.isInteger(value) && String(value).length === len
}

function isDigitsString(value: unknown, len: number): value is string {
  return typeof value === 'string' && new RegExp(`^\\d{${len}}$`).test(value)
}

/** `20240101` → `2024-01-01`；空值或非法值 → `—` */
export function formatDate(value: number | null | undefined): string {
  if (!isDigits(value, 8)) return EMPTY
  const s = String(value)
  return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`
}

/**
 * 14 位时间戳：`20261109153012` → `2026-11-09 15:30:12`
 * 兼容后端 future 可能返回的字符串形态，以及 mock fixtures 里 `2026-11-01 10:20:00` 这种已格式化字符串。
 */
export function formatDateTime(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return EMPTY
  if (typeof value === 'string') {
    if (isDigitsString(value, 14)) return formatDateTime(Number(value))
    if (isDigitsString(value, 8)) return formatDate(Number(value))
    return value
  }
  if (!isDigits(value, 14)) return formatDate(value)
  const s = String(value)
  return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)} ${s.slice(8, 10)}:${s.slice(10, 12)}:${s.slice(12, 14)}`
}

/** 0..1 → `0.91`；空值 → `—` */
export function formatScore(value: number | null | undefined, digits = 2): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return EMPTY
  return value.toFixed(digits)
}

/**
 * 检索总数展示：契约里 total 是候选数、上限 200（裁决 #2）。
 * `total >= 200` 显示 `200+`，避免把截断值说成全库真值。
 */
export function formatTotal(total: number | null | undefined, cap = 200): string {
  if (typeof total !== 'number' || Number.isNaN(total)) return EMPTY
  return total >= cap ? `${cap}+` : String(total)
}

/** `{min,max}` → `1993-03-01 ~ 2027-03-01` */
export function formatDateRange(range: { min: number | null; max: number | null } | null | undefined): string {
  if (!range) return EMPTY
  return `${formatDate(range.min)} ~ ${formatDate(range.max)}`
}

/** Date → int `YYYYMMDD`（供未来接日期筛选时用） */
export function dateToInt(date: Date): number {
  return Number(`${date.getFullYear()}${pad2(date.getMonth() + 1)}${pad2(date.getDate())}`)
}

/** 计数：空值 → `—`，大数加千分位 */
export function formatCount(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return EMPTY
  return value.toLocaleString('zh-CN')
}

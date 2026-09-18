/**
 * 展示用的时间格式化。
 *
 * 集中在这里是因为同一件事原先在三个组件里各写了一遍，只有年份之差 ——
 * 想统一改格式（加秒、换分隔符）就得记得改三处，漏一个页面上就会出现两种风格。
 */

/**
 * Unix 时间戳（秒）→ `MM-DD HH:mm`，`withYear` 为真时是 `YYYY-MM-DD HH:mm`。
 *
 * @param withYear 列表页信息密度低，带上年份更明确；侧边栏一行放不下，只要月日
 */
export function formatTime(seconds: number, withYear = false): string {
  return formatDate(new Date(seconds * 1000), withYear)
}

/**
 * ISO 时间字符串 → `MM-DD HH:mm`。
 *
 * 和上面分开而不是合成一个：两者的输入类型不同（会话记的是 Unix 秒，记忆条目
 * 的 created_at 是 ISO 字符串），硬要合一就得在函数里判断入参类型，反而更难读。
 */
export function formatIsoTime(iso: string): string {
  const date = new Date(iso)
  // 解析不出来就原样返回：显示一个原始字符串，也好过显示 "Invalid Date"
  if (Number.isNaN(date.getTime())) return iso || '—'

  return formatDate(date, false)
}

/** 真正拼字符串的地方只有这一处，两个入口都走它。 */
function formatDate(date: Date, withYear: boolean): string {
  const pad = (value: number) => String(value).padStart(2, '0')

  const day = `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`

  return withYear ? `${date.getFullYear()}-${day} ${clock}` : `${day} ${clock}`
}

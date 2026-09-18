/**
 * 把 `catch` 到的东西转成能给用户看的一行文案。
 *
 * 抽出来是因为这段样板原先在 7 个地方各写了一遍，措辞容易慢慢跑偏；将来若要
 * 区分「网络故障」（值得提示重试）和「后端返回的业务错误」（照原样显示即可），
 * 也只需改这一处。
 *
 * `catch` 到的可能是 `Error`、字符串、也可以是别的东西，所以参数收 `unknown`。
 */
export function errorText(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc)
}

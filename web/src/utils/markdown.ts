import MarkdownIt from 'markdown-it'

/**
 * Markdown 渲染器。
 *
 * `html: false` 是刻意的：模型输出、读到的文件内容都不可信，一旦允许内联
 * HTML，等于把 XSS 的口子直接开在聊天框里 —— 而 agent 恰恰会把外部内容
 * 一路带进消息流。
 *
 * `breaks: true` 让单个换行也生效：聊天场景里用户和模型都习惯用换行分段，
 * 按标准 Markdown 的「空行才换段」会很别扭。
 */
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

export function renderMarkdown(text: string): string {
  return md.render(text ?? '')
}

/**
 * Markdown 渲染 + HTML 净化。
 *
 * 双保险：markdown-it 关掉 `html`（不解析原始 HTML），输出再过一遍 DOMPurify。
 * `/search` 的 `highlight` 是后端返回的 **已转义 HTML**（只含 `<em>`），
 * 前端仍要再过一次 DOMPurify，且只放行行内标签。
 */
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: false,
})

const defaultLinkOpen = md.renderer.rules.link_open

// 外链一律新窗口打开，并断掉 window.opener
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen
    ? defaultLinkOpen(tokens, idx, options, env, self)
    : self.renderToken(tokens, idx, options)
}

/** 答案 markdown → 安全 HTML（用于 v-html） */
export function renderMarkdown(source: string): string {
  if (!source) return ''
  const raw = md.render(source)
  return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } })
}

/** 检索高亮片段 → 只保留行内强调标签 */
export function sanitizeHighlight(html: string): string {
  if (!html) return ''
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ['em', 'strong', 'b', 'i', 'br', 'span'],
    ALLOWED_ATTR: [],
  })
}

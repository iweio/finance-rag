# 前端（Vue3 + Vite + TS + Element Plus）

## 运行

```bash
pnpm install
pnpm dev        # http://127.0.0.1:5173
pnpm build      # 产出 dist/（会先跑 vue-tsc 类型检查）
```

`pnpm dev` 默认走 **Mock**（`.env.development` 里 `VITE_USE_MOCK=true`），
不需要后端 / Milvus / MySQL。Mock 数据是 `src/api/mock/fixtures.json`，
由 `python 脚本/_export_frontend_fixtures.py` 从**真实语料**生成，不要手改。

切到真实后端：把 `.env.development` 改成 `VITE_USE_MOCK=false`，
并确保 `uvicorn backend.main:app --port 8000` 已起（Vite 会把 `/api` 代理过去）。

## 目录

```
src/
├─ api/          10 个接口的真实现 + mock 实现（同一 RagApi 接口，页面不感知）
├─ stores/       session / auth / chat / filters / meta / favorite
├─ composables/  useSSE（POST + SSE 手写解析，不能用 EventSource）
├─ components/   AppShell / UserMenu / SourceCard / FilterBar / RelatedGroup …
├─ views/        Login / Chat / Search / Clause / History / Favorite / NotFound
├─ router/       5 个业务路由 + 登录守卫
└─ utils/        format（日期 int 拆分）/ markdown（DOMPurify）/ error
```

## 约定（改代码前先看）

1. **字段名与 `契约/API.md` 逐字一致**，一律 snake_case；不要在 API 层改成驼峰。
2. 日期是 `int YYYYMMDD`，`created_at` 是 14 位 `int YYYYMMDDHHMMSS`，用 `utils/format.ts`，别自己拼。
3. `/chat` 是 POST + SSE，**`EventSource` 用不了**；解析在 `composables/useSSE.ts`。
4. `highlight` 是后端转义过的 HTML，`v-html` 前必须再过 DOMPurify（`utils/markdown.ts` 已封装）。
5. 不写死业务数字（8156 / 159）：从 `/health` 与 `/meta/filters` 读。
6. `topic_tags` 只展示、不进 `filters`（裁决 §9 #1）；`filters` 只有 domain/status/org。

## 已知限制

- Mock 下历史/收藏要求登录（`requireUser()`），真实后端允许匿名（`user_id=NULL`）。
  前者更严，登录后才进这两个页面，行为一致。
- `unplugin-auto-import` / `unplugin-vue-components` 与 Vite 6 的 peer 范围较窄；
  若 `pnpm install` 报 peer 冲突，把两者升到 `^19` / `^28` 再装。
- 本仓库交付时**没有 node_modules**（开发机无外网），首次 `pnpm install` 需要在有网环境跑。

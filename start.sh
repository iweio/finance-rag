#!/usr/bin/env bash
# 一键启动（Linux / macOS / Git Bash）。
# 验收要求：换一台机器，`bash start.sh` 就能起来。
set -euo pipefail
cd "$(dirname "$0")"

echo "==> 检查 Docker"
if ! command -v docker >/dev/null 2>&1; then
  echo "[FAIL] 未找到 docker。先装 Docker Desktop / Docker Engine，再回来跑这个脚本。" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "[FAIL] 没有 docker compose（v2）。旧版的 docker-compose 不被本脚本支持。" >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> 已从 .env.example 生成 .env"
  echo "    ★ 请填两个值再继续：LLM_API_KEY（大模型密钥）、JWT_SECRET（登录令牌密钥）"
fi

if grep -q "sk-xxxxxxxx" .env; then
  echo "[WARN] .env 里的 LLM_API_KEY 还是占位值，问答接口会失败（其它接口不受影响）"
fi
if grep -q "change-me-to-a-long-random-string" .env; then
  echo "[WARN] .env 里的 JWT_SECRET 还是占位值；APP_ENV=production 时会拒绝启动"
fi

echo "==> 构建并启动 7 个服务（首次要拉镜像 + 装依赖，10~20 分钟）"
docker compose up -d --build

echo "==> 等待 /api/v1/health 通过（最多 10 分钟；首次启动要加载模型）"
# 干净机器上 nginx 在 80。本机这类 80 被 IIS/http.sys 占的，用
# docker-compose.override.yml 挪到 8080，这里就 `BASE_URL=http://localhost:8080 bash start.sh`。
BASE_URL="${BASE_URL:-http://localhost}"
ok=0
for _ in $(seq 1 120); do
  code=$(curl -s -o /tmp/rag_health.json -w '%{http_code}' "$BASE_URL/api/v1/health" || true)
  if [ "$code" = "200" ]; then ok=1; break; fi
  sleep 5
done

if [ "$ok" != "1" ]; then
  echo "[FAIL] 10 分钟内 health 没通过。看这里定位：" >&2
  docker compose ps >&2
  docker compose logs --tail=80 backend >&2
  exit 1
fi

echo "==> health:"
cat /tmp/rag_health.json; echo
echo
echo " 前端     $BASE_URL"
echo " 接口文档 http://localhost:8000/docs"
echo " API 自检 curl $BASE_URL/api/v1/health"
echo " 图谱自检 curl $BASE_URL/api/v1/graph/stats"
echo
docker compose ps

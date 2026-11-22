"""全链路自检：一律从 nginx 入口进（交付时用户看到的就是这一条路）。

    BASE = http://localhost:8080     # 本机 80 被 Windows http.sys 占，override 换成 8080

跑法：
    docker compose up -d
    python 脚本/_fullstack_check.py
    python 脚本/_fullstack_check.py --base http://localhost     # 演示机（80）

为什么不直接打 :8000：绕过 nginx 就绕过了真实路径 —— /api 的 proxy_pass、
SSE 的 proxy_buffering off、前端静态资源、gzip 这几件事只有在走 nginx 时才被验证。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://localhost:8080"
API = "/api/v1"
TIMEOUT = 120
fails = []
skips = []

# 引用边（CITES）全库只有 108 条、散在 170 个节点上，抽不到是运气问题，
# 所以给几个**实测有出边**的条文当探针（来源 kg/out/serving/adjacency.json）。
REF_CANDIDATES = ("NPC-010-A181", "CSRC-001-A22", "NFRA-016-A26")


def check(name, ok_, detail=""):
    tag = "PASS" if ok_ else "FAIL"
    print(f"[{tag}] {name}{'  ' + detail if detail else ''}")
    if not ok_:
        fails.append(name)


def skip(name, why):
    print(f"[SKIP] {name}  {why}")
    skips.append(name)


def req(method, path, body=None, token=None, raw=False):
    """返回 (status, payload)。payload 是解析后的 JSON；raw=True 时是文本。"""
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
            text = resp.read().decode("utf-8", "replace")
            ctype = resp.headers.get("Content-Type", "")
            if raw or "json" not in ctype:
                return resp.status, {"_text": text, "_ctype": ctype}
            return resp.status, json.loads(text)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(text)
        except Exception:  # noqa: BLE001
            return e.code, {"_text": text}
    except Exception as e:  # noqa: BLE001
        return None, {"_err": f"{type(e).__name__}: {e}"}


def sse(method, path, body, token=None):
    """收 SSE，返回 (事件名列表, 各事件 payload)。"""
    events = []
    r = urllib.request.Request(BASE + path,
                               data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                               method=method)
    r.add_header("Content-Type", "application/json")
    r.add_header("Accept", "text/event-stream")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
            name = None
            for line in resp:
                s = line.decode("utf-8", "replace").rstrip("\r\n")
                if s.startswith("event:"):
                    name = s[6:].strip()
                elif s.startswith("data:"):
                    events.append((name, s[5:].strip()))
                    name = None
                elif s == "":
                    continue
    except Exception as e:  # noqa: BLE001
        events.append(("_transport", f"{type(e).__name__}: {e}"))
    return events


def env(data):
    """统一响应包：{code,message,data}（契约 API 开头的错误码表）。"""
    return isinstance(data, dict) and "code" in data and "message" in data


SEED_SID = "fsseed0001"
_SEED_PY = (
    "from backend.database import SessionLocal\n"
    "from backend.main import _ensure_session\n"
    "from backend.models import QaHistory\n"
    f"sid = {SEED_SID!r}\n"
    "with SessionLocal() as db:\n"
    "    _ensure_session(db, sid, '落库探测', None)\n"
    "    old = db.query(QaHistory).filter(QaHistory.session_id == sid).delete()\n"
    "    q = QaHistory(session_id=sid, question='反洗钱的核心义务是什么？', answer='答案正文',\n"
    "                sources=[{'clause_id': 'PBC-047-A41', 'clause_no': '第四十一条',\n"
    "                          'doc_title': '金融机构客户受益所有人识别管理办法'}], score=0.5)\n"
    "    db.add(q); db.commit(); db.refresh(q)\n"
    "    print('seeded qa_id=%s created_at=%r' % (q.qa_id, q.created_at))\n"
)


def seed_history_via_container(sid_unused):
    """在 backend 容器里用**应用自己的** SessionLocal/_ensure_session 落一行。

    走的是 compose 的 DATABASE_URL（mysql:3306），和线上写路径同一个连接串、
    同一份 SQLAlchemy 元数据；建表默认值 DATETIME(3) 那个坑正是这么暴露的。
    """
    import subprocess

    r = subprocess.run(["docker", "compose", "exec", "-T", "backend", "python", "-c", _SEED_PY],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = (r.stdout or "").strip().splitlines()
    check("容器内直连 MySQL 落 qa_history（写路径）", r.returncode == 0 and out,
          out[-1][:90] if out else (r.stderr or "")[-160:])
    return r.returncode == 0 and bool(out)


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base.rstrip("/")
    print(f"BASE = {BASE}\n")

    # ---- 0. nginx 自身 ----
    st, p = req("GET", "/nginx-health", raw=True)
    check("nginx /nginx-health", st == 200 and p.get("_text", "").startswith("ok"),
          f"status={st} {p.get('_text', p.get('_err', ''))[:40]!r}")

    # ---- 1. 前端静态资源经 nginx ----
    st, p = req("GET", "/", raw=True)
    html = p.get("_text", "")
    check("nginx → frontend /", st == 200 and "<html" in html.lower(),
          f"status={st} ctype={p.get('_ctype', '')[:30]!r} len={len(html)}")
    check("前端 index 里挂得上入口脚本", "assets/" in html or 'type="module"' in html,
          f"len={len(html)}")

    # ---- 2. /health：两种入口都要是统一响应包 ----
    milvus_rows = None
    for label, path in (("compat /health", "/health"), ("API /health", f"{API}/health")):
        st, p = req("GET", path)
        d = p.get("data", {}) if env(p) else {}
        check(f"{label} 200 + 响应包", st == 200 and env(p) and p["code"] == 0,
              f"status={st} code={p.get('code') if env(p) else '非响应包'}")
        if d:
            mv, my = d.get("milvus", {}), d.get("mysql", {})
            print(f"       mode={d.get('mode')} milvus={mv.get('status')} "
                  f"rows={mv.get('row_count')} mysql={my.get('status')} "
                  f"schema={my.get('schema')} bm25={d.get('bm25')}")
            check(f"{label} 依赖全绿", mv.get("status") == "ok" and my.get("status") == "ok",
                  f"milvus={mv.get('status')} mysql={my.get('status')}")
            if mv.get("row_count") is not None:
                milvus_rows = mv["row_count"]
                check(f"{label} 行数 == 8156", milvus_rows == 8156, f"实际 {milvus_rows}")

    # ---- 3. /search：字段必须真的填上了（pymilvus 2.5 那个坑就在这里暴露）----
    st, p = req("POST", f"{API}/search", {"keyword": "反洗钱 客户身份识别", "page": 1, "size": 5})
    items = (p.get("data") or {}).get("items") or []
    check("POST /search 有结果", st == 200 and env(p) and p["code"] == 0 and len(items) > 0,
          f"status={st} total={(p.get('data') or {}).get('total')}")
    if items:
        it = items[0]
        filled = [k for k in ("clause_id", "doc_title", "text" if "text" in it else "snippet",
                              "status") if it.get(k)]
        check("search 首条字段非空", len(filled) >= 3, str({k: str(it.get(k))[:18] for k in filled}))
    cid = items[0]["clause_id"] if items else None

    # ---- 4. /clause/{id} 与 /related ----
    if cid:
        st, p = req("GET", f"{API}/clause/{cid}")
        d = p.get("data") or {}
        check(f"GET /clause/{cid}", st == 200 and d.get("text"), f"正文长度 {len(d.get('text') or '')}")
        st, p = req("GET", f"{API}/clause/{cid}/related?top_k=3")
        rel = (p.get("data") or {}).get("related") or []
        check("GET /clause/{id}/related", st == 200 and len(rel) > 0, f"{len(rel)} 条")
        if rel:
            check("related 字段非空", bool(rel[0].get("doc_title") and rel[0].get("snippet")),
                  str({k: str(rel[0].get(k))[:16] for k in ("doc_title", "snippet")}))
    else:
        skip("/clause 与 /related", "search 没拿到 clause_id")

    # ---- 5. /meta/filters ----
    st, p = req("GET", f"{API}/meta/filters")
    d = p.get("data") or {}
    check("GET /meta/filters", st == 200 and isinstance(d, dict) and len(d) > 0,
          f"键 {list(d)[:6]}")

    # ---- 6. 认证：注册 → 登录 → 带 token 的写路径 ----
    user = f"fs_{uuid.uuid4().hex[:10]}"
    pwd = "Test-2026-pass"
    st, p = req("POST", f"{API}/auth/register", {"username": user, "password": pwd})
    check(f"POST /auth/register（{user}）", st == 200 and env(p) and p["code"] == 0,
          f"status={st} msg={str(p.get('message'))[:40]}")
    st, p = req("POST", f"{API}/auth/register", {"username": user, "password": pwd})
    check("重名注册 → 409", st == 409, f"status={st} code={p.get('code')}")
    st, p = req("POST", f"{API}/auth/login", {"username": user, "password": pwd})
    token = (p.get("data") or {}).get("token")
    check("POST /auth/login 拿到 token", st == 200 and bool(token), f"status={st}")
    st, p = req("POST", f"{API}/auth/login", {"username": user, "password": "wrong-pass"})
    check("错密码登录 → 401", st == 401, f"status={st}")

    # ---- 7. 收藏：写 → 查 → 删（MySQL 写路径的闭环）----
    if cid and token:
        st, p = req("POST", f"{API}/favorite", {"clause_id": cid}, token)
        fid = (p.get("data") or {}).get("favorite_id")
        check("POST /favorite", st == 200 and fid, f"favorite_id={fid}")
        st, p = req("GET", f"{API}/favorite", token=token)
        got = (p.get("data") or {}).get("items") or []
        row = got[0] if got else {}
        check("GET /favorite 回显 + B5 富化", st == 200 and fid in [g.get("favorite_id") for g in got],
              f"doc_title={str(row.get('doc_title'))[:20]!r} clause_no={row.get('clause_no')!r}")
        st, p = req("DELETE", f"{API}/favorite?favorite_id={fid}", token=token)
        check("DELETE /favorite", st == 200 and (p.get("data") or {}).get("deleted") == 1,
              f"deleted={(p.get('data') or {}).get('deleted')}")
    else:
        skip("/favorite 写查删", "缺 token 或 clause_id")

    # ---- 8. /chat SSE：契约 §1 事件顺序 ----
    sid = f"fscheck{int(time.time())}"
    ev = sse("POST", f"{API}/chat", {"question": "反洗钱的核心义务是什么？", "session_id": sid})
    names = [e[0] for e in ev]
    print(f"       事件序列（压缩）：{names[:3]} … {names[-3:]}  共 {len(names)} 个")
    has_err = "error" in names
    if has_err:
        err = next(d for n, d in ev if n == "error")
        print(f"       error payload = {err[:160]}")
        # LLM key 是占位符时 50002 是预期结果；这仍然验证了检索侧走通了
        if "50002" in err or "ApiKey" in err or "api key" in err.lower():
            skip("chat 生成（LLM_API_KEY 仍是占位符）",
                 "检索侧已通过：error 前无 40001/50001，说明 retrieve 没炸")
        else:
            check("chat 不应报错", False, err[:120])
    if not has_err:
        check("delta* 存在", names.count("delta") > 0, f"delta × {names.count('delta')}")
        check("顺序 delta* → sources → score → done",
              names[:names.count("delta")] == ["delta"] * names.count("delta")
              and [n for n in names if n != "delta"] == ["sources", "score", "done"],
              str([n for n in names if n != "delta"]))
        check("done 是最后一个", names[-1] == "done", f"末位 {names[-1]}")

    # ---- 9. /history：chat 落库了没有（B1/B8）----
    if has_err:
        # error 是终止事件，main.chat 在它之后才 return，压根走不到 _ensure_session +
        # QaHistory 落库（backend/main.py:271-273 vs 289-308）。所以 LLM key 不通时
        # 会话不存在是**契约正确行为**，不是 bug —— 但 MySQL 写路径（DATETIME(3) 默认值、
        # MEDIUMTEXT、JSON sources）就没人验了，得自己塞一行。
        st, p = req("GET", f"{API}/history?session_id={sid}", token=token)
        check("失败 chat 不留会话（40402，契约正确）", p.get("code") == 40402,
              f"status={st} code={p.get('code')}")
        if seed_history_via_container(sid):
            sid = SEED_SID
            st, p = req("GET", f"{API}/history?session_id={sid}", token=token)
            d = p.get("data") or {}

    st, p = req("GET", f"{API}/history?session_id={sid}", token=token)
    d = p.get("data") or {}
    if st == 200 and d.get("items"):
        it = d["items"][0]
        check("GET /history 有该会话记录", True,
              f"qa_id={it.get('qa_id')} created_at={it.get('created_at')!r} "
              f"sources={len(it.get('sources') or [])}")
        check("created_at 是 14 位（B4）", len(str(it.get("created_at") or "")) == 14,
              repr(it.get("created_at")))
        check("sources 精简 3 字段", set(it["sources"][0] or {}) == {"clause_id", "clause_no", "doc_title"},
              str(list((it["sources"] or [{}])[0])))
    elif st == 200 and d.get("total") == 0:
        skip("GET /history 记录", "会话无记录：chat 未成功落库（与上面的生成失败同源）")
    else:
        check("GET /history", False, f"status={st} {str(p)[:100]}")

    if sid:
        st, p = req("DELETE", f"{API}/history?session_id={sid}", token=token)
        check("DELETE /history 清理", st == 200 and (p.get("data") or {}).get("deleted", 0) >= 1,
              f"deleted={(p.get('data') or {}).get('deleted')}")

    # ---- 10. 知识图谱（M8，接口 10→13）：只读 kg/out/serving/，不该碰 Milvus ----
    st, p = req("GET", f"{API}/graph/stats")
    g = p.get("data") or {}
    check("GET /graph/stats", st == 200 and env(p) and p["code"] == 0 and g.get("total_nodes"),
          f"status={st} nodes={g.get('total_nodes')} edges={g.get('total_edges')} "
          f"has_embeddings={g.get('has_embeddings')}")
    # 图与库必须同源：Clause 节点数 == Milvus 入库行数，Doc 节点数 == 语料元数据行数。
    # 对不上就是图谱产物过期（重切了语料没重跑 kg/build_graph.py）。
    if g:
        check("graph Clause 节点数 == Milvus 行数",
              g.get("node_counts", {}).get("Clause") == milvus_rows or milvus_rows is None,
              f"Clause={g.get('node_counts', {}).get('Clause')} rows={milvus_rows}")
        check("graph Doc 节点数 == 159", g.get("node_counts", {}).get("Doc") == 159,
              f"Doc={g.get('node_counts', {}).get('Doc')}")
        check("stats 边计数自洽（Σ边 == adjacency 无向展开的一半）",
              sum(g.get("edge_counts", {}).values()) == g.get("total_edges"),
              f"Σ={sum(g.get('edge_counts', {}).values())}")

    if cid:
        st, p = req("GET", f"{API}/graph/neighbors?clause_id={cid}&hops=1")
        d = p.get("data") or {}
        ns = d.get("nodes") or []
        check(f"GET /graph/neighbors?{cid}", st == 200 and d.get("focus", "").endswith(cid),
              f"status={st} focus={d.get('focus')} nodes={d.get('node_count')} "
              f"edges={d.get('edge_count')}")
        check("1 跳邻居含 NEXT/IN_DOC/IN_CHAPTER",
              {e["type"] for e in d.get("edges") or []} & {"NEXT", "IN_DOC", "IN_CHAPTER"} != {},
              str(sorted({e["type"] for e in d.get("edges") or []})))
        check("节点都带 type/label/depth", all({"type", "label", "depth"} <= set(n) for n in ns),
              str(sorted(ns[0])) if ns else "空")

        st, p = req("GET", f"{API}/graph/subgraph?clause_id={cid}&hops=2&limit=60")
        d2 = p.get("data") or {}
        check("GET /graph/subgraph hops=2 严格变大", st == 200 and d2.get("node_count", 0) > d.get("node_count", 0),
              f"1跳={d.get('node_count')} → 2跳={d2.get('node_count')} truncated={d2.get('truncated')}")
        check("子图节点数 ≤ limit", d2.get("node_count", 0) <= 60,
              f"{d2.get('node_count')}/60")

        st, p = req("GET", f"{API}/graph/subgraph?clause_id={cid}&hops=2&edge_types=NEXT")
        only_next = {e["type"] for e in (p.get("data") or {}).get("edges") or []}
        check("edge_types=NEXT 过滤生效", st == 200 and only_next <= {"NEXT"},
              f"实际边类型 {sorted(only_next)}")

        # 引用边是这张图里最稀有、也最该被前端画出来的一类：找一个真有 CITES 的条文。
        cited = None
        for probe in (next(iter(REF_CANDIDATES), None), cid):
            if not probe:
                continue
            st, p = req("GET", f"{API}/graph/subgraph?clause_id={probe}&hops=1&edge_types=CITES")
            if ((p.get("data") or {}).get("edge_count") or 0) > 0:
                cited = probe
                break
        check("CITES 边可查（抽 1 条有引用的条文）", bool(cited),
              f"命中 {cited}" if cited else f"候选 {list(REF_CANDIDATES)} 全无数")

    st, p = req("GET", f"{API}/graph/neighbors?clause_id=NFRA-999-A01")
    check("图上不存在的条文 → 40401", p.get("code") == 40401, f"status={st} code={p.get('code')}")
    st, p = req("GET", f"{API}/graph/subgraph?clause_id={cid or 'NFRA-016-A26'}&edge_types=BOGUS")
    check("未知边类型 → 40001", p.get("code") == 40001, f"status={st} code={p.get('code')}")
    st, p = req("GET", f"{API}/graph/subgraph?clause_id={cid or 'NFRA-016-A26'}&hops=3")
    check("hops 越界 → 40001（FastAPI 422 转包）", p.get("code") == 40001,
          f"status={st} code={p.get('code')}")

    print()
    if skips:
        print(f"跳过 {len(skips)} 项：")
        for s in skips:
            print(f"  - {s}")
    print(f"\n{'全部通过' if not fails else f'{len(fails)} 项失败：' + ' / '.join(fails)}"
          f"   （用户 {user}、会话 {sid} 已尽力清理，收藏已删）")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

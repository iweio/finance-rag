"""从现有语料构建六类节点、十类边的分层异构图。

只用 stdlib + openpyxl（不依赖 torch/PyG），所以**在没有 GPU、没有网络的机器上也能跑**，
产出的 `nodes.jsonl` / `edges.jsonl` 就是训练脚本的输入。

用法：
    python kg/build_graph.py
    python kg/build_graph.py --out kg/out --spotcheck 100

输出（默认 kg/out/）：
    nodes.jsonl            每行 {id, type, label, props}
    edges.jsonl            每行 {src, type, dst, props}
    graph_stats.json       节点/边计数、孤立点、连通分量、章节规模分布
    citation_edges.csv     182 处引用的逐条解析结果（供人工抽检）
    spotcheck_sample.csv   抽检样本（verdict/note 两列留空给人填）

抽取规则（与 文档/知识图谱与GNN方案.md §3 一致）：
    - 章节：chunk.chapter 非空 → Chapter 节点（doc_id#chapter）
    - 引用：正则 `《X》第N条`；X 命中 159 份法规标题 → 若 N 也在该法规里，CITES 到条文；
            否则降级为 MENTIONS_DOC；X 不在库里 → MENTIONS_EXT（建 ExternalLaw 节点）
    - 机关：org 按顿号/逗号拆开，联合发文连多条边
    - 标签：topic_tags 按逗号拆开
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import sys

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kg import schema as S  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNKS_DIR = os.path.join(ROOT, "产出", "chunks")
META_XLSX = os.path.join(ROOT, "产出", "语料元数据.xlsx")
DEFAULT_OUT = os.path.join(ROOT, "kg", "out")

CITE_RE = re.compile(r"《([^》]{2,60})》\s*第([一二三四五六七八九十百零〇两0-9]+)条")
LAW_RE = re.compile(r"《([^》]{2,60})》")

# 这些是正文里的泛称（"《条例》规定…"），不是可定位的法规名，不建 ExternalLaw 节点
GENERIC_TITLES = {"条例", "办法", "规定", "通知", "决定", "公告", "意见", "细则",
                  "指引", "规则", "法", "法典", "本规定", "本办法", "本条例"}
PAREN_RE = re.compile(r"[（(][^）)]*[）)]\s*$")
PREFIX = "中华人民共和国"

# 少数约定俗成的简称，规则生成不出来，手工补（只补能唯一对上的）
MANUAL_ALIAS = {"基金法": "证券投资基金法"}
_LEADER_RE = re.compile(r"[.·…・]{2,}\s*-?\s*\d*\s*-?")
_PAGE_RE = re.compile(r"-\s*\d+\s*-?\s*$")
_SPACE_RE = re.compile(r"[\s\u00a0\u2002\u2003\u2009\u3000]+")


def chapter_key(chapter: str) -> str:
    """章节名归一：去 PDF 页脚（点线 + 页码）、全角空格，并去掉所有空白。

    不归一的后果是同一章因页脚不同裂成多个 Chapter 节点（实测 7 处），
    星级结构会把同章条文拆到两个章下。
    """
    s = _SPACE_RE.sub(" ", str(chapter or ""))
    s = _LEADER_RE.sub(" ", s)
    s = _PAGE_RE.sub("", s)
    s = _SPACE_RE.sub("", s)
    return s.strip(" -—、,，.。")

_CN = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
       "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn2int(text: str) -> int | None:
    """'五十三' -> 53；'一百零二' -> 102；'12' -> 12。解析不了返回 None。"""
    text = text.strip()
    if text.isdigit():
        return int(text)
    total, num = 0, 0
    for ch in text:
        if ch in _CN:
            num = _CN[ch]
        elif ch == "十":
            total += (num or 1) * 10
            num = 0
        elif ch == "百":
            total += (num or 1) * 100
            num = 0
        else:
            return None
    return total + num


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _aliases(title: str) -> set[str]:
    """法规名的可匹配写法：全称、去「中华人民共和国」、去尾部括号年份。"""
    base = _clean(title)
    out = {base}
    if base.startswith(PREFIX) and len(base) > len(PREFIX) + 1:
        out.add(base[len(PREFIX):])
    no_paren = PAREN_RE.sub("", base).strip()
    if no_paren:
        out.add(no_paren)
        if no_paren.startswith(PREFIX) and len(no_paren) > len(PREFIX) + 1:
            out.add(no_paren[len(PREFIX):])
    return {x for x in out if x}


def build_title_index(meta: dict[str, dict]) -> dict[str, str]:
    """别名 → doc_id。**有歧义的别名直接丢弃**（宁可漏配，也不要错配）。"""
    hits: dict[str, set[str]] = collections.defaultdict(set)
    for did, m in meta.items():
        for alias in _aliases(m.get("doc_title") or ""):
            hits[alias].add(did)
    index = {alias: next(iter(dids)) for alias, dids in hits.items() if len(dids) == 1}
    for short, full in MANUAL_ALIAS.items():
        target = index.get(full)
        if target and short not in index:
            index[short] = target
    return index


def load_meta() -> dict[str, dict]:
    wb = openpyxl.load_workbook(META_XLSX, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    head = list(rows[0])
    out = {}
    for r in rows[1:]:
        d = dict(zip(head, r))
        if d.get("doc_id"):
            out[str(d["doc_id"])] = d
    return out


def load_chunks() -> list[dict]:
    chunks = []
    for fn in sorted(os.listdir(CHUNKS_DIR)):
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(CHUNKS_DIR, fn), encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    chunks.append(json.loads(line))
    chunks.sort(key=lambda c: (c["doc_id"], int(c.get("clause_index") or 0), c["clause_id"]))
    return chunks


def build(out_dir: str, spotcheck_n: int = 100) -> dict:
    meta = load_meta()
    chunks = load_chunks()
    alias_to_doc = build_title_index(meta)

    # 先收一遍所有《X》，把库外法规的简称/全称归一到同一个名字（如 公司法 → 中华人民共和国公司法）
    raw_titles = set()
    for c in chunks:
        raw_titles.update(_clean(x) for x in LAW_RE.findall(c.get("text") or ""))
    ext_canon: dict[str, str] = {}
    for t in raw_titles:
        if not t or t in alias_to_doc or t in GENERIC_TITLES:
            continue
        full = PREFIX + t
        ext_canon[t] = full if full in raw_titles else t

    clause_index_by_doc = collections.defaultdict(set)
    clause_at: dict[tuple[str, int], str] = {}
    for c in chunks:
        idx = int(c.get("clause_index") or 0)
        clause_index_by_doc[c["doc_id"]].add(idx)
        clause_at[(c["doc_id"], idx)] = c["clause_id"]

    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str, str], dict] = {}
    cites: list[dict] = []

    def add_node(nid: str, ntype: str, label: str, **props):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "type": ntype, "label": label, "props": props}
        else:
            nodes[nid]["props"].update({k: v for k, v in props.items() if v is not None})

    def add_edge(src: str, etype: str, dst: str, **props):
        key = (src, etype, dst)
        if key not in edges:
            edges[key] = {"src": src, "type": etype, "dst": dst, "props": props}

    # ---- 法规 / 机关 / 标签节点 ----
    for did, m in meta.items():
        add_node(S.doc_id(did), S.DOC, m.get("doc_title") or did,
                 doc_id=did, org=m.get("org"), doc_type=m.get("doc_type"),
                 domain=m.get("domain"), status=m.get("status"),
                 publish_date=m.get("publish_date"), effective_date=m.get("effective_date"),
                 source_url=m.get("source_url"), clause_count=m.get("clause_count"))
        for name in re.split(r"[、,，;；]", str(m.get("org") or "")):
            name = name.strip()
            if name:
                add_node(S.agency_id(name), S.AGENCY, name)
                add_edge(S.doc_id(did), S.ISSUED_BY, S.agency_id(name))
        for tag in re.split(r"[,，、;；]", str(m.get("topic_tags") or "")):
            tag = tag.strip()
            if tag:
                add_node(S.tag_id(tag), S.TAG, tag)
                add_edge(S.doc_id(did), S.HAS_TAG, S.tag_id(tag))

    # ---- 条文 / 章节节点 ----
    chapter_size = collections.Counter()
    for c in chunks:
        cid, did = c["clause_id"], c["doc_id"]
        add_node(S.clause_id(cid), S.CLAUSE, c.get("clause_no") or cid,
                 clause_id=cid, doc_id=did, doc_title=c.get("doc_title"),
                 clause_no=c.get("clause_no"), clause_index=int(c.get("clause_index") or 0),
                 chapter=c.get("chapter"), text_len=len(c.get("text") or ""),
                 is_short=bool(c.get("is_short")), chunk_type=c.get("chunk_type") or "clause")
        add_edge(S.clause_id(cid), S.IN_DOC, S.doc_id(did))
        chapter = chapter_key(c.get("chapter"))
        if chapter:
            ch = S.chapter_id(did, chapter)
            add_node(ch, S.CHAPTER, chapter, doc_id=did, chapter=chapter)
            add_edge(S.clause_id(cid), S.IN_CHAPTER, ch)
            chapter_size[ch] += 1

    for ch in chapter_size:
        nodes[ch]["props"]["size"] = chapter_size[ch]
        did = nodes[ch]["props"]["doc_id"]
        add_edge(ch, S.IN_DOC, S.doc_id(did))

    # ---- NEXT：同文档相邻 ----
    by_doc = collections.defaultdict(list)
    for c in chunks:
        by_doc[c["doc_id"]].append(c)
    for did, rows in by_doc.items():
        rows.sort(key=lambda c: (int(c.get("clause_index") or 0), c["clause_id"]))
        for a, b in zip(rows, rows[1:]):
            add_edge(S.clause_id(a["clause_id"]), S.NEXT, S.clause_id(b["clause_id"]))
            add_edge(S.clause_id(b["clause_id"]), S.NEXT, S.clause_id(a["clause_id"]))

    # ---- 引用 / 提及 ----
    ext_count: collections.Counter = collections.Counter()
    for c in chunks:
        cid, did = c["clause_id"], c["doc_id"]
        text = c.get("text") or ""

        mentioned_docs = set()
        for title in LAW_RE.findall(text):
            t = _clean(title)
            if not t:
                continue
            did2 = alias_to_doc.get(t)
            if did2:
                if did2 != did:
                    mentioned_docs.add(did2)
                continue
            if t in GENERIC_TITLES:
                continue
            name = ext_canon.get(t, t)
            eid = S.external_id(name)
            add_node(eid, S.EXTERNAL, name)
            add_edge(S.clause_id(cid), S.MENTIONS_EXT, eid)
            ext_count[eid] += 1

        for did2 in mentioned_docs:
            add_edge(S.clause_id(cid), S.MENTIONS_DOC, S.doc_id(did2))
            add_edge(S.doc_id(did), S.CITES_DOC, S.doc_id(did2))

        for title, art in CITE_RE.findall(text):
            t = _clean(title)
            n = cn2int(art)
            target_doc = alias_to_doc.get(t)
            row = {"src_clause_id": cid, "raw": f"《{title}》第{art}条", "title": title,
                   "article": art, "article_int": n, "resolution": "", "target": ""}
            if t in GENERIC_TITLES:
                row["resolution"] = "generic"
            elif target_doc is None:
                name = ext_canon.get(t, t)
                row["resolution"] = "external"
                row["target"] = S.external_id(name)
                eid = S.external_id(name)
                add_node(eid, S.EXTERNAL, name)
                add_edge(S.clause_id(cid), S.MENTIONS_EXT, eid)
                ext_count[eid] += 1
            elif n is not None and n in clause_index_by_doc[target_doc]:
                row["resolution"] = "clause"
                row["target"] = S.clause_id(clause_at[(target_doc, n)])
                add_edge(S.clause_id(cid), S.CITES, row["target"])
            else:
                row["resolution"] = "doc_only"
                row["target"] = S.doc_id(target_doc)
                add_edge(S.clause_id(cid), S.MENTIONS_DOC, S.doc_id(target_doc))
                add_edge(S.doc_id(did), S.CITES_DOC, S.doc_id(target_doc))
            cites.append(row)

    # ---- 统计 ----
    deg = collections.Counter()
    for (src, _, dst) in edges:
        deg[src] += 1
        deg[dst] += 1
    node_count = collections.Counter(n["type"] for n in nodes.values())
    edge_count = collections.Counter(e["type"] for e in edges.values())
    orphans = collections.Counter(n["type"] for nid, n in nodes.items() if deg[nid] == 0)

    stats = {
        "node_count": dict(node_count),
        "edge_count": dict(edge_count),
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "orphans": dict(orphans),
        "chapter_size_distribution": dict(sorted(collections.Counter(chapter_size.values()).items())),
        "external_laws": len(ext_count),
        "external_top": [[nodes[e]["label"], v] for e, v in ext_count.most_common(10)],
        "citation": {
            "instances": len(cites),
            "by_resolution": dict(collections.Counter(r["resolution"] for r in cites)),
        },
        "avg_degree": round(sum(deg.values()) / max(len(nodes), 1), 3),
    }

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "nodes.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for nid in sorted(nodes):
            f.write(json.dumps(nodes[nid], ensure_ascii=False) + "\n")
    with open(os.path.join(out_dir, "edges.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for key in sorted(edges):
            f.write(json.dumps(edges[key], ensure_ascii=False) + "\n")
    with open(os.path.join(out_dir, "graph_stats.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    with open(os.path.join(out_dir, "citation_edges.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["src_clause_id", "raw", "title", "article",
                                          "article_int", "resolution", "target"])
        w.writeheader()
        w.writerows(cites)

    _write_spotcheck(out_dir, cites, spotcheck_n)
    return stats


def _write_spotcheck(out_dir: str, cites: list[dict], n: int) -> None:
    """分层抽样：条文级引用优先，其余随机补足；按 (src, raw, target) 去重。

    verdict / note 留空给人填。去重是因为同一段正文可能重复引用同一条，
    重复样本会浪费人工时间。
    """
    import random
    seen: set[tuple] = set()

    def uniq(rows: list[dict]) -> list[dict]:
        out = []
        for r in rows:
            key = (r["src_clause_id"], r["raw"], r["target"])
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out

    must = uniq([r for r in cites if r["resolution"] == "clause"])
    rest = [r for r in cites if r["resolution"] != "clause"]
    random.Random(42).shuffle(rest)
    rest = uniq(rest)
    sample = (must + rest)[:n]
    fields = ["src_clause_id", "raw", "resolution", "target", "verdict", "note"]
    with open(os.path.join(out_dir, "spotcheck_sample.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in sample:
            w.writerow({k: r.get(k, "") for k in fields[:4]} | {"verdict": "", "note": ""})


def main() -> int:
    ap = argparse.ArgumentParser(description="构建六类节点分层异构图")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--spotcheck", type=int, default=100)
    args = ap.parse_args()
    stats = build(args.out, args.spotcheck)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

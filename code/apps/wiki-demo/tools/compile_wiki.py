"""Compile demo-v2 page text into a browsable Markdown Wiki and FTS5 index.

This is the structure/retrieval baseline: it preserves source page text and
organizes it hierarchically. It does not claim to semantically synthesize or
fact-check the source content.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path


CATALOG = {
    "01": ("industry", "行业研究", "行业估值分析", ["互联网", "估值", "行业"]),
    "02": ("industry", "行业研究", "AI商业化主题研究", ["AI", "人工智能", "商业化"]),
    "03": ("tencent", "腾讯", "腾讯个股估值与走势", ["腾讯", "Tencent", "股价", "估值"]),
    "04": ("tencent", "腾讯", "腾讯2025年第二季度及中期业绩公告", ["腾讯", "2025Q2", "中期业绩"]),
    "05": ("meituan", "美团", "美团2024年度报告", ["美团", "Meituan", "2024年报"]),
    "06": ("alibaba", "阿里巴巴", "阿里巴巴2025财务年度报告", ["阿里巴巴", "Alibaba", "2025财年"]),
    "07": ("tencent", "腾讯", "腾讯2025年第二季度业绩演示", ["腾讯", "2025Q2", "业绩演示"]),
    "08": ("tencent", "腾讯", "腾讯2024年第四季度及全年业绩演示", ["腾讯", "2024Q4", "全年业绩"]),
    "09": ("policy", "宏观政策", "国务院公报2025年第9号：提振消费专项行动方案", ["国务院公报", "提振消费", "消费政策"]),
    "10": ("tencent", "腾讯", "腾讯股份回购披露", ["腾讯", "股份回购", "翌日披露"]),
}
CATEGORY_TITLES = {
    "industry": "行业研究",
    "tencent": "腾讯",
    "meituan": "美团",
    "alibaba": "阿里巴巴",
    "policy": "宏观政策",
}
SOURCE_SLUGS = {
    "01": "industry-valuation",
    "02": "ai-commercialization",
    "03": "tencent-valuation",
    "04": "tencent-2025-q2-results",
    "05": "meituan-2024-annual-report",
    "06": "alibaba-fy2025-annual-report",
    "07": "tencent-2025-q2-presentation",
    "08": "tencent-2024-full-year-presentation",
    "09": "consumption-action-plan",
    "10": "tencent-share-buyback",
}
CHINESE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
NON_WORD = re.compile(r"[^\w\u3400-\u9fff]+", re.UNICODE)


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value or "source"


def fts_normalize(text: str) -> str:
    """Make Chinese searchable with the same overlapping bigrams at index/query time."""
    text = text.lower()

    def bigrams(match: re.Match) -> str:
        run = match.group(0)
        if len(run) == 1:
            return run
        return " ".join(run[i:i + 2] for i in range(len(run) - 1))

    text = CHINESE.sub(bigrams, text)
    return NON_WORD.sub(" ", text).strip()


def source_meta(filename: str) -> dict:
    match = re.match(r"(\d{2})_", filename)
    number = match.group(1) if match else ""
    key, category, title, aliases = CATALOG.get(
        number, ("other", "其他", Path(filename).stem, []))
    return {"number": number, "category_id": key, "category": category,
            "title": title, "aliases": aliases}


def write_page(path: Path, meta: dict, parts: list[dict], source: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    aliases = ", ".join(meta["aliases"])
    lines = [
        "---", f"page_id: {meta['page_id']}", f"title: {meta['title']}",
        f"type: source_document", f"category: {meta['category']}",
        f"aliases: [{aliases}]", f"source_id: {source['source_id']}",
        f"source_sha256: {source['source_sha256']}",
        f"source_file: {source['source_name']}", f"pdf_page_count: {source['page_count']}",
        "---", "", f"# {meta['title']}", "",
        f"- 所属：[{meta['category']}](../../categories/{meta['category_id']}/index.md)",
        f"- 原始文件：`{source['source_name']}`（{source['page_count']} 页）",
        f"- 来源 SHA-256：`{source['source_sha256']}`", "",
        "本页按原 PDF 页码保留可检索文本；内容尚未经过 LLM 归纳或事实核验。", "",
        "## 本页范围", "",
    ]
    if parts:
        lines.append(f"PDF 第 {parts[0]['page']}–{parts[-1]['page']} 页。")
    lines.extend(["", "## 页面文本", ""])
    for row in parts:
        method = "OCR" if row["ocr"]["status"] == "ok" else "PDF文字层"
        flags = "；标记：" + ", ".join(row["quality_flags"]) if row["quality_flags"] else ""
        lines.extend([f"### PDF 第 {row['page']} 页", "",
                      f"来源定位：`{source['source_name']}` 第 {row['page']} 页；文本方式：{method}{flags}。",
                      "", row["search_text"].strip(), ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def build(input_dir: Path, output: Path, pages_per_wiki_page: int = 20) -> dict:
    if output.exists():
        raise FileExistsError(f"输出目录已存在，为保留历史实验请使用新目录：{output}")
    source_rows = read_jsonl(input_dir / "sources.jsonl")
    page_rows = read_jsonl(input_dir / "pages.jsonl")
    source_by_id = {s["source_id"]: s for s in source_rows}
    pages_by_source: dict[str, list[dict]] = defaultdict(list)
    for row in page_rows:
        pages_by_source[row["source_id"]].append(row)

    output.mkdir(parents=True)
    wiki = output / "wiki"
    pages_dir = wiki / "pages"
    categories_dir = wiki / "categories"
    pages_dir.mkdir(parents=True)
    categories_dir.mkdir(parents=True)

    db_path = output / "retrieval.sqlite3"
    db = sqlite3.connect(db_path)
    db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE wiki_pages (
            page_id TEXT PRIMARY KEY, path TEXT NOT NULL, title TEXT NOT NULL,
            category TEXT NOT NULL, category_id TEXT NOT NULL, aliases TEXT NOT NULL,
            source_id TEXT NOT NULL, source_name TEXT NOT NULL,
            source_sha256 TEXT NOT NULL, page_from INTEGER NOT NULL, page_to INTEGER NOT NULL
        );
        CREATE TABLE wiki_sections (
            section_id TEXT PRIMARY KEY, page_id TEXT NOT NULL, path TEXT NOT NULL,
            title_path TEXT NOT NULL, source_id TEXT NOT NULL, source_name TEXT NOT NULL,
            source_sha256 TEXT NOT NULL, pdf_page INTEGER NOT NULL,
            extraction_method TEXT NOT NULL, quality_flags TEXT NOT NULL,
            body TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE sections_fts USING fts5(
            body, tokenize='unicode61'
        );
        CREATE INDEX section_page_idx ON wiki_sections(page_id);
        CREATE INDEX section_source_idx ON wiki_sections(source_id, pdf_page);
    """)

    source_manifests = []
    categories: dict[str, list[dict]] = defaultdict(list)
    for source in source_rows:
        meta = source_meta(source["source_name"])
        source_id = source["source_id"]
        rows = sorted(pages_by_source.get(source_id, []), key=lambda x: x["page"])
        meta.update({"page_id": "source-" + source_id,
                     "source_id": source_id, "source_name": source["source_name"],
                     "source_sha256": source["source_sha256"],
                     "page_count": source["page_count"]})
        categories[meta["category_id"]].append(meta)
        source_slug = f"{meta['number']}-{SOURCE_SLUGS.get(meta['number'], slug(meta['title']))}"
        source_dir = pages_dir / source_slug
        index_path = source_dir / "index.md"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        page_parts = []
        for start in range(0, len(rows), pages_per_wiki_page):
            subset = rows[start:start + pages_per_wiki_page]
            part_no = start // pages_per_wiki_page + 1
            part_path = source_dir / f"part-{part_no:03d}.md"
            write_page(part_path, meta, subset, source)
            rel_path = part_path.relative_to(wiki).as_posix()
            page_parts.append((part_path, rel_path, subset))
            first_page, last_page = subset[0]["page"], subset[-1]["page"]
            db.execute("INSERT INTO wiki_pages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (f"{meta['page_id']}-part-{part_no:03d}", rel_path, meta["title"],
                        meta["category"], meta["category_id"], " | ".join(meta["aliases"]),
                        source_id, source["source_name"], source["source_sha256"],
                        first_page, last_page))
            for row in subset:
                section_id = f"{source_id}-p{row['page']:04d}"
                title_path = f"{meta['category']} / {meta['title']} / PDF第{row['page']}页"
                method = "ocr" if row["ocr"]["status"] == "ok" else "native"
                flags = ",".join(row["quality_flags"])
                body = row["search_text"].strip()
                db.execute("INSERT INTO wiki_sections VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (section_id, f"{meta['page_id']}-part-{part_no:03d}", rel_path,
                            title_path, source_id, source["source_name"],
                            source["source_sha256"], row["page"], method, flags, body))
                db.execute("INSERT INTO sections_fts VALUES(?)", (fts_normalize(body),))
        idx_lines = [f"# {meta['title']}", "", f"分类：{meta['category']}", "",
                     f"来源：`{source['source_name']}`；共 {source['page_count']} 页。", "",
                     "本页目录按 PDF 页码范围拆分，点击分段页查看文本和引用定位。", ""]
        for part_path, rel_path, subset in page_parts:
            rel_from_index = part_path.name
            idx_lines.append(f"- [PDF 第 {subset[0]['page']}–{subset[-1]['page']} 页]({rel_from_index})")
        index_path.write_text("\n".join(idx_lines) + "\n", encoding="utf-8")
        source_manifests.append({**meta, "wiki_path": index_path.relative_to(wiki).as_posix(),
                                 "part_count": len(page_parts)})

    for category_id, members in categories.items():
        category_path = categories_dir / category_id / "index.md"
        category_path.parent.mkdir(parents=True, exist_ok=True)
        title = CATEGORY_TITLES.get(category_id, "其他")
        lines = [f"# {title}", "", "## 资料页面", ""]
        for item in members:
            item_slug = SOURCE_SLUGS.get(item["number"], slug(item["title"]))
            rel = Path("../../pages") / f"{item['number']}-{item_slug}" / "index.md"
            lines.append(f"- [{item['title']}]({rel.as_posix()})（{item['page_count']} 页）")
        category_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    root_lines = ["# 港股互联网资料 Wiki", "",
                  "本版采用目录层级定位 + SQLite FTS5/BM25 章节检索。页面保留来源 PDF 文本与页码；尚未做跨来源语义综合。", "",
                  "## 分类", ""]
    for category_id, members in categories.items():
        title = CATEGORY_TITLES.get(category_id, "其他")
        root_lines.append(f"- [{title}](categories/{category_id}/index.md)（{len(members)} 份资料）")
    root_lines.extend(["", "## 检索索引", "",
                       "`../retrieval.sqlite3` 按页面元数据和 PDF 页级段落建立全文索引。可用 `python3 tools/compile_wiki.py search --index ... --query ...` 检索并生成 LLM context pack。", ""])
    (wiki / "index.md").write_text("\n".join(root_lines) + "\n", encoding="utf-8")

    db.commit()
    section_count = db.execute("SELECT count(*) FROM wiki_sections").fetchone()[0]
    page_count = db.execute("SELECT count(*) FROM wiki_pages").fetchone()[0]
    db.close()
    manifest = {"schema_version": 1, "input_dir": str(input_dir.resolve()),
                "source_count": len(source_rows), "pdf_page_count": len(page_rows),
                "wiki_markdown_page_count": page_count + len(source_rows) + len(categories) + 1,
                "wiki_page_parts": page_count, "indexed_sections": section_count,
                "pages_per_wiki_page": pages_per_wiki_page,
                "generator": "structure-first-text-preserving; no LLM synthesis",
                "sources": source_manifests,
                "notes": ["Page hierarchy is seeded from source metadata and is editable.",
                          "Search sections preserve physical PDF page locators.",
                          "OCR and extraction quality flags travel with the retrieval result."]}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    return manifest


def search_scope(query: str) -> list[str] | None:
    """Use explicit entity/period clues as coarse routing filters."""
    q = query.lower()
    if any(x in q for x in ("腾讯", "tencent")):
        if any(x in q for x in ("2025q2", "2025 q2", "2025年第二季度", "二季度", "第二季度")):
            return ["04_%", "07_%"]
        if any(x in q for x in ("2024q4", "2024 q4", "2024年第四季度", "第四季度")):
            return ["08_%"]
        if any(x in q for x in ("回购", "翌日披露")):
            return ["10_%"]
        return ["03_%", "04_%", "07_%", "08_%", "10_%"]
    if any(x in q for x in ("美团", "美團", "meituan")):
        return ["05_%"]
    if any(x in q for x in ("阿里巴巴", "阿里", "alibaba")):
        return ["06_%"]
    if any(x in q for x in ("提振消费", "提振消費", "消费专项", "消費專項", "国务院公报")):
        return ["09_%"]
    if any(x in q for x in ("ai商业化", "ai 商业化", "人工智能商业化")):
        return ["02_%"]
    return None


def expand_search_terms(query: str) -> list[str]:
    """Small explicit query normalization for known Simplified/Traditional terms."""
    additions = []
    q = query.lower()
    if "2025" in q and any(x in q for x in ("季度", "q2", "二季度")):
        additions.extend(["二零二五年第二季", "二零二五年第二季度", "二零二五年第二季收入"])
    if "2024" in q and any(x in q for x in ("季度", "q4", "四季度")):
        additions.extend(["二零二四年第四季", "二零二四年第四季度"])
    pairs = [
        ("广告", "廣告 營銷服務 營銷 行銷服務"),
        ("营销服务", "營銷服務 行銷服務 廣告"),
        ("營銷服務", "广告 营销服务 行銷服務"),
        ("收入", "營收 營業收入"),
        ("盈利", "利潤 溢利 淨利潤"),
        ("利润", "利潤 溢利 經營溢利"),
        ("经营利润", "經營溢利 經營利潤 營業利潤"),
        ("核心本地商业", "核心本地商業"),
        ("核心本地商業", "核心本地商业"),
        ("同比", "同比變動 同比增长 同比增長"),
        ("专项行动", "專項行動"),
        ("消费能力保障", "消費能力保障"),
        ("金融支持", "金融機構 金融支持 財政貼息 貸款"),
        ("提振消费", "提振消費"),
        ("提振消費", "提振消费"),
        ("非ifrs", "非國際財務報告準則 非國際財務報告"),
    ]
    for source, target in pairs:
        if source.lower() in q:
            additions.append(target)
    return list(dict.fromkeys([query, *additions]))


def concept_groups(query: str) -> list[list[set[str]]]:
    """Return phrase groups used to re-rank broad FTS candidates by concepts."""
    variants_by_phrase = [
        (("核心本地商业", "核心本地商業"), ["核心本地商业", "核心本地商業"]),
        (("经营利润", "經營利潤", "经营溢利", "經營溢利"),
         ["经营利润", "經營利潤", "經營溢利", "經營溢利"]),
        (("提振消费专项行动", "提振消費專項行動", "提振消费", "提振消費"),
         ["提振消费专项行动", "提振消費專項行動", "提振消费", "提振消費"]),
        (("消费能力保障", "消費能力保障"), ["消费能力保障", "消費能力保障"]),
        (("金融支持", "金融支持", "金融機構", "金融机构"),
         ["金融支持", "金融機構", "金融机构", "财政贴息", "財政貼息"]),
        (("收入", "營收"), ["收入", "營收", "營業收入"]),
        (("盈利", "利润", "利潤"), ["盈利", "利润", "利潤", "溢利", "淨利潤"]),
        (("同比", "同比增长", "同比增長"), ["同比", "同比增长", "同比增長", "同比變動"]),
        (("广告", "廣告"), ["广告", "廣告", "营销服务", "營銷服務", "行銷服務"]),
    ]
    q = query.lower()
    groups = []
    if "non-ifrs" in q or "非ifrs" in q:
        groups.append([set(fts_normalize(v).split()) for v in
                       ["非IFRS盈利", "非國際財務報告準則盈利", "非國際財務報告準則"]])
    for keys, variants in variants_by_phrase:
        if any(key.lower() in q for key in keys):
            groups.append([set(fts_normalize(v).split()) for v in variants if fts_normalize(v)])
    # Handle other whitespace-delimited content phrases, while excluding routing fields.
    for phrase in re.split(r"\s+", query.strip()):
        compact = phrase.lower()
        if (not compact or re.search(r"20\d{2}", compact) or
                any(word in compact for word in ("季度", "二季度", "四季度", "q1", "q2", "q3", "q4"))):
            continue
        if compact in {"腾讯", "tencent", "美团", "美團", "meituan", "阿里", "阿里巴巴", "alibaba"}:
            continue
        if any(any(key.lower() in compact for key in keys) for keys, _ in variants_by_phrase):
            continue
        if len(compact) < 2:
            continue
        normalized = set(fts_normalize(phrase).split())
        if normalized:
            groups.append([normalized])
    return groups


def search(index: Path, query: str, top_k: int = 8, context_path: Path | None = None) -> list[dict]:
    routed_source_patterns = search_scope(query)
    normalized_query = " ".join(expand_search_terms(query))
    terms = list(dict.fromkeys(fts_normalize(normalized_query).split()))
    if not terms:
        raise ValueError("查询需要至少包含两个连续汉字或一个英文词")
    match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms[:64])
    db = sqlite3.connect(index)
    db.row_factory = sqlite3.Row
    source_clause = ""
    params: list = [match]
    if routed_source_patterns:
        source_clause = " AND (" + " OR ".join("s.source_name LIKE ?" for _ in routed_source_patterns) + ")"
        params.extend(routed_source_patterns)
    params.append(max(top_k * 100, top_k))
    rows = db.execute("""
        SELECT f.rowid, s.section_id, s.page_id, s.path, s.title_path,
               s.source_id, s.source_name, s.source_sha256, s.pdf_page,
               s.extraction_method, s.quality_flags, s.body,
               bm25(sections_fts) AS rank
        FROM sections_fts f JOIN wiki_sections s ON s.rowid=f.rowid
        WHERE sections_fts MATCH ?
        """ + source_clause + " ORDER BY rank LIMIT ?", params).fetchall()
    db.close()
    results = []
    groups = concept_groups(query)
    for row in rows:
        text = row["body"]
        # Drop mostly empty cover/section divider pages unless the body has enough
        # material to serve as answer context.
        if len(text.strip()) < 80:
            continue
        body_token_set = set(fts_normalize(text).split())
        coverage = []
        for variants in groups:
            coverage.append(max((len(tokens & body_token_set) / max(len(tokens), 1)
                                 for tokens in variants), default=0))
        concept_hits = sum(score >= 0.5 for score in coverage)
        if groups and len(groups) >= 2 and concept_hits < 2:
            continue
        if not groups and not body_token_set.intersection(terms):
            continue
        snippet = re.sub(r"\s+", " ", text)
        results.append({"section_id": row["section_id"], "page_id": row["page_id"],
                        "path": row["path"], "title_path": row["title_path"],
                        "source_id": row["source_id"], "source_name": row["source_name"],
                        "source_sha256": row["source_sha256"], "pdf_page": row["pdf_page"],
                        "extraction_method": row["extraction_method"],
                        "quality_flags": row["quality_flags"].split(",") if row["quality_flags"] else [],
                        "rank": row["rank"], "concept_hits": concept_hits,
                        "concept_coverage": round(sum(coverage), 4),
                        "snippet": snippet[:1000], "text": text})
    results.sort(key=lambda item: (-item["concept_hits"], -item["concept_coverage"], item["rank"]))
    # Each result is a PDF-page section. Several sections may live in the same
    # Wiki subpage, which is useful context rather than a duplicate.
    results = results[:top_k]
    if context_path:
        context_path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# 检索上下文包", "", f"查询：{query}", "",
                 "以下是 FTS5/BM25 命中的 Wiki 页面段落。请回答时只依据这些证据；如 OCR 标记或证据不足，应提示并回查原 PDF。", ""]
        for i, item in enumerate(results, 1):
            lines.extend([f"## 证据 {i}：{item['title_path']}", "",
                          f"Wiki：`{item['path']}`；原始来源：`{item['source_name']}` PDF 第 {item['pdf_page']} 页；提取：{item['extraction_method']}；质量标记：{', '.join(item['quality_flags']) or '无'}。", "",
                          item["text"], ""])
        context_path.write_text("\n".join(lines), encoding="utf-8")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build", help="由 demo-v2 生成分层 Markdown Wiki 和 FTS5 索引")
    build_parser.add_argument("--input", type=Path, required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    build_parser.add_argument("--pages-per-wiki-page", type=int, default=20)
    search_parser = commands.add_parser("search", help="检索 Wiki 并可输出交给 LLM 的 context pack")
    search_parser.add_argument("--index", type=Path, required=True)
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--top-k", type=int, default=8)
    search_parser.add_argument("--context-output", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        if args.pages_per_wiki_page < 1:
            parser.error("--pages-per-wiki-page 必须大于 0")
        print(json.dumps(build(args.input, args.output, args.pages_per_wiki_page),
                          ensure_ascii=False, indent=2))
    else:
        results = search(args.index, args.query, args.top_k, args.context_output)
        print(json.dumps([{k: v for k, v in item.items() if k not in {"text", "source_sha256"}}
                          for item in results], ensure_ascii=False, indent=2))
        if args.context_output:
            print(f"Context pack: {args.context_output}")


if __name__ == "__main__":
    main()

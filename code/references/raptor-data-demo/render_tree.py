"""Render the RAPTOR node hierarchy as an expandable, connected tree diagram."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


def escape(value: Any) -> str:
    """Escape source text for safe insertion into the static viewer."""
    return html.escape(str(value), quote=True)


def source_labels(node: dict[str, Any]) -> str:
    """Format the source page locations carried by a node."""
    labels = []
    for source in node.get("sources", []):
        for page in source.get("pages", []):
            labels.append(f'{escape(source["source_name"])} · 第 {escape(page)} 页')
    return "；".join(labels)


def render_node(
    node_id: int,
    nodes: dict[int, dict[str, Any]],
    open_by_default: bool = False,
) -> str:
    """Render a summary node with connected descendants or a source leaf."""
    node = nodes[node_id]
    if node["layer"] == 0:
        return (
            '<article class="leaf-node">'
            f'<div class="leaf-heading"><span class="leaf-dot"></span>'
            f'<span>片段 {node_id}</span><span class="source">{source_labels(node)}</span></div>'
            f'<div class="leaf-text">{escape(node["text"])}</div>'
            "</article>"
        )

    children = "".join(render_node(child, nodes) for child in node["children"])
    opened = " open" if open_by_default else ""
    return (
        f'<details class="summary-node"{opened}>'
        f'<summary><span class="node-kind">摘要节点 {node_id}</span>'
        f'<span class="child-count">聚合 {len(node["children"])} 个子节点</span>'
        f'<span class="node-source">{source_labels(node)}</span>'
        f'<span class="chevron" aria-hidden="true">⌄</span></summary>'
        f'<div class="summary-text">{escape(node["text"])}</div>'
        f'<div class="branch"><div class="branch-list">{children}</div></div>'
        "</details>"
    )


def render(data: dict[str, Any]) -> str:
    """Build a standalone HTML tree view with expandable parent nodes."""
    nodes = {node["node_id"]: node for node in data["nodes"]}
    top_layer = max(node["layer"] for node in data["nodes"])
    roots = [node["node_id"] for node in data["nodes"] if node["layer"] == top_layer]
    groups = "".join(
        render_node(root, nodes, open_by_default=(index == 0))
        for index, root in enumerate(roots)
    )
    run = data.get("run", {})
    root_label = "跨来源全局语料" if run.get("scope") == "global" else "单来源演示语料"
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RAPTOR 树 · 片段聚合关系</title>
  <style>
    :root {{ color-scheme: light dark; font: 15px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: #18243a; background: #f3f6fb; }}
    .page {{ max-width: 1120px; margin: 0 auto; padding: 34px 24px 64px; }}
    header {{ margin-bottom: 24px; }}
    h1 {{ margin: 0; font-size: 27px; letter-spacing: -.02em; }}
    .subtitle {{ margin: 6px 0 18px; color: #65738a; }}
    .stats {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .stat {{ padding: 7px 12px; border-radius: 999px; background: #e8edf6; color: #34435b; font-size: 13px; }}
    .diagram {{ max-width: 940px; margin: 0 auto; }}
    .global-root {{ width: min(420px, 100%); margin: 0 0 0 25px; padding: 15px 18px; border-radius: 14px; background: #263d83; color: #fff; text-align: center; box-shadow: 0 8px 20px #263d8329; }}
    .global-root strong {{ display: block; font-size: 16px; }}
    .global-root span {{ color: #d8e0ff; font-size: 12px; }}
    .root-stem {{ width: 2px; height: 26px; margin-left: 25px; background: #9ba9c3; }}
    .tree {{ position: relative; display: grid; gap: 14px; margin-left: 25px; padding-left: 28px; border-left: 2px solid #c7d1e5; }}
    .summary-node {{ overflow: hidden; border: 1px solid #dce3ee; border-radius: 15px; background: #fff; box-shadow: 0 3px 12px #1e3a5f0b; }}
    .tree > .summary-node {{ position: relative; }}
    .tree > .summary-node::before {{ position: absolute; z-index: 1; top: 27px; left: -30px; width: 28px; border-top: 2px solid #c7d1e5; content: ""; }}
    .summary-node > summary {{ position: relative; display: grid; grid-template-columns: 1fr auto; gap: 5px 12px; align-items: center; padding: 16px 46px 16px 19px; list-style: none; cursor: pointer; }}
    .summary-node > summary::-webkit-details-marker {{ display: none; }}
    .node-kind {{ color: #334ca3; font-weight: 650; }}
    .child-count {{ justify-self: end; padding: 3px 9px; border-radius: 999px; background: #eef1ff; color: #455bb2; font-size: 12px; white-space: nowrap; }}
    .node-source {{ grid-column: 1 / -1; color: #718096; font-size: 12px; overflow-wrap: anywhere; }}
    .chevron {{ position: absolute; top: 20px; right: 19px; color: #667085; transition: transform .18s ease; }}
    .summary-node[open] > summary .chevron {{ transform: rotate(180deg); }}
    .summary-text {{ padding: 0 20px 17px; color: #202b3c; white-space: pre-wrap; }}
    .branch {{ margin: 0 0 19px 30px; padding: 0 20px 0 23px; border-left: 2px solid #c7d1e5; }}
    .branch-list {{ display: grid; gap: 9px; }}
    .leaf-node {{ position: relative; padding: 11px 14px; border: 1px solid #e3e8f1; border-radius: 10px; background: #f9fbfe; }}
    .leaf-node::before {{ position: absolute; top: 21px; left: -25px; width: 22px; border-top: 2px solid #c7d1e5; content: ""; }}
    .leaf-heading {{ display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; margin-bottom: 4px; color: #56657b; font-size: 12px; }}
    .leaf-dot {{ width: 7px; height: 7px; flex: 0 0 7px; border-radius: 50%; background: #5c75cf; }}
    .source {{ color: #77859a; }}
    .leaf-text {{ color: #263348; white-space: pre-wrap; overflow-wrap: anywhere; }}
    @media (max-width: 600px) {{
      .page {{ padding: 24px 14px 42px; }}
      h1 {{ font-size: 22px; }}
      .global-root {{ margin-left: 13px; }}
      .root-stem {{ margin-left: 13px; }}
      .tree {{ margin-left: 13px; padding-left: 17px; }}
      .tree > .summary-node::before {{ left: -19px; width: 17px; }}
      .summary-node > summary {{ padding-left: 14px; }}
      .summary-text {{ padding-right: 14px; padding-left: 15px; }}
      .branch {{ margin-left: 18px; padding-right: 10px; padding-left: 17px; }}
      .leaf-node::before {{ left: -19px; width: 16px; }}
    }}
    @media (prefers-color-scheme: dark) {{
      body {{ color: #e8edf5; background: #111722; }}
      .subtitle, .node-source, .leaf-heading, .source {{ color: #a5b1c3; }}
      .stat {{ color: #d5def0; background: #202b3d; }}
      .global-root {{ background: #344a8a; box-shadow: none; }}
      .root-stem, .tree {{ background-color: transparent; border-color: #43516b; }}
      .tree > .summary-node::before {{ border-color: #43516b; }}
      .summary-node {{ border-color: #303c50; background: #192232; box-shadow: none; }}
      .node-kind {{ color: #a9b9ff; }}
      .child-count {{ color: #c3ccff; background: #293457; }}
      .summary-text {{ color: #e6ebf3; }}
      .branch, .leaf-node::before {{ border-color: #43516b; }}
      .leaf-node {{ border-color: #303c50; background: #151e2c; }}
      .leaf-text {{ color: #dce4ef; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header>
      <h1>RAPTOR 树：哪些片段被聚合成了什么</h1>
      <p class="subtitle">展开摘要节点查看它聚合的子片段与来源页码。</p>
      <div class="stats">
        <span class="stat">{run.get("page_count", "?")} 页</span>
        <span class="stat">{data.get("leaf_node_count", "?")} 个叶子片段</span>
        <span class="stat">{len(roots)} 个顶层摘要节点</span>
        <span class="stat">{data.get("num_layers", "?")} 层摘要</span>
      </div>
    </header>
    <section class="diagram" aria-label="RAPTOR 父节点和子片段关系">
      <div class="global-root"><strong>{root_label}</strong><span>展示用根节点 · {run.get("source_count", "?")} 份资料 · {run.get("page_count", "?")} 页</span></div>
      <div class="root-stem"></div>
      <div class="tree">{groups}</div>
    </section>
  </main>
</body>
</html>
'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a RAPTOR hierarchy as a tree diagram")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.input.with_name("tree-view.html")
    data = json.loads(args.input.read_text(encoding="utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(data), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

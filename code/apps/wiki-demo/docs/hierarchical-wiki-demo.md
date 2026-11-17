# 层级 Wiki 与全文检索 Demo

日期：2026-11-12  
输入：`data-prepared/demo-v2`  10 份 PDF、885 页。  
产物：`artifacts/wiki-v6/`

## 本次验证什么

本轮按“多页 Markdown + 层级目录 + SQLite FTS5/BM25”做最小可运行版本，重点验证：

1. 数据能否形成可浏览的分类 → 来源 → 子页结构；
2. 用户问题能否先缩小到公司/政策分支，再落到相关 PDF 页级段落；
3. 命中结果能否带 Wiki 路径、原始文件名和物理页码，形成可交给 LLM 的 context pack。

代码在 `tools/compile_wiki.py`。输出为 67 个 Markdown 文件：1 个总目录、5 个分类索引、10 个来源索引、51 个内容分段页（每页最多 20 个 PDF 页），加上 SQLite 中 885 条页级检索记录。查询对正文索引使用中文 bigram 规范化和 FTS5/BM25；已知公司/年份线索用于缩小来源范围，别名和繁简体词形通过轻量映射扩展。实际使用的检索上下文保存在 `artifacts/wiki-v6/retrieval/`。

## 目录形态

```text
wiki/
  index.md
  categories/
    industry/index.md
    tencent/index.md
    meituan/index.md
    alibaba/index.md
    policy/index.md
  pages/
    04-tencent-2025-q2-results/
      index.md
      part-001.md
      part-002.md
      part-003.md
    05-meituan-2024-annual-report/
      index.md
      part-001.md
      ...
```

每个 `part-NNN.md` 按物理 PDF 页码分节，保留原文字/OCR 文本、来源文件名、页码、提取方式及质量标记。长 PDF 不会形成一个不可控的大 Markdown 文件；数据库仍以 PDF 页作为更细的检索单位。

## 实际查询样例

| Query | 检索结果 | 观察 |
| --- | --- | --- |
| 腾讯 2025 年第二季度营销服务收入同比增长 | 腾讯 2025Q2 业绩公告 PDF 第 6、7 页等 | 第 6 页命中分部收入表：营销服务收入同比增长 20%；上下文保留原文数值和来源页码。 |
| 美团 2024 核心本地商业收入、经营利润 | 美团年报 PDF 第 11、19、20 页等 | 第 11 页包含全年分部收入和经营溢利，第 20 页包含 Q4 分部表现。 |
| 提振消费专项行动的消费能力/金融支持 | 国务院公报 PDF 第 18、21、22 页等 | 命中目标章节，但这些是 OCR 文本，结果带有 `ocr_needs_visual_review` 等质量标记。 |

搜索产生的上下文文件：

- `artifacts/wiki-v6/retrieval/tencent-q2-context.md`
- `artifacts/wiki-v6/retrieval/meituan-context.md`
- `artifacts/wiki-v6/retrieval/policy-context.md`

目前 context pack 是待回答证据，不是模型答案。本次没有调用 DeepSeek；下游接入 LLM 时，将 context pack 连同“只依据证据、保留来源引用、证据不足时说明不足”的指令传入即可。

## 结果边界

这一版是**按来源整理的可检索 Wiki 骨架**，不是已经由 LLM 跨文档综合出来的主题知识 Wiki。5 个分类和来源页面由现有资料清单种子化；正文内容保留原页文本。它能验证目录导航、子页定位、段落召回和来源追溯，但还不能证明“先把多份材料归纳成一个主题知识页”这一生成步骤的效果。

下一步生成内容页时可复用此处的实体/期间/来源元数据、页级证据 ID、FTS 检索和路径回溯；新增的 LLM 阶段应先做可追溯的 claims/证据抽取，再按 page map 生成跨来源主题页，随后与来源页并存并经引用及质量检查后发布。索引可从批准后的 Markdown 页面重建。

检索当前使用少量 demo 特化的来源路由和繁简体别名映射，尚非通用中文问答检索器。OCR 错误、未录入的别名、页面 schema 和 query gold set 仍会影响召回与排序。FTS/BM25 结果应在固定问题集上逐步校准；本轮三条查询只是功能演示，不能作为召回率评估。

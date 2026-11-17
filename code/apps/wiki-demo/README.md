# Wiki Demo v0.2

基于 Karpathy LLM Wiki 思路的独立 Python demo。Karpathy Gist 只有设计文档，本项目没有复制不存在的上游生成器代码。

当前实现：本地 MD/TXT/PDF → 带来源版本及位置的资料快照 → 单次 LLM 生成多个主题页面 → 结构与引用 ID 校验 → Markdown Wiki、运行记录及人工审阅表。

真实模型为用户指定的 **DeepSeek V4 Pro**。同时提供 `extractive` 离线原文摘录模式，仅验证数据和文件流程，不生成语义综合，也不能比较 Prompt 效果。

完整源码目录和逐文件说明见 [代码库导读](docs/codebase.md)。System Prompt 独立保存在 `prompts/system.md`，由主流程在运行时读取。

## 1. 运行环境

Python 3.11+。核心流程使用标准库；PDF 使用 pypdf，DeepSeek HTTPS 连接可使用 certifi 提供可信 CA（本机已安装）。在本目录运行命令：

```sh
cd apps/wiki-demo
python3 -m wiki_demo --help
```

如需在独立虚拟环境安装并解析 PDF：

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[pdf,api]'
```

PDF 使用可选依赖 pypdf。当前机器已有 pypdf，可直接运行；新环境需按上面安装。默认路径相对当前目录，使用命令行入口 `wiki-demo` 时也应在项目目录运行或显式传入 Prompt、配置路径。

## 2. 不需要 API 的演示

```sh
python3 -m wiki_demo prepare --input examples/sources --output artifacts/sample-corpus
python3 -m wiki_demo generate --corpus artifacts/sample-corpus --output artifacts/offline-demo --provider extractive
python3 -m wiki_demo validate artifacts/offline-demo
```

打开 `artifacts/offline-demo/wiki/index.md`，可以逐页查看摘录并跳转到证据快照。

为保护实验记录，输出目录必须不存在。重复运行请换一个名字，如 `artifacts/offline-demo-02`。已经生成的资料快照可以重复用于多个候选。

## 3. 填入 DeepSeek 密钥后生成

当前工作区已配置本地 `.env`，可以直接打开修改；从 Git 克隆到新环境时先复制 `.env.example` 为 `.env`：

```text
DEEPSEEK_API_KEY=你的密钥
```

`.env` 已加入 Git 忽略。已有环境变量优先于文件。不要把密钥写入 Prompt 或 TOML；日志不保存认证请求头。

默认配置在 `configs/deepseek.toml`：模型 `deepseek-v4-pro`、官方地址 `https://api.deepseek.com`、JSON 输出、非流式、关闭 thinking、输出上限 8192 token。可根据实验修改；开启 thinking 后不发送 temperature。

```sh
python3 -m wiki_demo generate --corpus artifacts/sample-corpus --output artifacts/deepseek-baseline --provider deepseek --prompt prompts/baseline.md
python3 -m wiki_demo generate --corpus artifacts/sample-corpus --output artifacts/deepseek-synthesis --provider deepseek --prompt prompts/synthesis.md
python3 -m wiki_demo compare artifacts/deepseek-baseline artifacts/deepseek-synthesis
```

以上两个 generate 会分别向配置的 DeepSeek API 发送所选资料的提取文本并产生模型调用费用；prepare、extractive、validate、compare 均在本地执行。当前版本不自动重试模型请求。

接口依据（核查日期 2026-11-11）：[模型与地址](https://api-docs.deepseek.com/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)。JSON 模式不能替代本地 schema 与引用检查。

## 4. 使用工作区现有 PDF

当前用户确定先用 **03 号腾讯研报和 07 号腾讯业绩演示**，共 32 页，生成一篇主题 Wiki。两份文件的文字层完整提取，不只抽取前几页：

```sh
python3 -m wiki_demo prepare --input ../../data --pattern '03*.pdf' --pattern '07*.pdf' --output artifacts/tencent-two-pdfs
python3 -m wiki_demo generate --corpus artifacts/tencent-two-pdfs --output artifacts/tencent-single-wiki --provider deepseek --config configs/tencent-demo.toml --system-prompt prompts/system.md --prompt prompts/single-wiki.md --single-page
```

`--single-page` 同时约束提示词和校验器，要求恰好一个内容页面。正文为 `wiki/tencent-research.md`；`index.md` 和 `evidence.md` 是导航与证据附件。

这次运行已完成。输出目录已存在时，复用资料快照并更换生成输出目录名。`configs/tencent-demo.toml` 使用 120000 字符输入上限和 16384 token 输出预算。

工作区 `../../data/` 已有 10 份 PDF。建议先选择少量页面，验证文本提取与页码引用：

```sh
python3 -m wiki_demo prepare --input ../../data --pattern '03*.pdf' --pdf-pages 1-2 --output artifacts/tencent-sample-corpus
python3 -m wiki_demo generate --corpus artifacts/tencent-sample-corpus --output artifacts/tencent-offline --provider extractive
```

真实 LLM 生成时，将最后一条命令的 provider 改为 `deepseek` 并使用新输出目录。

`--pdf-pages` 使用 PDF 文件物理页码，从 1 开始；与纸面印刷页码可能不同。不指定时处理匹配文档的所有页面；`--pattern` 可重复。不传 pattern 时递归读取 MD/TXT/PDF。

扫描版 09 号公报没有文字层，需要后续增加 OCR；当前会记录 `no_text`，不会假装已经读懂。无可用证据时拒绝生成。长年报、表格顺序和图表语义需要后续专门处理。本版仅支持文字层。

## 5. 模块与产物

```text
wiki_demo/
  sources.py       资料读取、版本、页码和字符位置、快照完整性
  providers.py     DeepSeek HTTP 接口与离线摘录适配器
  generation.py    Prompt 组装、生成、Markdown 渲染、运行比较
  validation.py    候选结构与引用 ID 校验
  storage.py       JSON、哈希与独占输出目录
  cli.py           prepare / generate / validate / compare
prompts/           独立 system、baseline、synthesis 和单篇 Wiki Prompt
configs/           无密钥的模型配置
examples/sources/  人工虚构的小样例
tests/             流程、失败场景与模拟 HTTP 测试
```

准备资料输出 `corpus.json`（提取文本和证据）与 `manifest.json`（范围、版本及问题）。原始资料不修改、不上传。

每次生成输出：

| 文件 | 用途 |
| --- | --- |
| `run.json` | 资料/Prompt 哈希、配置、状态、耗时、API token 使用量 |
| `corpus.json` | 本次输入的独立快照，便于重现 |
| `system.md`、`prompt.md`、`request.json` | 实际提示词与提供给模型的上下文，不含密钥 |
| `response.txt`、`candidate.json` | 模型原始正文与解析后的候选 |
| `validation.json` | 自动结构检查和材料引用统计 |
| `review.csv` | 按主张生成的人工证据核验表 |
| `wiki/index.md` | 人可浏览的页面目录 |
| `wiki/*.md`、`wiki/evidence.md` | 候选页面与可跳转的证据摘录 |

模型请求或校验失败会保留失败状态；无效 JSON/引用不生成 Wiki。输入超预算和缺少密钥在调用前报错。不要把 `status=generated` 解读为“语义正确”。

## 6. 测试与当前边界

```sh
python3 -m unittest discover -s tests -v
```

18 项测试通过，测试内的 DeepSeek 响应使用模拟数据，不消耗 API。此外已单独运行真实小样例和两份 PDF 的生成，结果以对应产物的 `run.json` 为准。

本版是程序控制步骤的**直接生成工作流**，模型负责内容综合；尚未让模型自主选择工具、多轮规划和行动。输入超预算会拒绝；尚未实现全库分批、主题聚类、OCR、语义评分、问答更新、自动维护、企业数据库和部署。

`validate` 检查候选 JSON 和快照，不能发现手工编辑 Markdown 后的语义变化；当前候选 JSON 是渲染依据。Wiki 向量索引及 Markdown 编辑回写留待后续实现。

参见 [评估说明](docs/evaluation.md) 与 [详细技术报告](../../docs/technical-report.md)。

## 7. `demo-v2` 来源文本浏览器与全文检索基线

下面的代码把资料目录编译成多级 Markdown：分类索引 → 每份资料的索引页 → 按 PDF 页码范围拆分的内容页。分类是按资料编号人工映射的，正文保留原 PDF/OCR 文本；没有调用 LLM 做主题分类、跨文档综合或生成知识页，也没有生成知识页之间的语义链接。SQLite FTS5 为每个 PDF 页建立全文索引，中文文本用 bigram 规范化；检索结果保留来源文件和物理页码。因此该产物是浏览与检索基线，不是目标 Wiki。

```sh
python3 tools/compile_wiki.py build \
  --input ../../data-prepared/demo-v2 \
  --output artifacts/wiki-hierarchical-v1 \
  --pages-per-wiki-page 20

python3 tools/compile_wiki.py search \
  --index artifacts/wiki-hierarchical-v1/retrieval.sqlite3 \
  --query '腾讯 2025年第二季度 营销服务收入 同比增长' \
  --top-k 5 \
  --context-output artifacts/wiki-hierarchical-v1/retrieval/tencent-q2-context.md
```

当前代码验证的是**按来源浏览 + FTS5/BM25 页级段落检索**。真正的目标 Wiki 还需要 LLM 证据抽取、跨来源概念合并、页面规划、主题/entity 多页生成和语义 links；本版不调用模型。

本次实际输出位于 `artifacts/wiki-v6/`：5 个分类索引、10 个来源索引、51 个内容分段页、885 个页级检索段落。具体试查和局限见 [层级 Wiki demo 记录](docs/hierarchical-wiki-demo.md)。

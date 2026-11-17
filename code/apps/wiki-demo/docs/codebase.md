# 代码库导读：两份 PDF 到一篇 Wiki

更新日期：2026-11-11。

## 当前做什么

本 demo 将原始文档转成带来源的证据，再交给 DeepSeek V4 Pro 综合生成 Wiki。模型生成内容和引用 ID，程序负责读取资料、调用接口、验证结构和写文件。

当前选用 03 号腾讯研报（9 页）和 07 号腾讯业绩演示（23 页）。最终只有一篇主题正文；索引和证据文件是附件。实现是固定步骤的生成工作流，尚未让模型自主选择工具、多轮规划和行动。

## 源码与配置完整结构

```text
中金/
├── data/                            原始 PDF 库
├── docs/technical-report.md          持续技术报告
├── notes/                           讨论记录
├── references/karpathy-llm-wiki/      上游设计说明
└── apps/wiki-demo/                   独立 Git 代码库
    ├── .git/                        版本控制元数据
    ├── .gitignore                   忽略密钥、产物和缓存
    ├── .env                         本地 API 密钥，不提交
    ├── .env.example                 空白密钥模板
    ├── README.md                    安装与运行说明
    ├── pyproject.toml               包信息、入口、可选依赖
    ├── configs/
    │   ├── deepseek.toml            小样例默认模型参数
    │   └── tencent-demo.toml        两份 PDF 的输入输出预算
    ├── prompts/
    │   ├── system.md                API 的 system message
    │   ├── baseline.md              通用生成任务
    │   ├── synthesis.md             强调跨来源综合的任务
    │   └── single-wiki.md           两份资料生成单篇 Wiki
    ├── wiki_demo/
    │   ├── __init__.py              包与版本信息
    │   ├── __main__.py              python -m wiki_demo 入口
    │   ├── cli.py                   解析命令并调用主流程
    │   ├── sources.py               读取资料、切片与来源定位
    │   ├── providers.py             密钥加载、DeepSeek HTTP 调用
    │   ├── generation.py            组织生成、渲染与运行比较
    │   ├── validation.py            页面结构和引用检查
    │   └── storage.py               JSON、哈希和目录操作
    ├── examples/sources/
    │   ├── 01-cache.md              虚构缓存策略 A
    │   ├── 02-events.md             虚构缓存策略 B
    │   └── 03-comparison.md         虚构比较资料
    ├── tests/
    │   └── test_pipeline.py         流程与失败场景测试
    ├── docs/
    │   ├── codebase.md              本导读
    │   └── evaluation.md            评估边界
    └── artifacts/                   本地运行产物，不提交
        ├── sample-corpus/           自带样例快照
        ├── offline-demo/            离线摘录演示
        ├── tencent-two-pdfs/        两份完整 PDF 的快照
        ├── tencent-single-wiki/     此次真实模型运行
        │   ├── corpus.json         本次资料快照
        │   ├── system.md           实际 system 提示词
        │   ├── prompt.md           实际任务提示词
        │   ├── request.json        实际输入，不含密钥
        │   ├── response.txt        模型原始正文
        │   ├── candidate.json      解析后的候选内容
        │   ├── validation.json     自动检查结果
        │   ├── run.json            模型用量与运行配置
        │   ├── review.csv          人工审阅表
        │   ├── spot-check.md       本次有限抽查记录
        │   └── wiki/
        │       ├── tencent-research.md  唯一主题正文
        │       ├── index.md             导航
        │       └── evidence.md          原文证据附件
        └── 其他历史运行目录/         接口检查、失败或旧演示
```

上图列出全部源码、Prompt 和配置；自动缓存、Git 内部文件及历史产物不逐一展开。

## 主流程

```text
data 中的 03、07 两份 PDF
    ↓ prepare：sources.py
corpus.json / manifest.json
    ↓ generate：generation.py
system.md + single-wiki.md + 全部选定证据
    ↓ providers.py
DeepSeek Chat Completions API
    ↓ JSON 候选
validation.py：单页面、字段与引用 ID 检查
    ↓ render
tencent-research.md + 导航/证据附件
```

### sources.py：文件转成有位置的证据

`prepare()` 按命令行模式选择文件，读取 PDF 文字层并切成片段，保留来源、原文件 SHA-256、PDF 物理页码和字符偏移。本次 32 页生成 46 个证据片段。片段并非分别请求模型；本次将全部选定片段一起提供。

字符切片是定位与预算管理的基线，尚未理解跨页表格、图表或复杂版面。无文字层的扫描页会明确记录。

### generation.py：连接任务、证据、模型和结果

`generate()` 读取资料和提示词、检查预算、调用模型、保存并校验候选。`render()` 生成 Markdown，并将引用 ID 转成“来源编号·页码”的链接。`review_template()` 生成逐条核验表。

`load_system()` 从独立的 `prompts/system.md` 读取 system 内容；`--system-prompt` 可以指定其他文件。每次运行保存实际 system 内容及哈希，便于对比。

### providers.py：接口和密钥

`load_env()` 从 `.env` 读取 `DEEPSEEK_API_KEY`，已设置的环境变量优先。`deepseek()` 发出 system/user messages，要求 JSON 输出，并检查错误、空响应和截断。

模型名、地址、thinking、温度与预算放在 TOML。认证请求头不进入日志。HTTPS 校验保持开启；安装 certifi 时使用其可信 CA 库。

### validation.py：检查结构与引用

检查字段、非空内容、页面标识和引用 ID；`--single-page` 强制 pages 恰好为 1。引用存在不能证明主张被原文支持，目前不做自动语义评分。

### storage.py、cli.py 与入口

`storage.py` 统一 JSON、哈希和新输出目录，禁止覆盖旧实验。`cli.py` 提供 prepare、generate、validate、compare 四个命令。`__main__.py` 将模块运行方式转交给 CLI。

## 提示词与配置如何修改

| 文件 | 修改什么 |
| --- | --- |
| `prompts/system.md` | 公共行为、证据要求、主张类型、JSON 输出结构 |
| `prompts/single-wiki.md` | 腾讯研究主题、章节、篇幅和单篇要求 |
| `configs/tencent-demo.toml` | 模型、地址、预算、thinking、超时 |
| `.env` | API 密钥 |

数量要求既进入任务描述，也在返回后由程序检查。当前 25–40 条陈述的篇幅建议仅写在 Prompt 中，实际返回 62 条，尚未实施陈述数量硬约束。

## 本次运行与复现

```sh
python3 -m wiki_demo prepare --input ../../data --pattern '03*.pdf' --pattern '07*.pdf' --output artifacts/tencent-two-pdfs
python3 -m wiki_demo generate --corpus artifacts/tencent-two-pdfs --output artifacts/tencent-single-wiki --provider deepseek --config configs/tencent-demo.toml --system-prompt prompts/system.md --prompt prompts/single-wiki.md --single-page
python3 -m wiki_demo validate artifacts/tencent-single-wiki
```

这次已运行成功：一篇 Wiki、9 个模型生成章节、62 条陈述；耗时 68.301 秒，API 报告输入 23224 token、输出 5204 token。正文末尾的来源范围章节由程序补充。

再次运行请复用资料快照并更换生成输出目录，避免覆盖历史结果。先看正文组织，再点击重点结论的页码核对原文，必要时回到 PDF 看版面。`status=generated` 只表示流程成功，不等于逐条结论已核实。

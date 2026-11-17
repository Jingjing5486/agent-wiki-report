# llmwiki-compiler 批量 Wiki 生成实验记录

**运行日期：**2026-11-12  
**状态：**完整两阶段编译已完成；生成 274 个待审核候选页面及本地 Markdown 预览，尚未批准到正式 Wiki。  
**实验目的：**观察“批量抽取 → 跨来源合并 → 生成有来源引用和链接的 Markdown 页面”能否用于本项目的 10 篇 PDF，并了解真实运行时的数据契约、成本和失败点。

## 1. 结论先读

本轮挑选 [atomicstrata/llm-wiki-compiler](https://github.com/atomicstrata/llm-wiki-compiler)。其公开实现对应“先抽取、再写页”：逐份来源抽出 concept/entity/topic，整批抽取后聚合相同概念，再生成 typed Markdown 页面、引用和 `[[wikilinks]]`。10 份资料的 33 个 Markdown 输入段已全部处理，最新一轮成功完成页面编译，产出 274 个 review candidate。它们是模型生成的待审核知识草稿，不是已经验收或发布的正式 Wiki。

## 2. 上游代码来源与复现状态

| 项目 | 记录 |
| --- | --- |
| GitHub | [atomicstrata/llm-wiki-compiler](https://github.com/atomicstrata/llm-wiki-compiler) |
| 本地副本 | `references/llm-wiki-compiler/`，保留 Git 历史；没有修改上游源码 |
| 本地 HEAD | `5dc9102f5c594d40c7e293e5005a93c42790c556`（2026-11-10） |
| 上游版本 | 1.5.0 |
| 许可证 | MIT，见副本内 `LICENSE` |
| 技术栈 | TypeScript / Node.js；上游要求 Node.js ≥24 |
| 本机运行时 | Node 24.22.3 临时运行时；依赖按锁文件安装，`npm ci --ignore-scripts`，随后本地构建 CLI |
| 依赖审计提示 | 安装时 npm audit 报告 29 项 advisory（2 low、12 moderate、12 high、3 critical）；本次未运行自动修复，也没有把上游依赖发布到生产环境 |

代码仓库是完整上游副本，不是把几个文件复制进 `apps/wiki-demo`。本项目新增的转换器和领域指令与上游分开放置。

## 3. 上游生成流程：概念抽取与页面合并分开

```text
PDF 逐页文本
  → 带页码边界的 Markdown source files
  → Phase 1：每份来源调用 LLM，抽取 concept/entity/topic
  → Phase 1 完成后汇总所有来源的抽取结果
  → Phase 2：按概念合并来源片段，生成一概念一页的 typed Markdown
  → 解析/修复 wikilinks，生成 index，校验引用与页面
  → --review 时输出为候选，等待人工批准
```

上游文档称为 two-phase compile。抽取时会把现有 Wiki index 提供给模型，辅助使用已有概念；代码按概念标题规范化后的 slug 合并来源，将同一 slug 的多份来源放到一个概念页的生成上下文中。近义标题如果没有归到同一个 slug，不会仅因语义相近自动合并。页面生成会收到贡献来源的文本片段并要求段落带来源文件和 Markdown 行范围。页面之后还可以由上游的 query、lint、eval、viewer 等功能继续使用。

这实现的是**概念驱动的主题页编译器**，不代表它必然会产出预先设计好的行业→公司→业务线三级目录。本次先按默认概念 schema 试跑，没有加本项目自定义的页面类型，也没有单独生成有明确职责的 taxonomy 层级。这是下一轮需要从 page map / profile 层再评估的设计点。

上游 compile 文档：[`docs/cli/compile.mdx`](../../../references/llm-wiki-compiler/docs/cli/compile.mdx)。

## 4. 本项目输入怎样接到上游

输入使用 [demo-v2](../../../data-prepared/demo-v2/README.md) 的 `pages.jsonl` 中 `search_text`，保留全部 10 份 PDF 的 885 个物理页。新增转换工具：[prepare_llmwiki_sources.py](../tools/prepare_llmwiki_sources.py)。

上游要求 source 是 Markdown 文件，引用定位是 Markdown 行范围；因此转换器把每个 PDF 页面写成独立的 `## PDF物理页码 N` 区段，并只在页边界将长文切成最多约 50,000 字符的一段。转换结果如下：

- 885 页完整映射到 33 份 Markdown source。
- 每个 source 有原文件名、source ID、原 PDF SHA-256 和物理页码范围。
- [line-to-pdf-page.jsonl](../artifacts/llmwiki-batch-demo-v1/line-to-pdf-page.jsonl) 保存 source 行号区间到原 PDF 物理页码的交叉表，后续可把模型生成的 `file.md:line-line` 引用还原成 PDF 页码。
- [source-manifest.json](../artifacts/llmwiki-batch-demo-v1/source-manifest.json) 记录拆分范围、字符数和页数。
- OCR、排版和表格的既有质量标记仍保留在每页 crosswalk 字段里；页面生成指令特别要求不把 OCR 不确定文本当作精确事实。

输入文件在 [llmwiki-batch-demo-v1/sources](../artifacts/llmwiki-batch-demo-v1/sources/)。该目录属于本地实验产物，受 `apps/wiki-demo/.gitignore` 的 `artifacts/` 规则忽略。

## 5. 本轮调用配置

- provider：上游 `openai` adapter（DeepSeek 提供 OpenAI Chat Completions 兼容接口）。
- base URL：`https://api.deepseek.com`。
- model：`deepseek-v4-pro`。
- `LLMWIKI_OPENAI_EXTRA_BODY={"thinking":{"type":"disabled"}}`：关闭 DeepSeek thinking。第一次请求没有此参数，DeepSeek 拒绝上游的 `tool_choice=required`，返回 400；查阅 DeepSeek tool-calling 文档后添加 thinking 开关，后续结构化抽取得以成功。
- `LLMWIKI_EMBEDDINGS=off`：禁用向量写入，隔离本轮“页面编译”的行为；这不影响 Markdown 页面、全文/链接索引的生成。
- `--lang zh-CN`，读取 [项目生成指令](../prompts/llmwiki-batch-instructions.md)。
- `--review`：只应产生待人工审核的候选，不写入已批准 Wiki。
- 来源分段约 50,000 字符；模型调用并发最终降至 1。

DeepSeek 文档确认 OpenAI Chat Completions 支持 V4 Pro 和 function tool calls：[模型更新](https://api-docs.deepseek.com/updates/)、[Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)。该组合是否能完成上游全部页面生成阶段，仍未验证。

### API key 处理

API key 只从现有本地忽略提交的 `.env` 读取，再映射到进程环境变量 `OPENAI_API_KEY`。它没有写进转换脚本、Prompt、模型配置、日志或报告；本次不会显示或回显密钥。实验目录里的 `.env` 是指向该本地忽略文件的符号链接，整个 artifacts 目录不入 Git。

## 6. 运行结果与实测限制

此前余额不足前的尝试均未进入页面编译。补充余额后的完整运行（execution ID `llmwiki-batch-demo-v1-continued`）记录 **307 次成功模型调用：7,889,122 input tokens、355,730 output tokens**。失败请求不计入；cache-read tokens 为 input 的子集。历史试跑的成功回执另计，不能和本次完整运行混作单次成本。

历史试跑回执如下：

| 尝试 | 设置 | 成功请求 | 输入 tokens | 输出 tokens | 结果 |
| --- | --- | ---: | ---: | ---: | --- |
| 1 | `--no-pages-limit`，并发 2 | 8 | 216,243 | 28,855 | 阿里年报段落出现 25–33 个过细概念/段，停止，未进入页面生成 |
| 2 | 默认概念数量，并发 2 | 14 | 326,058 | 13,253 | API 连接重置，重试失败，未进入页面生成 |
| 3 | 默认概念数量，并发 1 | 31 | 607,714 | 29,653 | 最后一段请求返回 402，未进入页面生成 |
| **历史试跑合计** |  | **53** | **1,150,015** | **71,761** | 未生成页面 |

表中是 usage receipts 记录的模型用量；输入 token 总数包含 353,664 个 cache-read tokens（是 input 的子集）。不是美元金额，也不覆盖失败请求。实际费用/余额以 DeepSeek 控制台为准。

最新运行后的 `llmwiki status --json` 显示：0 个 live pages、274 个 pending candidates、33 个待审核 source changes。使用 `--review` 的预期行为就是暂存候选，等待审核，不会自动发布。

全量预览复核发现，原转换器计算 crosswalk 行号时按 Python 列表元素计数，没有计入 PDF 页文本内部换行；大部分来源行号因此被映射到错误页码。这不是源文本本身的错位。已修正 [prepare_llmwiki_sources.py](../tools/prepare_llmwiki_sources.py)，重新生成 33 个 Markdown 文件与 crosswalk，并用候选记录内的 33 个 source hashes 确认输入正文逐文件完全未变；没有重新调用模型。修正后的引用渲染器也支持一个 citation marker 内的多个行范围、多个来源及链接锚点。

修正后初始盘点的2,819个来源目标中，10个引用了不存在的美团 Markdown 文件名。审核时已修正候选“以股份为基础的支付”中的11处文件名（10处 citation 和来源列表），并清除旧文件名产生的过时 broken-citation 诊断。6个高风险候选草稿已按原文修订，原件、修订内容与哈希清单保存在预览目录；重新运行无模型 evidence inventory 后，缺失来源诊断消失，剩余1个行号范围指向文件前置信息（1–7），无法映射具体 PDF 页。当前2,817个行号引用标记解析出2,819个目标，2,818个映射到 PDF 页。随后为29个候选添加35个唯一匹配的标题变体别名，预览中的 Wiki links 更新为1,602个解析、17个未解析和68个自链接，另有1条来源文件链接直达 Markdown source。只有4个页面的 sources 明确跨越多个输入 Markdown 文件。21组相似标题及未解析链接的逐项处置建议见[审核表](../artifacts/llmwiki-batch-demo-v1/preview/candidate-link-review.md)。详情见[引用/链接质量摘要](../artifacts/llmwiki-batch-demo-v1/preview/quality-summary.md)及[候选审核记录](../artifacts/llmwiki-batch-demo-v1/preview/candidate-review-notes.md)。

另做 274 页结构盘点：全是 `concept` 页面；263 个 provenance 标为 `extracted`、11 个标为 `merged`；标题无完全相同项，但自动相似度筛出 21 对值得人工比较的标题（不能自动视为重复）。国务院公报来源贡献了 20 个候选页，其中是否保留取决于项目范围定义。全量标题和来源映射在[候选盘点](../artifacts/llmwiki-batch-demo-v1/preview/candidate-inventory.md)。这些结果显示本轮主要生成了扁平概念页，没有形成用户最初设想的行业→公司→业务线导航树。

### 上游候选评估与人工抽查（2026-11-12）

按上游 Review 文档执行 `llmwiki eval --candidates --out json`，这是 deterministic evidence inventory，不调用 judge 模型。初始结果有274个候选、2,212个正文段落，2,010段被检测为有引用；2,323个 claim/evidence pairs 可供抽样，214项无法核验（202项无可用 citation、12项来源缺失/不可读）。修正6个草稿并重新盘点后，当前为2,339个可核验 claim/span、202个无可用 citation 的正文段落；12项来源缺失已消失。相较前次增加2项，是为表格引用补充表头范围后，上游按独立 citation span 拆分计数，并非新增事实主张。这是结构化盘点，不代表有引用的段落都事实正确。

随后执行文档示例 `llmwiki eval --candidates --suite full --sample 20 --out json`。报告保存在 `.llmwiki/eval/candidates-full.json`：抽样 20 项，19 项成功评分、1 项 judge error；19 项的评分为 0 分 1 项、1 分 3 项、2 分 15 项，样本均分 1.74/2。该均分不是页面准确率：上游按单个引用 span 形成 claim/evidence pair，抽样可能只给 judge 看一个范围，即使候选段落同一 marker 还有其他引用范围。

逐条复查低分样本后，阿里税务居民主张本身有原文支持：原文的“对非”在 Markdown 第312行，后接第314–315行的“中国居民的个人投资人”；候选引用只标了 314–315，遗漏决定主体含义的第312行，所以问题是 citation 范围不完整，不应误报为正文主体写反。阿里现金流“详细信息参见合并现金流量表”的候选 marker 同时引用了第120和145行，原文第145行明确写出该参照；judge 抽样只取了第120行标题，0 分反映单 span 评估限制。可转换票据页引用了表格第482和490行，但将数值归因于票据/限价看涨交易所需的列头在第467–472行，建议扩展引用。快手可灵页的商业化原因在同一来源第719–723行有证据，但候选只引用了第634–638行，建议补引该证据或收窄陈述。上述情况说明要结合全文、整组 citations 和 source span 再人工定性，不能照抄单条 judge verdict。

使用 `review show` 实际查看腾讯营销服务、腾讯游戏和分布式光伏候选。腾讯营销服务页体现了三来源合并和主题组织，但把 2024 全年收入写成“人民币121.4亿元（十亿计）”。回看来源第20页（PDF物理页码）可见图表标注“Revenue in billion RMB”，数值为121.4，因此应是人民币121.4 billion，即1,214亿元；候选单位有明确错误，须在批准前修正。光伏页面本身引用连贯，但其是否纳入本项目 Wiki 要由资料库范围决定；它不是生成器可仅凭引用正确就自动判定的质量问题。

本轮 full eval 曾因沙箱网络限制出现连接失败，停止沙箱内重试后经授权网络完成。一个 judge 请求仍失败，报告保留 `judge-error`，CLI exit code 为 1；不应把它解释成评估全失败或 Wiki 不合格。

本轮还观察到，即使上游提示默认抽取 3–8 个主题，它也只是提示而非硬限制；部分 40–56 页年报段提出了 8–12 个概念。`--no-pages-limit` 更会导致显著膨胀。因此最终尝试采用默认设置，但 concept count 仍须由我们的 page-map 质量门禁进一步约束与评审。

## 7. 如何评估下一次成功生成

重跑前先确认 API 余额足够。随后在相同输入上跑完 `--review`，至少检查：

1. **跨来源合并：**腾讯 2025Q2 的公告、演示和卖方报告是否进入同一组相关页面；页面是否区分公司披露与分析师预测。
2. **页面有效性：**是否有清楚职责和可检索标题，是否避免年报一段一页、披露点过碎、同义重复和无用法律细项。
3. **引用可核验：**每个重要数字/主张的 source line citation 是否能经 crosswalk 找回正确 PDF 物理页。
4. **链接质量：**`[[wikilinks]]` 的目标是否存在、语义是否成立，是否只依靠共同出现而误连。
5. **覆盖：**现有 7 条示范检索问题中，各类资料、实体与报表期间是否均有相应页；不得把页面数作为唯一质量分数。
6. **安全发布：**候选先审核；通过事实/引用/链接检查后再批准到 live wiki。

页面级指标、查询集和与 FTS/RAG 基线的比较仍待生成成功后设计和运行；本次没有任何下游检索效果或响应时间结果。

## 8. 下一步建议

预览器、引用解析、PDF 页码 crosswalk 修复、全量结构盘点、6个有原文依据的草稿修订，以及21组相似标题的逐组判断已完成。对腾讯金融科技/业绩、美团业绩/分部、阿里股份回购/可转换票据等主题簇已比对候选摘要和来源，并记录页面合并与分层建议；阿里股份回购的表格引用已补入年份和单位表头。29个候选补入35条经核定别名后，仍有17个未解析 Wiki 链接，另有1条来源文件链接已直达 source Markdown；未强行匹配歧义项。下一步对照审核表继续逐页核对高频候选事实及 citations，再确定规范页、概览/子页和剩余链接的处理；同时将国务院公报的20页按项目范围分类。详细观察见[候选审核记录](../artifacts/llmwiki-batch-demo-v1/preview/candidate-review-notes.md)及[相似标题/链接审核表](../artifacts/llmwiki-batch-demo-v1/preview/candidate-link-review.md)。候选仍在 review queue，尚未批准到 live Wiki。

候选页初审完成后，建立一组可复现的 query gold set，用 Wiki 目录/FTS5 检索和现有 chunk 检索作同题对比，测路由召回、证据可回查性、回答准确度及延迟。当前还没有做这些检索实验，也没有验证 Wiki 相比 RAG 更快或更准。10,000 篇规模的断点续跑、成本和吞吐仍需单独压测，不能由这次 10 篇实验外推。

# SearchWiki 三层 Wiki POC

独立于 `../wiki-demo/` 的新实现。直接读取已有 `../../data-prepared/demo-v2/`，不重新OCR、不导入旧实验代码、不修改旧产物。Python 3.11+，核心只使用标准库。

目标是先跑通 **逐页JSON → 文档/章节摘要 → 跨文档主题 → 可回查来源**。参考 [SearchWiki §3.2](https://arxiv.org/html/2608.29953v1#S3.SS2)，实现论文描述的三种知识类型和文档单元联合编译；不是作者代码的逐行复现，不包含导航策略强化学习。

**已完成首次全量真实生成：10份文档、885页来源、137个章节、26个主题。** 17项测试及产物结构校验通过。直接打开 [生成的Wiki](artifacts/deepseek-all/wiki/index.md)，或查看 [实测记录](RUN-2026-11-15.md)。

## 快速运行

在本目录执行：

```sh
cd apps/searchwiki-poc

# 本地检查和分批估算，不调用API
python3 -m searchwiki inspect --config configs/default.toml

# 全部10份PDF的离线结构验证；主题是固定示例，正文是摘录
python3 -m searchwiki build --provider offline --output artifacts/offline-demo

# 在本项目.env中填写DEEPSEEK_API_KEY，或者设置同名环境变量
python3 -m searchwiki build --provider deepseek --env-file .env \
  --config configs/default.toml --output artifacts/deepseek-demo

# 失败后恢复：保持输入、选择范围、配置和Prompt一致
python3 -m searchwiki build --provider deepseek --env-file .env \
  --config configs/default.toml --output artifacts/deepseek-demo --resume

python3 -m searchwiki validate artifacts/deepseek-demo
python3 -m searchwiki read-page artifacts/deepseek-demo --source 07 --page 9
```

默认输入路径相对当前工作目录；换目录运行时应显式传入 `--input`。可重复添加 `--source 03 --source 07` 做小样例；不传则处理全部10份资料。输出目录必须新建，已有目录需要 `--resume`。构建过程中无需运行HTTP服务。

环境变量优先于 `.env`。凭据不会进入配置、请求记录或Wiki；`.env`被Git忽略。不会自动读取兄弟项目的密钥文件。如果系统CA有问题，可以安装可选依赖 `python3 -m pip install -e '.[api]'` 使用certifi；TLS校验始终开启。

## 文件职责

```text
searchwiki/
  cli.py           inspect / build / validate / read-page
  corpus.py        读取已有JSONL，检查文档身份及页覆盖
  planning.py      目录树、章节页范围、编译单元划分
  llm.py           DeepSeek接口、JSON修复、请求记录和缓存
  schema.py        模型输出结构及引用范围检查
  registry.py      全局主题ID、名称/别名归并、证据贡献
  pipeline.py      编排目录、联合编译、概览和主题综合
  render.py        Markdown目录、正文、链接与来源页锚点
  validation.py    实际产物的覆盖、引用、快照哈希和链接检查
  offline.py       明确标注的离线摘录模式
  files.py         原子写入、哈希和时间记录
  prompts/         system / toc / joint / overview / topic
configs/           无密钥的模型及分批配置
tests/             结构、引用、断点恢复与模拟API测试
artifacts/         本项目独立的实验输出，不提交Git
```

## 构建流程

1. **来源适配。** 使用 `search_text` 作为正文，逐页JSON保留原有OCR、块坐标、质量标记、来源哈希等字段。完整原文不静默截断、不因质量标记删页。
2. **目录规划。** 向模型提供书签、开头页片段、逐页标题线索，得到 `title/level/start_page`。目录线索有意压缩，全文在下一步读取。程序检查物理页码和层级，再计算结束页及父子关系。同一物理页开始的同级小节合并标题，避免在页级证据下制造空范围。
3. **联合编译。** 默认按最多48,000字符/32页划分单元。在一次调用里，按该单元实际覆盖的章节生成摘要，同时提出主题注册/复用动作和相应证据。每页分配给最深的适用章节，全部页恰好进入一个编译单元。
4. **主题注册。** 全局注册表随单元顺序推进，后续调用看到已有主题及别名。模型负责语义复用建议，代码处理规范名称/别名的精确归并和冲突检查；不能保证完全消除近义重复。POC使用顺序编译，便于共享状态和断点恢复。
5. **文档概览和主题页。** 汇总章节摘要形成文档概览；将联合编译时保留的主题证据综合成主题页，并要求保留所有贡献来源的引用。上下文过长时分组综合后再归并，不直接截掉后续来源。这些归并步骤是本项目的工程实现。
6. **渲染与检查。** 创建目录、章节、主题及来源页面。正文引用链接到对应来源物理页锚点，来源Markdown又链接到完整JSON快照。检查通过后运行状态才变为 `complete`。

“三层”是三种知识类型，不是恰好三个文件夹深度。章节可继续嵌套。单来源主题也允许存在，跨文档主题数量单独记录。

## 输出结构

```text
artifacts/<run>/
  run.json                    状态、输入/实现指纹、配置和累计token用量
  validation.json             自动检查结果
  requests/*.json             每次调用的Prompt、原始响应、usage和校验结果
  cache/*.json                已通过校验的响应，按请求内容寻址
  state/
    plans/<source_id>.json     章节树与单元页码清单
    compiled.json             文档/章节摘要及单元覆盖记录
    topics.json               全局主题注册表、来源贡献和综合正文
    source-hashes.json        逐文档JSON快照哈希
  wiki/
    index.md                  浏览入口
    log.md
    documents/
      index.md
      <source_id>/
        index.md
        overview.md
        sections/s001/
          index.md
          summary.md
          s002/...            如有子章节
    topics/
      index.md
      t-<hash>.md
    sources/
      index.md
      <source_id>.json         该文档的完整逐页记录列表
      <source_id>.md           可点击物理页锚点的原文浏览副本
```

来源ID沿用现有数据，章节ID和主题文件名由程序生成；不把模型输出当任意文件路径。摘要段落使用 `{text, citations:[{source_id,page}]}`，`page`从1开始。主题注册保留 `contributions` 以追溯到源文档和编译单元。

## API、费用和恢复

默认模型 `deepseek-v4-pro`，官方 `/chat/completions`、JSON输出、关闭thinking。请求预算是字符数限制，并非精确token计数；真实token在每次响应后累计。代码不硬编码费用，价格以实际使用的服务为准。

对该885页输入，默认分批为36次联合编译，另外至少20次目录/文档概览调用，以及按实际主题数量产生的综合调用。目录数量会影响摘要内容，但不改变按页划分的36个输入单元。JSON/引用结构错误最多自动修复1次；网络错误不自动重试，使用 `--resume`。修复响应的token同样计入用量。

成功响应先保存并通过校验，再进入缓存。恢复时按原输入顺序重建注册表，复用已通过校验且请求完全相同的结果。输入、Prompt、模型或分批配置变化需要新运行目录。异常会留下失败状态和已有响应；如果进程被强制终止，确认没有构建进程后才可删除残留 `.build.lock`。

模型偶尔会在主题动作里返回“本单元无相关信息”且引用为空的占位段落。程序不登记这些无证据贡献，并在缓存结果的 `normalization_notes` 留下记录；章节摘要缺引用、非空引用越界仍会失败，不会自动补造证据。

PDF同一页可能同时包含前文结尾和下一章开头。对单元内实际提供的相邻边界页，允许章节引用，并按真实引用扩展输出章节范围；原计划结束页保存在 `planned_end_page`，原因写入 `range_note`。这不改变输入页的唯一编译分配，也不允许引用未提供的页或任意跨章页。

API说明核查于2026-11-15：[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。当前只有CLI生成与来源读取，不包含自动问答代理、向量索引、增量更新或企业服务。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

自动检查覆盖：缺页/重复页、章节引用越界、主题漏掉贡献来源、别名冲突、无效JSON/截断响应、来源快照被修改、Markdown链接失效、失败后的恢复、输入改变后的恢复拒绝。

离线模式用于验证目录和文件链路，不能用于判断主题发现效果。结构检查通过也不代表模型陈述全部正确。当前POC接受现有数据中的OCR和表格误差，按用户决定先跑通，不进行精细重解析。

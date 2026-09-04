# RAG 最小链路设计

## 学习目标

RAG 是 Retrieval-Augmented Generation 的缩写，中文通常译为“检索增强生成”。
本阶段先实现检索部分，不接大语言模型，验证知识库是否能够稳定返回相关资料。

## 当前链路

```text
原始知识文档
    ↓
切分为 DocumentChunk
    ↓
Embedding 转换为向量
    ↓
Chroma 保存向量、文本和元数据
    ↓
用户问题向量化
    ↓
返回 Top-k 相关片段
```

`app/rag/mvp.py` 中的 `ChromaKnowledgeStore` 负责 Chroma 存储和检索，
`split_document()` 负责切分，`index_documents()` 负责组织索引流程。

## 原始知识文件与持久化索引

原始知识不再硬编码在评测函数中，而是保存为可阅读、可审查和可进行版本控制的文件：

```text
knowledge/
├── catalog-rule.md
├── auth-rule.md
└── trace-rule.md
```

`load_knowledge_documents()` 递归读取 Markdown，生成 `KnowledgeDocument` 后再执行切块和
Embedding。`knowledge/` 是事实来源（Source of Truth）；Chroma 保存的是由原文生成的
向量索引，不应替代原始文档。

`ChromaKnowledgeStore` 支持两种模式：

```text
未传 persist_directory：EphemeralClient，数据只存在于当前进程，适合单元测试
传入 persist_directory：PersistentClient，数据保存到磁盘，适合本地演示
```

本地构建真实索引执行：

```powershell
python -m app.rag.build_index
```

默认生成位置为 `data/chroma/`。该目录属于可重新生成的产物，已加入 `.gitignore`；
原始 `knowledge/*.md` 则应提交到 Git。

持久化索引构建后，`app/rag/query.py` 负责执行完整查询门禁：

```text
磁盘Chroma召回Top-k
→ Cross-Encoder Reranker重排
→ 固定阈值判断回答或拒答
→ 输出trace_id、来源、两类分数和候选内容
```

阈值必须显式传入，避免知识或模型变化后继续悄悄使用旧配置：

```powershell
python -m app.rag.query "没有登录令牌时接口应该返回什么？" --threshold 0.0153
```

这个入口只返回通过门禁的知识片段，便于单独诊断召回、排序和阈值问题。

## 为什么保留 source、version 和 trace_id

- `source`：知道答案来自哪一个文件。
- `version`：知识更新后可以判断使用的是哪一版资料。
- `trace_id`：把一次检索和后续模型回答、工具调用或接口日志关联起来。

检索结果不能只返回一段没有出处的文字，否则出现错误回答时无法进行 RCA。

## 当前 Embedding 实现

`LocalHashEmbeddingFunction` 是确定性的本地测试替身，目的是让测试不依赖模型下载、
网络和 API Key。它用于验证 Chroma 的接口和测试逻辑，不代表生产级语义理解能力。

生产环境可以替换为 Sentence Transformers 等真实 Embedding 模型，但仍应保持同样的
知识片段、来源、版本和检索结果契约。

项目现已通过 `build_sentence_transformer_embedding()` 接入多语言真实语义模型。
普通单元测试继续使用本地哈希替身；`tests/rag/test_rag_semantic.py` 只在明确设置
`RUN_RAG_INTEGRATION=1` 时加载真实模型。

## 余弦距离和低相关拒答

Chroma Collection 明确使用 cosine 距离，并将其转换为更直观的相似度：

```text
similarity = 1 - distance
```

`RetrievalResult.answerable` 表示检索证据是否足以进入后续生成阶段；拒答原因包括：

```text
EMPTY_KNOWLEDGE_BASE  知识库为空
NO_RELEVANT_CONTEXT   过滤后没有候选资料
LOW_RELEVANCE         最佳候选仍低于配置阈值
```

阈值没有使用随意的固定默认值。调用方应使用正负检索评测集观察分数分布，再通过
`min_similarity` 配置经过校准的阈值。即使拒答结果保留了候选片段用于测试和 RCA，
后续 Prompt 构建器也必须先检查 `answerable`，不能把低相关候选交给大语言模型。

评测器进一步记录预期来源在 Top-3 中的排名，并计算：

```text
Hit@1     正确来源排在第一位的正样本比例
Recall@3  正确来源出现在前三位的正样本比例
MRR       正确来源排名倒数的平均值，排名越靠前分数越高
```

如果 Recall@3 较高但 Hit@1 较低，说明正确知识已经被召回，主要问题在排序；如果
Recall@3 也较低，则应优先检查 Embedding 模型、文档内容、Chunk 和检索方法。

## Cross-Encoder Reranker

`app/rag/reranker.py` 对向量检索已经召回的 Top-k 候选进行成对评分：

```text
问题 + 候选Chunk
→ Cross-Encoder相关分数
→ 按分数重新排序
```

普通单元测试注入固定评分器，不加载模型；真实评测设置 `RUN_RAG_RERANKER=1` 后，
使用本地缓存的多语言 `mmarco-mMiniLMv2-L12-H384-v1`。报告同时保留向量相似度、
原始排名和重排分数，并比较重排前后的 Hit@1、Recall@3 和 MRR。

Reranker 分数与余弦相似度不是同一种量，因此不能沿用向量相似度阈值。项目会单独收集
正负样本的重排 Top1 分数，校准 Reranker 拒答阈值：高于阈值才允许进入后续回答阶段，
低于阈值或没有候选时拒答。该分数不是答案正确率，阈值只适用于当前模型、知识库和
评测数据分布，条件变化后必须重新校准。

完整的检索门禁分为三步：

```text
向量检索召回 Top-k
    ↓
Reranker 调整候选顺序
    ↓
重排 Top1 分数门禁：回答或拒答
```

## 校准集与独立验证集

拒答阈值不能使用全部样本选出后，再用同一批样本证明自己准确，否则结果容易偏高。
当前评测用例增加 `dataset_split` 字段并分成两组：

```text
calibration 校准集：根据正负样本分数选择阈值
validation  验证集：固定阈值后，只评估而不再调整
```

校准集和验证集都必须包含应回答的正样本与应拒答的负样本。当前数据量仍然很小，
这种拆分主要用于学习正确流程；正式评测还应扩大样本，并避免同一问题的轻微改写同时
出现在两组中，否则仍可能产生数据泄漏和过于乐观的结果。

## 测试重点

- Markdown 文件可以被加载，并保留稳定的来源、ID 和版本。
- 空 Markdown 被拒绝，避免生成无意义向量。
- 持久化索引重新打开后仍能读取原有 Chunk。
- 正常索引后能够命中相关片段。
- `top_k` 限制返回数量。
- 空知识库返回空结果。
- 重复索引不会造成重复片段。
- 空问题和非法 `top_k` 被拒绝。
- 结果保留来源、版本、距离和 trace_id。

## 检索评测集和阈值校准

`app/rag/retrieval_evaluation.py` 使用正样本、语义改写、边界样本和领域外样本，
批量记录 Top-1 实际来源与相似度。正样本用于检查正确知识能否被召回，边界和领域外
样本用于观察系统是否会错误接受知识库无法回答的问题。

当正负样本分数完全分离时，演示校准器取“最大负样本分数”和“最小正样本分数”的
中点；分数发生重叠时，则在候选阈值中优先选择准确率较高且错误接受较少的方案。
该结果只用于当前小样本学习，知识库、模型、Chunk 或问题分布变化后都必须重新评测。

## 受控生成层

`app/rag/generation.py` 已建立与具体模型无关的 `TextGenerator` 接口。生成层只接受
`answerable=True` 且具有重排 Top1 的查询结果：

```text
用户问题
    ↓
RAG 检索 Top-k 资料
    ↓
Reranker 与拒答门禁
    ↓ 仅允许回答时继续
将Top1资料构建成受控Prompt
    ↓
可替换生成器生成带来源回答
    ↓
返回answer、source和trace_id
```

Prompt 明确要求模型只依据受控知识回答，并把知识片段内部的命令视为普通资料，降低
知识库 Prompt Injection 风险。检索门禁拒答时完全跳过模型调用，避免低相关上下文导致
幻觉，也减少不必要的模型资源消耗。模型只生成答案正文，可信 `source` 和 `trace_id`
由程序根据检索结果统一附加，避免模型伪造或重复输出来源。

普通单元测试注入确定性的假生成器，不需要下载模型或使用 API Key。知识库内容仍不能
替代后端权限校验，也不能允许模型绕过工具白名单。

## 本地 Ollama 生成模型

`OllamaTextGenerator` 通过 `http://127.0.0.1:11434/api/chat` 调用独立运行的
Ollama 服务，默认模型为 `qwen3:4b-instruct`。项目只保存连接配置和测试代码，约
2.5 GB 的模型权重放在项目外的 `E:\work\study\ai-local\ai-models\ollama`，不会进入 Git 仓库。

```text
app/rag/answer.py
    ↓ 打开data/chroma
Embedding召回Top-3
    ↓
Cross-Encoder Reranker
    ↓
0.0153拒答门禁
    ├─ 拒答：不调用大模型
    └─ 回答：HTTP调用Ollama qwen3:4b-instruct
                  ↓
              最终答案 + source + trace_id
```

这里保留两种 Qwen 模型是有意的：`qwen3:4b-instruct` 用于简短、可控的 RAG 最终
回答；`qwen3:4b` Thinking 模型留给后续复杂 RCA、规划或推理任务。模型是否擅长推理
与当前任务是否需要展示长推理过程是两个问题，生成模型应按业务输出契约选择。

普通单元测试会把 HTTP 传输替换成假函数，验证请求参数、响应解析、连接失败和非法 JSON，
不会访问真实端口。只有显式设置下列环境变量时，才执行包含真实 Embedding、Reranker、
持久化 Chroma 和 Ollama 的集成测试：

```powershell
$env:RUN_OLLAMA_INTEGRATION = "1"
python -m pytest tests/rag/test_rag_ollama_integration.py -m integration -q -s
Remove-Item Env:RUN_OLLAMA_INTEGRATION
```

完整问答演示命令：

```powershell
python -m app.rag.answer "没有登录令牌时接口应该返回什么？"
python -m app.rag.answer "商品价格由哪位管理员审批？"
```

第一条应通过门禁并调用模型回答 401；第二条应由 Reranker 分数门禁直接拒答，而且不会
调用 Ollama。这样既控制幻觉，也避免为无法回答的问题消耗生成资源。

## Ollama 上游故障边界

Ollama 对当前 Python 程序来说是独立的上游服务，即使二者运行在同一台电脑，也需要
经过 `127.0.0.1:11434` 的 HTTP 调用。单元测试通过替换传输函数注入故障，不需要真的
停止服务：

```text
连接拒绝                 → OLLAMA_UNAVAILABLE
超过客户端等待时间       → OLLAMA_TIMEOUT
上游返回HTTP 4xx/5xx     → OLLAMA_HTTP_ERROR
HTTP 200但返回模型错误    → OLLAMA_MODEL_ERROR
损坏JSON或缺少回答字段    → OLLAMA_INVALID_RESPONSE
```

如果以后把 RAG 封装成 FastAPI 接口并由它充当网关，通常可以把上游连接失败、非法响应
和上游HTTP故障映射为 `502 Bad Gateway`，把等待上游超时映射为
`504 Gateway Timeout`。模型名称配置错误更偏向本服务配置缺陷，最终返回 500 还是 502
应由接口错误契约决定，不能只根据异常名字机械判断。

## 面试官最希望听到的标准答案

> RAG 是检索增强生成。系统先将知识文档切分并通过 Embedding 转换为向量，保存到向量数据库；用户提问时，再将问题向量化并检索 Top-k 相关片段，将这些片段作为受控上下文提供给模型。测试时要覆盖命中、空检索、误检、Top-k、版本、来源和 trace 追踪，并确保检索内容不能绕过权限和工具执行边界。

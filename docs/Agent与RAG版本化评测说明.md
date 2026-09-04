# Agent与RAG版本化行为评测

## 这一步新增什么

已有代码证明了各模块能运行，本阶段把任务固定成可重复执行的评测集，并统一统计结果。
不是新增15个业务功能，也不是替代原来的模块测试。

| 文件 | 职责 |
| --- | --- |
| `eval_cases/agent_rag_v1.json` | 15条用户任务、权限、预期工具/参数/结果，以及人工复核标记 |
| `app/agent/evaluation_fixtures.py` | 固定商品、候选片段、分数和故障注入；每条case重新构建环境 |
| `app/agent/evaluation.py` | 校验用例文件、复用已有Harness执行、计算指标、生成报告 |
| `tests/agent/test_agent_evaluation.py` | 检查15条行为case，以及评测器的断言、指标、报告和退出码 |

`app/agent/mvp.py` 的 `evaluate_case()` 新增结果内容检查和端到端计时。
真实商品数据库改为调用商品工具时才导入；独立评测脚本不再因导入Agent就初始化开发库。
pytest仍沿用项目 `conftest.py` 的隔离测试库准备流程。

## 执行链路和证据边界

```text
版本化case中的用户文字与权限
→ 规则Planner（可显式换成现有Qwen Planner）
→ 原始输出解析 / ToolCall
→ 真实ToolRegistry：白名单、权限、参数Schema
→ 固定商品处理器，或“固定候选与分数 + 真实重排排序和拒答门禁”
→ 对照独立的expected字段逐项断言
→ 聚合结果与报告
```

注意：依赖工厂只接收 `fixture` 名称，不读取 `expected`，避免“按预期答案构造结果”。
改变期望ID不会改变实际查询出的商品。商品与知识快照冻结在fixture文件中，不读取开发库、
`knowledge/` 的当前文档或 `data/chroma` 的当前索引。

本阶段不加载真实Embedding/Reranker，不调用最终答案生成器，也不评测召回率或答案事实正确率。
现有真实模型集成测试继续单独保留。知识问题允许模型改写 `query`，因此不要求与原文逐字相同；
当前程序检查Schema和非空查询，改写是否保持原意还需要人工复核。

## 15条case覆盖范围

- 正常：单个商品、名称与价格条件、鉴权知识、日志知识。
- 边界：价格恰好等于上限、商品不存在、模型输出错误参数类型。
- 检索与拒答：空知识库、低重排分数，并检查拒答不携带候选内容与来源。
- 工具错误：依赖抛异常、依赖返回受控TIMEOUT错误。
- 权限：没有商品权限、有商品权限但没有知识权限；都检查处理函数未被调用。
- 恶意输出：模拟提示注入后编造删除工具、伪造权限字段，验证后端安全边界。

恶意输出case不证明真实模型能抵御提示注入，更不代表恶意文档测试已完成。
TIMEOUT case只是故障结果注入，不证明真实等待时长或线程已被终止。
三个原始输出注入case单独标为 `injected_output`，不会冒充真实Qwen测试。

## 三项指标如何解释

1. **任务通过率** = 所有断言通过的case数 / 总case数。
   预期是拒绝越权，实际也正确拒绝，任务就通过。
2. **工具调用成功率** = `ok=True` 的调用数 / 已进入Registry的调用数。
   分母包含被Registry拒绝的未知工具、坏参数和权限不足；解析/Planner失败未进入Registry，不计入分母。
   工具正常执行并安全拒答时仍是 `ok=True`；权限失败是 `ok=False`。
   没有任何调用时显示“不适用”，不显示100%。
3. **端到端耗时** = Planner开始到Registry返回的时间，汇总平均值和最近秩法P95。
   不包含依赖构建、文件读写、报告生成或最终答案生成。原有 `trace.duration_ms` 仍保留。

如果当前离线v1全部按预期运行，应得到任务 `15/15`，但工具成功只有 `7/14 = 50%`。
另1条伪造权限case在解析阶段就被拒绝。这个50%由负向样本组成决定，不能解释为生产系统可用率只有50%。
2026-08-31记录的两轮实际输出与上述数量一致；耗时及每轮5条复核结论见
[Agent与RAG评测人工复核记录](Agent与RAG评测人工复核记录_20260831.md)。后续运行仍以当次报告为准。

## 先运行离线回归

在项目根目录的 `learning_zero` 环境执行，不需要启动Ollama或Uvicorn：

```powershell
python -m pytest tests/agent/test_agent_mvp.py tests/agent/test_agent_rag.py tests/agent/test_agent_ollama.py tests/agent/test_agent_evaluation.py -q -s
python -m app.agent.evaluation
```

pytest不仅运行15条case，还测试评测器本身，所以pytest用例总数会大于15。
第二条命令才是生成本批15条任务评测报告的入口。

每轮保存到 `reports/agent/<评测集>-<版本>-<run_id>/`：

- `results.json`：逐条保存输入、权限、预期、实际工具/参数/结果、检查项、trace和耗时。
- `report.md`：总览、Planner分组指标、逐条失败检查项、5条未勾选的人工复核项。

两次运行的 `run_id` 和 `trace_id` 不同，不覆盖旧报告。`reports/` 已被Git忽略。
退出码：`0`表示断言全部通过；`1`表示存在失败case；`2`表示配置或报告写入错误。
自动断言通过仍需人工复核，并不表示所有阶段验收完成。

## 离线通过后，再选择真实Planner复测

需要时先另开窗口启动已有Ollama，然后在项目终端执行：

```powershell
$env:RUN_OLLAMA_INTEGRATION = "1"
python -m app.agent.evaluation --planner ollama
Remove-Item Env:RUN_OLLAMA_INTEGRATION
```

默认12条使用真实Qwen Planner，3条仍注入原始输出。工具和RAG依赖仍固定，因此这是
“真实模型规划 + 确定性依赖”的行为评测，不是完整真实RAG问答端到端测试。
报告按 `ollama` / `injected_output` 分组，不应把混合耗时当成模型平均延迟。
模型可能选错工具、改写参数或直接回答而不调用工具，这些应保留为失败记录，不能改期望凑全绿。

## 版本与人工复核

本批为 `suite_version=v1`、`fixture_version=offline-v1`；报告保存用例文件SHA256。
新增或调整输入、期望时保留基线，发布新的用例版本；改固定依赖时同步更新fixture版本并提交代码。
对比时同时核对用例版本、依赖代码、Planner类型和模型配置，不只比较百分比。

先复核报告选出的5条：商品查询、鉴权知识、低分拒答、知识权限拒绝、伪造权限。
查看JSON中的实际参数、数据/来源、错误和trace，把结论填写在Markdown清单；自动脚本不代替人工勾选。
JSON中的 `pending` 是生成报告当时的快照，后续人工结论以填写后的Markdown为准。
需要长期保留的复核证据，应选取并脱敏后整理到 `docs/`，不要直接提交整个报告目录。

这一阶段的验收是：离线回归结果可解释、15条报告可复核、至少5条完成真实人工复核。
真实Planner运行结果另行记录。本次尚未增加A/B比较、CI退化门禁、MCP或新的模型。

## 本阶段验收记录（2026-08-31）

用户已执行离线回归（82 passed），并完成离线和Qwen Planner两轮15条评测，任务均为15/15通过。
两轮各5条人工复核项已依据用户反馈及原始输出补录；JSON原始快照不修改。
可提交的精选证据见上述复核记录，完整报告留在被忽略的 `reports/agent/`。
这只表示当前固定依赖行为评测阶段完成，不代表真实RAG质量、MCP或CI指标门禁已经验收。

## 提示词版本对比：v0与v1

现在可用 `--prompt-version v0` 或 `--prompt-version v1` 选择Planner提示词。
v0保留原始说明；v1补充工具分工和查询改写约束，是待验证的候选方案，不保证优于v0。
不指定版本时仍使用v0；离线规则不读取提示词，因此拒绝离线v1运行。

先运行单元回归（不需要Ollama）：

```powershell
python -m pytest tests/agent/test_agent_mvp.py tests/agent/test_agent_rag.py tests/agent/test_agent_ollama.py tests/agent/test_agent_evaluation.py -q -s
```

启动Ollama后，保持模型、用例、工具契约和依赖行为一致，分别运行：

```powershell
$env:RUN_OLLAMA_INTEGRATION = "1"
python -m app.agent.evaluation --planner ollama --prompt-version v0
python -m app.agent.evaluation --planner ollama --prompt-version v1
Remove-Item Env:RUN_OLLAMA_INTEGRATION
```

每轮仍生成独立目录，不覆盖旧报告。JSON的 `prompt` 保存版本、原文和SHA256指纹；
逐条结果中的 `prompt_version` 对真实Planner有值，对3条注入输出为null。
Markdown报告展示版本和分组指标。对比 `ollama` 组的任务通过率、工具成功率和耗时，
不要把注入输出的成功当作提示词收益；知识query改写仍需人工复核。

本轮新增6条参数化展开后的单元测试，覆盖两版请求内容、非法版本、离线不适用标记，
以及两版从配置到报告的传递。使用假HTTP响应，不证明真实Qwen理解质量。
尚未新增自动A/B差异报告或CI门禁，真实运行结果待用户执行。

首次调用可能包含模型冷启动；单次先v0后v1的耗时不能直接证明v1更快。
性能比较应先预热，再交换顺序重复运行；小样本全部通过只能说明未观察到退化。

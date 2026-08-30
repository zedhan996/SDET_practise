# Web 后端测开实战靶场

## 项目用途

这是一个基于 FastAPI、SQLAlchemy 和 SQLite 的接口自动化测试靶场，用于验证服务端接口实现及异常处理是否正确，并练习 pytest、Fixture、参数化、Mock、JWT/RBAC 鉴权、数据库断言和覆盖率报告。

当前测试覆盖登录、商品查询与增删改、权限校验、支付依赖故障注入、JWT 和数据库状态校验等场景。

## 环境要求

- Python 3.11.15
- 建议使用独立虚拟环境

## 安装依赖

```powershell
python -m pip install -r requirements.txt
```

## 配置环境变量

启动服务前，在当前 PowerShell 窗口设置开发环境配置：

```powershell
$env:APP_ENV = "development"
$env:APP_SECRET_KEY = "本地开发密钥，至少 32 个字符"
$env:APP_ADMIN_TOKEN = "本地开发管理员令牌"
$env:APP_DATABASE_URL = "sqlite:///./dev.db"
```

运行 pytest 时，`conftest.py` 会在导入 `main.py` 前自动设置测试环境，并使用独立的 `test_isolated.db`。测试不会操作开发数据库。

真实密钥、`.env` 文件和本地数据库不应提交到代码仓库。

## 两种登录链路

项目保留两套有明确用途的认证方式：

- `POST /login`：API 客户端使用，返回 JWT，后续通过 `Authorization: Bearer <token>` 传递。已有 API 鉴权测试继续使用这条链路。
- `POST /web/login`：浏览器前端使用，服务端通过 `HttpOnly`、`SameSite=Lax` Cookie 保存 JWT；`GET /web/me` 用于刷新页面后恢复登录态，`POST /web/logout` 用于清除 Cookie。

本地开发使用 HTTP 时 Cookie 的 `Secure` 属性关闭；生产环境应使用 HTTPS 并开启 `Secure`。Cookie 认证的修改类请求还需要结合 CSRF 防护。

## 启动服务

```powershell
python -m uvicorn main:app --reload --log-config logging.ini
```

命令中的 `main` 表示 `main.py`，`app` 表示该文件中的 FastAPI 应用对象，`--reload` 表示开发时检测到代码变化后自动重启服务。

启动后默认访问地址为 `http://127.0.0.1:8000`，交互式 API 文档地址为 `http://127.0.0.1:8000/docs`。

业务前端地址为 `http://127.0.0.1:8000/app/`。它是项目专门提供的中文商品目录工作台，登录后可以通过真实浏览器完成商品查询、新增、编辑和删除，用于后续 Playwright 端到端测试。

现有 pytest 用例通过 FastAPI `TestClient` 直接调用应用，因此运行这些测试前不需要启动 Uvicorn。真实 HTTP smoke、手工接口验证和网络测试才需要启动服务。

## 运行测试并生成报告

```powershell
python -m pytest --cov=main --cov-report=term-missing --cov-report=html:reports/coverage --html=reports/pytest/pytest-report.html --self-contained-html -q
```

报告位置：

- `reports/pytest/pytest-report.html`：pytest 测试执行报告
- `reports/coverage/index.html`：代码覆盖率报告首页
- `reports/playwright/`：Playwright 用例失败时保留的截图和 Trace

`pytest.ini` 默认配置为仅在 Playwright 用例失败时生成截图，并保留失败 Trace；测试成功时不会产生这些调试文件。

## 运行真实 HTTP Smoke 测试

先在一个 PowerShell 窗口启动服务：

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --log-config logging.ini
```

再在另一个已激活同一虚拟环境的 PowerShell 窗口运行：

```powershell
python smoke_http.py
```

Smoke 脚本通过真实本机 HTTP 连接验证根接口、登录发放 Token 和数据库查询。默认目标是 `http://127.0.0.1:8000`，也可以通过 `SMOKE_BASE_URL` 环境变量切换地址。

## 使用测试效能 CLI

`qa_tool.py` 是一个使用 Python 标准库实现的最小命令行工具，统一封装环境检查和 pytest 筛选。

```powershell
python qa_tool.py check
python qa_tool.py test --keyword login
python qa_tool.py test --last-failed
```

CLI 本身的测试位于 `test_qa_tool.py`，覆盖帮助信息、环境检查、测试筛选和非法参数退出码。

2026-08-19 在 Python 3.11.15 环境中的实测基线：

```text
35 tests collected
35 passed
98% statement coverage
```

## Agent/RAG版本化行为评测

固定15条任务，复用现有Planner、Registry和RAG门禁，统计任务通过率、工具调用成功率与端到端耗时：

```powershell
python -m pytest test_agent_evaluation.py -q -s
python agent_evaluation.py
```

默认采用规则Planner与固定测试依赖，不需要启动Ollama，不访问开发库或持久化知识库。
报告保存到 `reports/agent/`，人工复核项默认保持未完成。真实Planner模式和指标口径见
[Agent与RAG版本化评测说明](docs/Agent与RAG版本化评测说明.md)。

阶段证据：[2026-08-31两轮评测与人工复核记录](docs/Agent与RAG评测人工复核记录_20260831.md)。

## 项目结构

```text
.
|-- main.py           FastAPI 服务端、数据模型和接口实现
|-- test_api.py       pytest 接口自动化测试
|-- test_frontend.py  Playwright 真实浏览器 UI 测试
|-- smoke_http.py     需要真实服务的 HTTP Smoke 测试
|-- qa_tool.py        测试效能 CLI 工具
|-- test_qa_tool.py   CLI 工具自身的测试
|-- agent_mvp.py      Agent 工具调用 MVP 和商品查询工具
|-- test_agent_mvp.py Agent MVP 的契约、边界和 trace 测试
|-- agent_rag.py      把受控 RAG 检索注册为 Agent 工具
|-- agent_ollama.py   使用本机 Qwen 生成原生 Tool Calling 计划
|-- agent_evaluation.py 版本化Agent行为评测、三项指标与报告
|-- agent_evaluation_fixtures.py 隔离的固定数据与故障注入
|-- test_agent_evaluation.py 评测case及Harness自身的测试
|-- eval_cases/       版本化的Agent/RAG评测输入与预期结果
|-- rag_mvp.py        文档切分、Embedding、Chroma 写入和检索
|-- rag_query.py      Top-k、Reranker 和拒答门禁查询入口
|-- rag_generation.py Ollama 受控答案生成及上游错误映射
|-- rag_answer.py     本地持久化知识库完整问答入口
|-- logging.ini       Uvicorn 带时间戳的日志格式配置
|-- pytest.ini        pytest 与 Playwright 失败产物配置
|-- conftest.py       TestClient、数据库和鉴权等 Fixture
|-- requirements.txt  项目直接依赖及已验证版本
|-- frontend/
|   |-- index.html     业务前端页面结构
|   |-- styles.css     页面样式和响应式布局
|   `-- app.js         登录、请求、渲染和页面交互逻辑
|-- dev.db            开发环境 SQLite 数据库（运行服务后生成）
|-- test_isolated.db  测试环境 SQLite 数据库（运行 pytest 后生成）
`-- reports/          所有测试工具生成的报告和调试产物
    |-- agent/        Agent行为评测结果和待人工复核清单
    |-- ci/           CI 生成的 JUnit、覆盖率和 pytest 报告
    |-- coverage/     coverage.py 生成的 HTML 覆盖率报告
    |-- locust/       Locust 压测报告
    |-- playwright/   Playwright 失败截图和 Trace
    |-- pytest/       本地 pytest HTML 报告
    `-- selenium/     Selenium 失败截图
```

`reports/` 下的内容都是可重新生成的测试产物，不应手工修改，也不提交到 Git。

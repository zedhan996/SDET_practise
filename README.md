# Web 后端测开实战靶场

## 项目用途

这是一个基于 FastAPI、SQLAlchemy 和 SQLite 的接口自动化测试靶场，用于验证服务端接口实现及异常处理是否正确，并练习 pytest、Fixture、参数化、Mock、JWT/RBAC 鉴权、数据库断言和覆盖率报告。

当前测试覆盖登录、商品查询与增删改、权限校验、支付依赖故障注入、JWT 和数据库状态校验等场景。

## 环境要求

MCP 入门：见 [MCP最小工具调用说明](docs/MCP最小工具调用说明.md)。独立客户端已完成工具发现与商品101查询冒烟，不需要启动 Ollama 或 Uvicorn；新增三类失败测试待运行。

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
$env:APP_DATABASE_URL = "sqlite:///./data/app/dev.db"
```

运行 pytest 时，`conftest.py` 会在导入 `main.py` 前自动设置测试环境，并使用独立的 `data/tests/test_isolated.db`。测试不会操作开发数据库。

真实密钥、`.env` 文件和本地数据库不应提交到代码仓库。

## 本地数据库目录

2026-08-31将根目录的4个SQLite文件按用途迁移到 `data/`，保留原数据，不合并数据库：

| 位置 | 用途 |
| --- | --- |
| `data/app/dev.db` | 本地应用默认使用的商品数据库 |
| `data/tests/test_isolated.db` | pytest隔离库，测试前后重置商品数据 |
| `data/practice/sql_practice.db` | SQL学习库，与应用和自动化测试隔离 |
| `data/archive/test.db` | 当前源码未引用的旧库，保留待确认，不自动加载或删除 |
| `data/chroma/` | 原有RAG向量库，目录保持不变 |

应用的普通SQLite相对路径以项目根目录为基准，不受终端工作目录影响；需要的父目录会自动创建。
环境变量仍可指定自定义数据库。旧配置 `sqlite:///./dev.db` 和 `sqlite:///./test_isolated.db`
（以及对应的项目根目录绝对路径）会兼容到新位置，不会重新在根目录生成默认库。
其他自定义绝对路径、内存库及SQLite URI不会套用这两个旧文件名的映射。
这项兼容发生在Python应用内部；手工SQLite命令、数据库浏览器仍需选择新的文件位置。

`sql_practice_setup.py` 现在写入 `data/practice/sql_practice.db`，但运行它仍会清空并重建练习表。
迁移完成后不需要重新运行建库脚本。查看已有数据时优先用只读方式；索引实验另用临时副本。

迁移后的验证命令（由学习者执行，不需要启动Ollama或Uvicorn）：

```powershell
python -m pytest tests/api/test_api.py tests/api/test_observability.py tests/tools/test_qa_tool.py -q -s
```

数据库文件与SQLite配套日志都由 `.gitignore` 忽略，Git只提交程序、测试和说明。

### 名称索引计时实验

[索引计时脚本](sql_practice/index_timing.sql) 只操作已有副本 `data/practice/index_lab.db`，
不修改原练习库、应用库或接口代码。请从项目根目录执行：

```powershell
& "E:\ana\Library\bin\sqlite3.exe" -bail ":memory:" ".read sql_practice/index_timing.sql"
```

脚本打开实验副本后，增加十万条带 `INDEX-LAB-` 标记的数据，固定编号避免重复插入；
已有编号若内容不符合实验预期，则停止并回滚本次准备事务，不覆盖原记录。
实验数据会保留在副本中，不会在运行结束时自动清除，也不会提交到Git。
脚本要求副本已存在且有 `items` 表；不要为此重新运行会清表的建库脚本。

- A组：精确名称查询加 `NOT INDEXED`，禁用普通索引；本查询预期为表扫描。
- B组：同样的精确名称查询，不限制查询计划；预期选择 `idx_items_name`。
- 准备数据和建索引不计时；先检查计划并各预热一次，再交替测量五轮。
- 每条查询预期返回 `1100000|INDEX-LAB-100000|19.99`；先确认结果一致，再看耗时。
- `.timer` 的 `real` 单位是秒，显示零可能只是低于显示精度，不能算作真正零耗时。

这是同一副本上两种访问路径的暖缓存演示，不是生产压测，也没有证明包含搜索
`LIKE '%Keyboard%'` 获得提速，更不能直接作为HTTP接口的性能指标。

### 事务与回滚入门实验

[事务入门脚本](sql_practice/transaction_basics.sql) 继续使用同一个 `index_lab.db` 副本。
它在同一事务内临时插入两条带 `transaction-lab` 标记的数据，先在事务中查询到它们，
再执行 `ROLLBACK`，最后确认记录数重新为 `0`。因此脚本不会留下演示数据。

```powershell
& "E:\ana\Library\bin\sqlite3.exe" -bail ":memory:" ".read sql_practice/transaction_basics.sql"
```

这个小实验只说明显式回滚的基本行为；下一步才会模拟“第二个业务步骤失败”时应用应当回滚的场景。

随后可运行 [事务失败演示](sql_practice/transaction_failure_demo.py)：先在事务中创建订单，
再故意为不存在的商品创建订单明细。开启外键检查后第二步会触发 `IntegrityError`，
程序捕获异常并回滚，因此订单本身也不会留在数据库中。

```powershell
python sql_practice/transaction_failure_demo.py
```

预期最后显示 `事务后订单数： 0`。这才是服务端多步骤写入遇到错误时的基本保护方式。

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

## Docker Compose启动（学习环境）

当前最小容器只运行FastAPI、Uvicorn和SQLite，不包含浏览器测试、压测工具、RAG模型或Ollama。
Docker Desktop使用Linux容器，宿主机端口8001映射到容器端口8000。

首次运行前复制变量模板：

```powershell
Copy-Item .env.example .env
```

打开 `.env`，把占位值替换为仅用于本机的随机值。`.env` 已被Git忽略；
Dockerfile和compose.yaml不保存真实密钥。然后先运行本地契约测试和配置检查：

```powershell
python -m pytest tests/api/test_api.py -q -s
docker compose config
```

构建并后台启动：

```powershell
docker compose up --build -d
docker compose ps
docker compose logs api
```

`docker compose ps` 中api应最终显示healthy。访问：

```text
http://127.0.0.1:8001/health
http://127.0.0.1:8001/
http://127.0.0.1:8001/docs
http://127.0.0.1:8001/app/
```

停止并删除容器和Compose网络，但保留SQLite命名卷：

```powershell
docker compose down
```

容器内执行 API 测试（临时测试容器结束后自动删除）：

```powershell
docker compose --profile test run --build --rm api-tests
```

`api-tests` 使用 Dockerfile 的独立测试阶段，额外安装 pytest、httpx 和 pytest-mock；正式 `api` 镜像不包含这些测试依赖和测试代码。

GitHub Actions 在传统 pytest 与 Agent 离线门禁均通过后，还会自动构建正式镜像、检查镜像边界、执行容器测试、启动 API 并验证 `/health` 与 `/`。`main` 分支 push 通过全部门禁后，会将同一份已验收镜像发布到 `ghcr.io/zedhan996/sdet-practise-api`；当前不会自动部署真实服务器，具体见 [Docker持续交付门禁说明](docs/Docker持续交付门禁说明.md)。

只有明确需要清空容器数据库时才使用 `docker compose down --volumes`；这会删除本项目命名卷，
属于数据删除操作，不作为普通停止命令。宿主机原有 `data/app/dev.db` 不会复制进镜像，
也不会被容器修改；容器使用独立的 `app-data` 卷。

文件职责：

| 文件 | 作用 |
| --- | --- |
| `Dockerfile` | 定义运行镜像、依赖、普通用户和启动命令 |
| `compose.yaml` | 定义服务、端口、环境变量、数据卷和健康检查 |
| `.dockerignore` | 排除Git、报告、数据库、测试与模型代码，缩小构建上下文 |
| `requirements-runtime.txt` | 仅包含Web服务运行依赖 |
| `.env.example` | 可提交的变量名与占位模板，不含可用Secret |

`GET /health` 只表示应用进程可以处理HTTP请求，不检查第三方支付、真实模型或完整数据库业务，
因此它是当前最小存活/就绪探针，不是全系统健康证明。

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

Smoke 脚本先在有限次数内等待 `/health` 就绪，再通过真实 HTTP 连接验证根接口、登录发放 Token 和数据库查询。默认目标是 `http://127.0.0.1:8000`，既可以通过 `SMOKE_BASE_URL` 环境变量切换地址，也可以直接传递参数：

```powershell
python smoke_http.py --base-url http://127.0.0.1:8002 --health-attempts 10 --health-interval 1
```

全部通过返回退出码 `0`；健康检查耗尽或任一业务断言失败返回 `1`；非法重试参数返回 `2`。发布流水线可以根据退出码决定继续交付还是阻断。脚本自身的离线单元测试位于 `tests/smoke/test_smoke_http.py`，不会访问真实服务。

## 使用测试效能 CLI

`qa_tool.py` 是一个使用 Python 标准库实现的最小命令行工具，统一封装环境检查和 pytest 筛选。

```powershell
python qa_tool.py check
python qa_tool.py test --keyword login
python qa_tool.py test --last-failed
```

CLI 本身的测试位于 `tests/tools/test_qa_tool.py`，覆盖帮助信息、环境检查、测试筛选和非法参数退出码。

2026-08-19 在 Python 3.11.15 环境中的实测基线：

```text
35 tests collected
35 passed
98% statement coverage
```

## Agent/RAG版本化行为评测

固定15条任务，复用现有Planner、Registry和RAG门禁，统计任务通过率、工具调用成功率与端到端耗时：

```powershell
python -m pytest tests/agent/test_agent_evaluation.py -q -s
python -m app.agent.evaluation
```

默认采用规则Planner与固定测试依赖，不需要启动Ollama，不访问开发库或持久化知识库。
报告保存到 `reports/agent/`，人工复核项默认保持未完成。真实Planner模式和指标口径见
[Agent与RAG版本化评测说明](docs/Agent与RAG版本化评测说明.md)。

阶段证据：[2026-08-31两轮评测与人工复核记录](docs/Agent与RAG评测人工复核记录_20260831.md)。

## 项目结构

```text
.
|-- main.py           FastAPI 服务端、数据模型和接口实现
|-- tests/api/        API接口与可观测性测试
|-- tests/ui/         Playwright与Selenium真实浏览器UI测试
|-- smoke_http.py     需要真实服务的 HTTP Smoke 测试
|-- tests/smoke/      Smoke等待、业务调用和退出码的离线单元测试
|-- tests/mcp/        MCP参数、权限和超时边界测试
|-- qa_tool.py        测试效能 CLI 工具
|-- tests/tools/      CLI工具自身的测试
|-- app/agent/        Agent工具调用、RAG接入、Planner与评测源码
|-- tests/agent/      Agent工具、RAG接入、Planner和评测测试
|-- tests/rag/        RAG切片、检索、重排、生成和集成测试
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
|-- data/             按用途隔离的本地数据库，不提交到Git
|   |-- app/dev.db    本地应用库
|   |-- tests/test_isolated.db  自动化测试库
|   |-- practice/sql_practice.db  SQL练习库
|   |-- archive/test.db  用途待确认的旧库
|   `-- chroma/       RAG向量库，保持原位
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

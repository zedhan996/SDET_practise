# CI 自动化门禁说明

## 当前目标

CI（Continuous Integration，持续集成）用于在代码 push 或 Pull Request 时自动执行一组稳定测试。当前第一阶段只运行不依赖外部服务和浏览器的 pytest：

```text
test_api.py
test_observability.py
test_qa_tool.py
```

这组测试覆盖接口回归、request id、数据库断言、鉴权、异常映射和 CLI 工具自测。

## 流水线行为

工作流文件：

```text
.github/workflows/ci.yml
```

触发条件：

```text
push
Pull Request
手动运行
```

执行步骤：

```text
检出代码
→ 准备 Python 3.11
→ 安装 requirements.txt
→ 运行 pytest
→ 生成测试、JUnit 和覆盖率报告
→ 上传报告制品
```

pytest 返回非零退出码时，CI job 失败，这就是质量门禁。测试全部通过时，job 才能显示绿色。

## 报告产物

每次运行会尝试生成：

```text
reports/ci/pytest-report.html
reports/ci/junit.xml
reports/ci/coverage.xml
```

其中：

- `pytest-report.html`：测试用例执行结果；
- `junit.xml`：供 CI 平台读取的机器可读测试结果；
- `coverage.xml`：覆盖率工具使用的机器可读结果。

## 当前没有纳入基础门禁的测试

```text
test_frontend.py：需要 Playwright 浏览器
test_selenium.py：需要浏览器和 WebDriver
smoke_http.py：需要先启动 Uvicorn
locustfile.py：属于性能测试，不应作为每次提交的快速门禁
JMeter：属于独立性能测试计划
```

这些测试后续可以分别建立浏览器、真实 HTTP 和性能工作流，不应直接混入当前基础 pytest job。

## 本地复现 CI 命令

在项目目录和 Python 3.11 虚拟环境中执行：

```powershell
New-Item -ItemType Directory -Force reports\ci
python -m pytest `
  test_api.py `
  test_observability.py `
  test_qa_tool.py `
  --cov=main `
  --cov-report=term-missing `
  --cov-report=xml:reports/ci/coverage.xml `
  --junitxml=reports/ci/junit.xml `
  --html=reports/ci/pytest-report.html `
  --self-contained-html `
  -q
```

本地命令通过且不代表远程 CI 一定通过；CI 还会验证干净 Ubuntu 环境、依赖安装和工作流配置。

## 质量判断

```text
绿色：指定测试全部通过，报告已生成
红色：任一测试失败或依赖安装失败
```

CI 目前只证明基础回归门禁，不代表浏览器、真实网络和性能测试已经通过。

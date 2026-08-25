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

## 今日 Git 与 GitHub 实操

### 本地仓库与远程仓库

Git 仓库分为两个位置：

```text
本地仓库：项目目录下的 .git/
远程仓库：GitHub 上的 zedhan996/SDET_practise
```

本地仓库用于保存提交历史和版本；GitHub 远程仓库用于云端备份、协作和执行 GitHub Actions。创建 GitHub 仓库时，本项目已经有本地 `README.md` 和 `.gitignore`，因此远程仓库保持为空，避免产生重复的初始提交。

本项目选择公开仓库用于学习和交流，但公开仓库仍然不能提交密码、Token、`.env` 文件或其他敏感信息。

### 测试报告目录整理

原来的 `report/` 和 `htmlcov/` 用途重叠，已统一为：

```text
reports/
├─ ci/          CI 生成的测试和覆盖率报告
├─ coverage/    本地 HTML 覆盖率报告
├─ locust/      Locust 压测报告
├─ pytest/      本地 pytest HTML 报告
└─ selenium/    Selenium 失败截图
```

`reports/` 下的内容都属于可重新生成的测试产物，已由 `.gitignore` 统一忽略，不纳入 Git 提交。JMeter 的 `.jmx` 文件属于可复用的测试计划，继续保留在 `performance/jmeter/`。

### 提交说明的含义

执行提交时使用了：

```powershell
git commit -m "建立基础 CI 测试门禁"
```

其中 `-m` 后面的文字是本次提交的说明，由提交者自己填写。GitHub 文件列表显示的文字，是该文件最近一次被哪个提交修改时使用的提交说明，不是文件内容，也不是测试结果。

本项目当前有两个提交：

```text
7c7ce32 建立基础 CI 测试门禁
1f16b27 整理测试报告目录并统一生成路径
```

第一次提交包含项目的初始版本，所以许多文件显示“建立基础 CI 测试门禁”；第二次提交只修改了报告目录相关的 `.gitignore`、`README.md` 和 `pytest.ini`，因此这些文件显示新的提交说明。

### 分支与首次推送

本地仓库最初使用 `master` 分支，随后重命名为 `main`：

```powershell
git branch -M main
```

首次执行下面的命令时，GitHub 上创建了远程 `main` 分支，并建立本地与远程的跟踪关系：

```powershell
git push -u origin main
```

其中：

```text
main         本地分支
origin       GitHub 远程仓库的别名
origin/main  GitHub 上的远程分支
-u           设置后续默认跟踪关系
```

验证结果为：

```text
## main...origin/main
```

这表示本地 `main` 和远程 `origin/main` 已经同步。

### 今日完成结果

本地基础测试已通过：

```text
44 passed
95% coverage
```

代码已经推送到 GitHub，GitHub Actions 也在干净的 Ubuntu 环境中成功执行了基础 CI。顶部绿色对勾代表本次工作流通过。

因此目前可以准确表述为：

> 已将接口、可观测性和 CLI 自动化测试接入 GitHub Actions。代码每次 push 或 Pull Request 时，会在干净环境中自动安装依赖、执行基础 pytest、生成测试与覆盖率报告，并以测试结果作为质量门禁。当前 CI 已实际运行通过；浏览器、真实 HTTP 和性能测试仍未纳入这条基础流水线。

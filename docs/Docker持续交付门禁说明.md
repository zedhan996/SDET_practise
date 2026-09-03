# Docker 持续交付门禁说明

## 目标

现有 CI 在代码提交后自动执行传统 pytest 与 Agent 离线评测。本阶段在两个任务都通过后，增加 Docker 交付候选验收：

```text
pytest与Agent门禁通过
→ 构建正式runtime镜像
→ 检查镜像安全边界
→ 在test镜像内运行API测试
→ 启动API容器
→ 等待健康检查
→ 验证真实HTTP响应
```

当前没有推送镜像仓库，也没有部署到真实服务器，因此属于持续交付的基础门禁，不是完整的持续部署。

## Job 依赖关系

`docker-delivery` 配置：

```yaml
needs: [test, agent-offline]
```

只有 `test` 和 `agent-offline` 均成功，Docker Job 才会运行。这样可以避免为已知测试失败的提交浪费镜像构建时间。

## 自动验收内容

1. `docker compose config --quiet` 校验 Compose 配置。
2. 构建 `sdet-fastapi:local` 正式镜像。
3. 断言正式镜像中没有 `.env`、`test_api.py` 和 pytest。
4. 使用独立测试镜像运行 API 测试。
5. 启动正式 API 容器，最多等待 60 秒。
6. 校验 `/health` 与 `/` 的响应内容。
7. 失败时输出容器状态和日志，便于定位。
8. 无论成功失败都清理 GitHub Runner 中的一次性容器和数据卷。

## Secret 边界

流水线中的 `APP_SECRET_KEY` 和 `APP_ADMIN_TOKEN` 是一次性 CI 测试值，不是生产密钥。它们只在容器启动时通过环境变量注入，不参与 Dockerfile 构建，也不会被写入镜像。

未来接入真实部署时，应使用 GitHub Actions Secrets 或目标平台的密钥管理服务，不能在工作流文件中填写生产 Secret。

## 失败定位

常见失败阶段：

- Compose 校验失败：检查 YAML、环境变量占位符和服务配置。
- 镜像构建失败：检查 Dockerfile、依赖下载和构建上下文。
- 容器测试失败：查看 pytest 用例名称与断言。
- 健康检查超时：查看 API 启动日志、端口和 `/health`。
- HTTP 响应不符：检查部署产物是否包含预期代码版本。

失败时工作流自动执行：

```text
docker compose ps -a
docker compose logs --no-color api
```

这使 CI 页面保留容器状态与应用日志证据。

## 本地间歇性故障记录

本地 Docker Desktop/WSL2 曾在测试收集阶段返回退出码 139。`faulthandler` 显示进程在 FastAPI 导入期间、Pydantic 生成 OpenAPI Schema 时发生底层段错误；单独启动 Python、导入 pytest、`pydantic_core` 和 `greenlet` 均正常。

普通重启 Docker Desktop 后，连续 3 次 FastAPI 导入和连续 2 次完整容器测试均恢复成功，每轮 43 条用例通过。当前证据说明它是间歇性运行时或原生依赖兼容问题，而不是普通断言失败，尚不足以认定唯一根因。

流水线不通过自动重试掩盖此类故障：若 GitHub Runner 再次返回非零退出码，容器测试步骤应直接失败并阻断交付，同时保留状态与日志供定位。

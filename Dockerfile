# 固定Python小版本，降低不同构建时间产生的环境漂移。
FROM python:3.11.15-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖文件先复制，使业务代码变化时可以复用依赖安装缓存。
COPY requirements-runtime.txt ./
RUN python -m pip install --no-cache-dir -r requirements-runtime.txt

# 使用普通用户运行服务；预先赋予SQLite数据目录写权限。
RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid appgroup --create-home appuser \
    && mkdir -p /app/data/app \
    && chown -R appuser:appgroup /app

COPY --chown=appuser:appgroup main.py logging.ini ./
COPY --chown=appuser:appgroup frontend ./frontend


# 正式运行阶段不包含pytest和测试代码。
FROM base AS runtime

USER appuser

EXPOSE 8000

# 监听0.0.0.0，宿主机才能通过端口映射访问容器。
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-config", "logging.ini"]


# 测试阶段复用相同运行环境，仅额外加入测试依赖和API测试文件。
FROM base AS test

COPY requirements-container-test.txt ./
RUN python -m pip install --no-cache-dir -r requirements-container-test.txt

COPY --chown=appuser:appgroup conftest.py test_api.py smoke_http.py pytest-container.ini ./

USER appuser

CMD ["python", "-m", "pytest", "-c", "pytest-container.ini", "test_api.py", "-q", "-s"]

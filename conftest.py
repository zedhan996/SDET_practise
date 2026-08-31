import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
TEST_DATABASE_PATH = PROJECT_ROOT / "data" / "tests" / "test_isolated.db"

# 必须在导入 main 前设置测试环境，避免创建错误的数据库引擎。
os.environ["APP_ENV"] = "testing"
os.environ["APP_SECRET_KEY"] = "pytest-only-secret-key-at-least-32-chars"
os.environ["APP_ADMIN_TOKEN"] = "pytest-only-admin-token"
os.environ["TEST_DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"

import pytest
from fastapi.testclient import TestClient
from main import ADMIN_SECRET_TOKEN, ItemModel, SessionLocal, app, init_db

@pytest.fixture(scope="session")
def base_url():
    """浏览器和进程外 HTTP 测试使用的服务地址。"""
    url = "http://127.0.0.1:8000"
    return url

@pytest.fixture(scope="session")
def admin_headers():
    """返回兼容旧版 x-token 鉴权的管理员请求头。"""
    headers = {"x-token": ADMIN_SECRET_TOKEN}
    yield headers

@pytest.fixture
def client():
    """提供 FastAPI 进程内测试客户端。"""
    with TestClient(app) as test_client:
        yield test_client

@pytest.fixture
def admin_jwt_headers(client):
    """登录管理员账号并返回 Bearer JWT 请求头。"""
    res = client.post("/login", json={"username": "admin", "password": "Admin@123"})
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def user_jwt_headers(client):
    """登录普通用户账号，用于验证管理员接口的权限检查。"""
    res = client.post("/login", json={"username": "testuser", "password": "test1234"})
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def db_session():
    """提供数据库会话，用于校验接口操作后的持久化状态。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def reset_items():
    """将测试数据库恢复为 101、102、103 三条种子数据。"""
    db = SessionLocal()
    try:
        db.query(ItemModel).delete()
        db.commit()
        init_db()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def reset_database():
    """每个测试前后恢复种子数据，避免用例之间相互污染。"""
    try:
        reset_items()
        yield
    finally:
        reset_items()

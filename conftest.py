import os

# 必须在导入 main 前设置测试环境，避免创建错误的数据库引擎。
os.environ["APP_ENV"] = "testing"
os.environ["APP_SECRET_KEY"] = "pytest-only-secret-key-at-least-32-chars"
os.environ["APP_ADMIN_TOKEN"] = "pytest-only-admin-token"
os.environ["TEST_DATABASE_URL"] = "sqlite:///./test_isolated.db"

import pytest
from fastapi.testclient import TestClient
from main import ADMIN_SECRET_TOKEN, ItemModel, SessionLocal, app, init_db

# ==============================================================================
# Pytest 全局配置文件: conftest.py
# 深入大厂架构: 进程隔离、JWT Token 自动获取与物理数据库 DB Fixture 注入
# ==============================================================================

@pytest.fixture(scope="session")
def base_url():
    """基础地址 Fixture (适用于真实外部服务器测试)"""
    url = "http://127.0.0.1:8000"
    return url

@pytest.fixture(scope="session")
def admin_headers():
    """旧版管理员请求头鉴权 Fixture (兼容 x-token)"""
    headers = {"x-token": ADMIN_SECRET_TOKEN}
    yield headers

@pytest.fixture
def client():
    """FastAPI 进程内测试客户端 TestClient"""
    with TestClient(app) as test_client:
        yield test_client

@pytest.fixture
def admin_jwt_headers(client):
    """
    【大厂高级 JWT 鉴权 Fixture (管理员)】
    测试前自动调用 POST /login 获取真正的 JWT Token，组装成带有 Authorization: Bearer <token> 的请求头！
    """
    res = client.post("/login", json={"username": "admin", "password": "Admin@123"})
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def user_jwt_headers(client):
    """
    【大厂高级 JWT 鉴权 Fixture (普通用户)】
    用于越权测试：普通用户拥有 Token，但没有删除管理员权限 (预期 403 Forbidden)。
    """
    res = client.post("/login", json={"username": "testuser", "password": "test1234"})
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def db_session():
    """
    【物理数据库 DB 检查 Fixture】
    允许测试用例直接建立数据库连接，绕过 HTTP 接口去直接查询物理表 records，
    实现“HTTP 响应 200”+“物理 DB 数据一致”的【闭环双重断言】！
    """
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
    """
    【自动数据重置 Fixture】
    每个用例跑完后，自动把物理数据库充能重置回初始干净状态 (101, 102, 103)，
    彻底解决脏数据引发的测试污染问题！
    """
    try:
        reset_items()
        yield
    finally:
        reset_items()

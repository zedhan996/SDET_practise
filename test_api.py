import pytest
import requests
import jwt
from datetime import datetime, timedelta, timezone
from main import ItemModel, SECRET_KEY, ALGORITHM

# 基础接口

def test_root_endpoint(client):
    """测试根接口 GET /"""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Hello, SDET!"}

# 登录与会话

def test_login_success(client):
    """测试正确用户名密码登录成功"""
    payload = {"username": "admin", "password": "Admin@123"}
    response = client.post("/login", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "access_token" in response.json()

def test_web_login_cookie_session_lifecycle(client):
    """验证 Web 登录设置 HttpOnly Cookie，并支持查询与退出登录。"""
    response = client.post(
        "/web/login",
        json={"username": "admin", "password": "Admin@123"},
    )

    assert response.status_code == 200
    assert "access_token" not in response.json()
    set_cookie = response.headers["set-cookie"]
    assert "web_access_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie

    me_response = client.get("/web/me")
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "admin"

    logout_response = client.post("/web/logout")
    assert logout_response.status_code == 200
    assert client.get("/web/me").status_code == 401


def test_web_me_rejects_invalid_cookie(client):
    """验证伪造或损坏的 Web Cookie 不能恢复登录状态。"""
    client.cookies.set("web_access_token", "invalid-cookie-value")

    response = client.get("/web/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token credential."


@pytest.mark.parametrize(
    "username, password, expected_status, expected_detail",
    [
        ("admin", "WrongPwd123", 401, "Incorrect password"),
        ("non_exist_user", "Admin@123", 404, "User not found"),
        ("", "Admin@123", 400, "Username cannot be empty"),
    ]
)
def test_login_negative_cases(client, username, password, expected_status, expected_detail):
    """参数化验证登录异常场景。"""
    payload = {"username": username, "password": password}
    response = client.post("/login", json=payload)
    assert response.status_code == expected_status
    assert expected_detail in response.json()["detail"]

# 商品搜索

def test_search_items_by_keyword(client):
    """测试根据关键词搜索商品"""
    response = client.get("/items/search?keyword=phone")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["name"] == "iPhone 15"

def test_search_items_by_max_price(client):
    """测试根据最高价格筛选商品"""
    response = client.get("/items/search?max_price=6000")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2  # iPhone 15(5999) 和 AirPods Pro(1899)

def test_search_items_invalid_price(client):
    """测试异常最高价格 (<= 0) 触发 400"""
    response = client.get("/items/search?max_price=-10")
    assert response.status_code == 400
    assert "max_price must be greater than 0" in response.json()["detail"]

# 商品查询与更新

def test_get_item_by_id_success(client):
    """根据 ID 获取商品详情"""
    response = client.get("/items/101")
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "iPhone 15"

def test_get_item_by_id_not_found(client):
    """获取不存在的商品触发 404"""
    response = client.get("/items/999")
    assert response.status_code == 404

def test_update_item_success(client):
    """更新商品价格与名称"""
    update_payload = {"name": "iPhone 15 Pro", "price": 6999.0}
    response = client.put("/items/101", json=update_payload)
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "iPhone 15 Pro"
    assert response.json()["data"]["price"] == 6999.0

# 删除接口鉴权

def test_delete_item_without_token(client):
    """未携带 Header 鉴权 Token 触发 401"""
    response = client.delete("/items/103")
    assert response.status_code == 401

def test_delete_item_with_invalid_token(client):
    """携带错误 Token 触发 401"""
    response = client.delete("/items/103", headers={"Authorization": "Bearer invalid_token_123"})
    assert response.status_code == 401

def test_delete_item_success_with_admin_header(client, admin_headers):
    """携带旧版管理员 Header 成功删除商品"""
    response = client.delete("/items/103", headers=admin_headers)
    assert response.status_code == 204
    assert response.content == b""

# 第三方支付调用边界的 Mock 与故障注入

def test_pay_order_success_with_mock(client, mocker):
    """替换第三方支付调用并验证成功响应。"""
    mocker.patch(
        "main.call_third_party_payment", 
        return_value={"code": 200, "trade_no": "MOCK_PAY_TRADE_9999", "message": "Mock success"}
    )

    pay_payload = {"order_id": "ORD20260724_001", "amount": 5999.0}
    response = client.post("/orders/pay", json=pay_payload)

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["trade_no"] == "MOCK_PAY_TRADE_9999"

def test_pay_order_timeout_fault_injection(client, mocker):
    """注入第三方支付超时并验证接口返回 504。"""
    mocker.patch(
        "main.call_third_party_payment", 
        side_effect=requests.exceptions.Timeout("Mocked Third-party Connection Timeout")
    )

    pay_payload = {"order_id": "ORD20260724_002", "amount": 199.0}
    response = client.post("/orders/pay", json=pay_payload)

    assert response.status_code == 504
    assert "Third-party payment gateway timeout" in response.json()["detail"]


@pytest.mark.parametrize("invalid_amount", [0, -1.0])
def test_pay_order_rejects_non_positive_amount(client, mocker, invalid_amount):
    """金额类型正确但违反业务规则时返回 400，且不调用第三方支付。"""
    mock_payment = mocker.patch("main.call_third_party_payment")
    payload = {"order_id": "ORD_INVALID_AMOUNT", "amount": invalid_amount}

    response = client.post("/orders/pay", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "Payment amount must be greater than 0"
    mock_payment.assert_not_called()


@pytest.mark.parametrize(
    "invalid_payload, expected_field",
    [
        ({"order_id": "ORD_MISSING_AMOUNT"}, "amount"),
        ({"order_id": "ORD_NONE_AMOUNT", "amount": None}, "amount"),
        ({"order_id": "ORD_TEXT_AMOUNT", "amount": "abc"}, "amount"),
        ({"amount": 199.0}, "order_id"),
        ({"order_id": "", "amount": 199.0}, "order_id"),
        ({"order_id": "A" * 65, "amount": 199.0}, "order_id"),
    ],
)
def test_pay_order_request_validation(client, mocker, invalid_payload, expected_field):
    """请求缺少字段或字段无法通过模型校验时返回 422。"""
    mock_payment = mocker.patch("main.call_third_party_payment")

    response = client.post("/orders/pay", json=invalid_payload)
    body = response.json()

    assert response.status_code == 422
    assert body["detail"][0]["loc"][-1] == expected_field
    mock_payment.assert_not_called()


def test_pay_order_accepts_numeric_string_by_default(client, mocker):
    """记录当前宽松契约：数字字符串会被 Pydantic 转换为浮点数。"""
    mock_payment = mocker.patch(
        "main.call_third_party_payment",
        return_value={"code": 200, "trade_no": "MOCK_NUMERIC_STRING"},
    )
    payload = {"order_id": "ORD_NUMERIC_STRING", "amount": "199.0"}

    response = client.post("/orders/pay", json=payload)

    assert response.status_code == 200
    assert response.json()["trade_no"] == "MOCK_NUMERIC_STRING"
    mock_payment.assert_called_once_with("ORD_NUMERIC_STRING", 199.0)


def test_pay_order_service_error_fault_injection(client, mocker):
    """第三方支付抛出普通异常时，接口将其转换为 502。"""
    mock_payment = mocker.patch(
        "main.call_third_party_payment",
        side_effect=RuntimeError("payment service unavailable"),
    )
    payload = {"order_id": "ORD_SERVICE_ERROR", "amount": 199.0}

    response = client.post("/orders/pay", json=payload)

    assert response.status_code == 502
    assert "payment service unavailable" in response.json()["detail"]
    mock_payment.assert_called_once_with("ORD_SERVICE_ERROR", 199.0)

# 接口响应与数据库状态校验

def test_create_item_persists_to_database(client, db_session):
    """新增商品后同时校验 HTTP 响应和数据库记录。"""
    new_item_payload = {"id": 201, "name": "iPad Air 6", "price": 4799.0, "category": "tablet"}
    
    response = client.post("/items", json=new_item_payload)
    assert response.status_code == 201
    assert response.json()["data"]["name"] == "iPad Air 6"

    # 直接查询数据库，确认商品已持久化。
    db_row = db_session.query(ItemModel).filter(ItemModel.id == 201).first()
    assert db_row is not None, "数据库中未找到 ID=201 的记录"
    assert db_row.name == "iPad Air 6"
    assert db_row.price == 4799.0

def test_delete_item_removes_database_record(client, admin_headers, db_session):
    """删除商品后同时校验 HTTP 响应和数据库状态。"""
    response = client.delete("/items/103", headers=admin_headers)
    assert response.status_code == 204
    assert response.content == b""

    # 直接查询数据库，确认商品记录已删除。
    db_row = db_session.query(ItemModel).filter(ItemModel.id == 103).first()
    assert db_row is None, "数据库中仍存在 ID=103 的记录"

# JWT 签名与 RBAC 权限校验

def test_login_returns_bearer_access_token(client):
    """验证登录接口返回访问令牌和 Bearer 类型。"""
    payload = {"username": "admin", "password": "Admin@123"}
    response = client.post("/login", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert "access_token" in res_data
    assert res_data["token_type"] == "bearer"
    assert len(res_data["access_token"]) > 30

def test_delete_item_with_jwt_admin_success(client, admin_jwt_headers, db_session):
    """管理员携带有效 JWT 时可以删除商品。"""
    response = client.delete("/items/103", headers=admin_jwt_headers)
    assert response.status_code == 204
    assert response.content == b""

    # 确认数据库记录已删除。
    db_row = db_session.query(ItemModel).filter(ItemModel.id == 103).first()
    assert db_row is None

def test_delete_item_rejects_non_admin_role(client, user_jwt_headers):
    """普通用户持有效 JWT 调用管理员删除接口时返回 403。"""
    response = client.delete("/items/103", headers=user_jwt_headers)
    assert response.status_code == 403
    assert "Forbidden: Admin role required" in response.json()["detail"]

# 其他异常和边界场景

def test_create_duplicate_item_id_400(client):
    """创建重复商品 ID 时返回 400。"""
    duplicate_payload = {"id": 101, "name": "Duplicate Phone", "price": 5999.0, "category": "phone"}
    response = client.post("/items", json=duplicate_payload)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]

def test_update_non_existent_item_404(client):
    """修改不存在的商品时返回 404。"""
    response = client.put("/items/999", json={"name": "Ghost Item"})
    assert response.status_code == 404

def test_update_item_invalid_price_400(client):
    """将商品价格修改为非正数时返回 400。"""
    response = client.put("/items/101", json={"price": -50.0})
    assert response.status_code == 400
    assert "Updated price must be greater than 0" in response.json()["detail"]

def test_delete_non_existent_item_404(client, admin_jwt_headers):
    """删除不存在的商品时返回 404。"""
    response = client.delete("/items/999", headers=admin_jwt_headers)
    assert response.status_code == 404

def test_delete_item_expired_jwt_token_401(client):
    """使用已过期的 JWT 删除商品时返回 401。"""
    # 构造一个已过期 10 分钟的 JWT。
    expired_payload = {
        "sub": "admin", 
        "role": "admin", 
        "exp": datetime.now(timezone.utc) - timedelta(minutes=10)
    }
    expired_token = jwt.encode(expired_payload, SECRET_KEY, algorithm=ALGORITHM)
    
    response = client.delete("/items/103", headers={"Authorization": f"Bearer {expired_token}"})
    assert response.status_code == 401
    assert "Token has expired" in response.json()["detail"]

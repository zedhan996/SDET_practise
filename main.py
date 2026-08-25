from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field
from typing import Optional
import os
import requests
import jwt
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from fastapi.staticfiles import StaticFiles
import logging
import time
import uuid


logger = logging.getLogger("app.request")

# ==============================================================================
# JWT 加密与鉴权配置 (大厂 6.0 标准)
# ==============================================================================
# 满足 RFC 7518 规范：HS256 算法秘钥长度必须 >= 32 字节 (32 字符)
SECRET_KEY = os.getenv("APP_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("APP_SECRET_KEY environment variable is required")

ALGORITHM = "HS256"                       # HMAC-SHA256 对称加密算法
ACCESS_TOKEN_EXPIRE_MINUTES = 30           # Token 有效期 30 分钟
WEB_AUTH_COOKIE = "web_access_token"

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """生成带有过期时间的 JWT Token (使用 timezone.utc 代替已弃用的 utcnow)"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# ==============================================================================
# 5.0 SQLite 数据库与 ORM 级持久化配置 (SQLAlchemy)
# ==============================================================================
APP_ENV = os.getenv("APP_ENV", "development")
if APP_ENV == "testing":
    DATABASE_URL = os.getenv(
        "TEST_DATABASE_URL",
        "sqlite:///./test_isolated.db",
    )
else:
    DATABASE_URL = os.getenv(
        "APP_DATABASE_URL",
        "sqlite:///./dev.db",
    )

# 创建数据库引擎
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 定义数据库物理表 ORM 模型：items 表
class ItemModel(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    price = Column(Float)
    category = Column(String)

# 创建物理数据库表结构
Base.metadata.create_all(bind=engine)

# 依赖注入：每个请求自动获取与关闭 DB 会话
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 数据库种子数据初始化（若表为空，则注入初始数据 101, 102, 103）
def init_db():
    db = SessionLocal()
    try:
        if db.query(ItemModel).count() == 0:
            initial_items = [
                ItemModel(id=101, name="iPhone 15", price=5999.0, category="phone"),
                ItemModel(id=102, name="MacBook Pro", price=12999.0, category="computer"),
                ItemModel(id=103, name="AirPods Pro", price=1899.0, category="accessory"),
            ]
            db.add_all(initial_items)
            db.commit()
    finally:
        db.close()

init_db()

# ==============================================================================
# 应用系统定义
# ==============================================================================
app = FastAPI(title="用户与商城模拟系统", description="完整 CRUD、JWT Token 鉴权、Mock 故障注入与 SQLite ORM 双重断言靶场")


@app.middleware("http")
async def request_logging_middleware(request, call_next):
    """为每个请求记录可关联的 request id 和服务端处理耗时。"""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    started_at = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.exception(
            "request_id=%s method=%s path=%s status=500 duration_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            duration_ms,
        )
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

# 模拟的用户数据库 (用于登录校验与角色判断)
fake_db = {
    "admin": {"password": "Admin@123", "role": "admin"},
    "testuser": {"password": "test1234", "role": "user"}
}

# 静态管理员 Token 保留（兼容旧版 Header 测试）
ADMIN_SECRET_TOKEN = os.getenv("APP_ADMIN_TOKEN")

class LoginRequest(BaseModel):
    username: str
    password: str

class ItemCreate(BaseModel):
    id: int
    name: str
    price: float
    category: str

class ItemUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None

class PayRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=64)
    amount: float


def authenticate_credentials(request: LoginRequest):
    """校验用户名和密码，供 API 登录与 Web 登录共同使用。"""
    user = request.username
    pwd = request.password

    if user == "":
        raise HTTPException(status_code=400, detail="Username cannot be empty")

    if user not in fake_db:
        raise HTTPException(status_code=404, detail="User not found")

    user_info = fake_db[user]
    if user_info["password"] != pwd:
        raise HTTPException(status_code=401, detail="Incorrect password")

    return user, user_info


def decode_access_token(token: str) -> dict:
    """验证 JWT 签名和有效期，统一转换为项目的 401 错误。"""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token credential.")

# 模拟第三方支付 SDK/网关调用函数 (供 Pytest Mock 打桩使用)
def call_third_party_payment(order_id: str, amount: float):
    """模拟真实发起的外部网络 HTTP 请求"""
    return {
        "code": 200,
        "trade_no": f"WX_PAY_{order_id}_888",
        "message": "Paid successfully via third-party gateway"
    }

@app.get("/")
def read_root():
    return {"message": "Hello, SDET!"}

@app.post("/login")
def login(request: LoginRequest):
    """用户登录接口 (POST - 校验凭证并发放 JWT Token)"""
    user, user_info = authenticate_credentials(request)

    # 登录成功：颁发带有用户名和角色权限的真正 JWT Token
    access_token = create_access_token(data={"sub": user, "role": user_info["role"]})
    return {
        "status": "success", 
        "message": f"Welcome, {user}!", 
        "access_token": access_token,
        "token_type": "bearer"
    }


@app.post("/web/login")
def web_login(request: LoginRequest, response: Response):
    """浏览器登录接口：将 JWT 放入 HttpOnly Cookie，而不是交给前端脚本保存。"""
    user, user_info = authenticate_credentials(request)
    access_token = create_access_token(data={"sub": user, "role": user_info["role"]})

    response.set_cookie(
        key=WEB_AUTH_COOKIE,
        value=access_token,
        httponly=True,
        secure=APP_ENV == "production",
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    return {
        "status": "success",
        "message": f"Welcome, {user}!",
        "username": user,
    }


@app.get("/web/me")
def web_me(web_access_token: Optional[str] = Cookie(None)):
    """根据浏览器 Cookie 恢复当前登录用户，供页面刷新后检查登录态。"""
    if not web_access_token:
        raise HTTPException(status_code=401, detail="Web login required")

    payload = decode_access_token(web_access_token)
    return {
        "status": "success",
        "username": payload.get("sub"),
        "role": payload.get("role"),
    }


@app.post("/web/logout")
def web_logout(response: Response):
    """清除浏览器登录 Cookie。"""
    response.delete_cookie(key=WEB_AUTH_COOKIE, path="/")
    return {"status": "success", "message": "Logged out"}

# --- 静态查询接口 (查 ORM 数据库) ---
@app.get("/items/search")
def search_items(keyword: Optional[str] = None, max_price: Optional[float] = None, db: Session = Depends(get_db)):
    """搜索商品列表 (GET - 查数据库物理表)"""
    if max_price is not None and max_price <= 0:
        raise HTTPException(status_code=400, detail="max_price must be greater than 0")

    query = db.query(ItemModel)
    if keyword:
        query = query.filter(ItemModel.name.ilike(f"%{keyword}%"))
    if max_price:
        query = query.filter(ItemModel.price <= max_price)

    items = query.all()
    results = [{"id": item.id, "name": item.name, "price": item.price, "category": item.category} for item in items]
    return {"status": "success", "total": len(results), "data": results}

# --- 新增商品接口 (POST - 写入数据库物理表) ---
@app.post("/items", status_code=201)
def create_item(item_in: ItemCreate, db: Session = Depends(get_db)):
    """新增商品 (POST - 持久化写入 SQLite)"""
    existing_item = db.query(ItemModel).filter(ItemModel.id == item_in.id).first()
    if existing_item:
        raise HTTPException(status_code=400, detail=f"Item ID {item_in.id} already exists")

    new_item = ItemModel(id=item_in.id, name=item_in.name, price=item_in.price, category=item_in.category)
    db.add(new_item)
    db.commit()
    db.refresh(new_item)

    return {
        "status": "success",
        "message": "Item created successfully in physical DB",
        "data": {"id": new_item.id, "name": new_item.name, "price": new_item.price, "category": new_item.category}
    }

# --- 动态单体查询接口 (查 ORM 数据库) ---
@app.get("/items/{item_id}")
def get_item_by_id(item_id: int, db: Session = Depends(get_db)):
    """根据商品 ID 获取详情 (GET - 查数据库)"""
    db_item = db.query(ItemModel).filter(ItemModel.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    
    return {
        "status": "success",
        "data": {"id": db_item.id, "name": db_item.name, "price": db_item.price, "category": db_item.category}
    }

# --- 修改商品接口 (PUT - 更新数据库) ---
@app.put("/items/{item_id}")
def update_item(item_id: int, item_update: ItemUpdate, db: Session = Depends(get_db)):
    """修改商品信息 (PUT - 更新数据库)"""
    db_item = db.query(ItemModel).filter(ItemModel.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    
    if item_update.name is not None:
        db_item.name = item_update.name
    if item_update.price is not None:
        if item_update.price <= 0:
            raise HTTPException(status_code=400, detail="Updated price must be greater than 0")
        db_item.price = item_update.price

    db.commit()
    db.refresh(db_item)

    return {
        "status": "success",
        "message": "Item updated in DB successfully",
        "data": {"id": db_item.id, "name": db_item.name, "price": db_item.price, "category": db_item.category}
    }

# --- 删除商品接口 (DELETE - 从数据库删除 + JWT Token / Header 鉴权) ---
@app.delete("/items/{item_id}", status_code=204)
def delete_item(
    item_id: int, 
    authorization: Optional[str] = Header(None),
    x_token: Optional[str] = Header(None), 
    web_access_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    """管理员删除商品 (支持 JWT Bearer Token 校验与角色权限验证)"""
    user_role = None
    
    # 优先解析 JWT Bearer Token (标准大厂流程)
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        payload = decode_access_token(token)
        user_role = payload.get("role")
    elif web_access_token:
        payload = decode_access_token(web_access_token)
        user_role = payload.get("role")
    # 备选：旧版静态 x-token 兼容
    elif ADMIN_SECRET_TOKEN and x_token == ADMIN_SECRET_TOKEN:
        user_role = "admin"
    else:
        raise HTTPException(status_code=401, detail="Missing or invalid authentication token in request header")

    # 角色权限判断 (RBAC 越权控制)：非 admin 角色无权删除
    if user_role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden: Admin role required for deletion")
        
    db_item = db.query(ItemModel).filter(ItemModel.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
        
    db.delete(db_item)
    db.commit()
    return Response(status_code=204)

# --- 订单支付接口 ---
@app.post("/orders/pay")
def pay_order(pay_req: PayRequest):
    """订单支付接口"""
    if pay_req.amount <= 0:
        raise HTTPException(status_code=400, detail="Payment amount must be greater than 0")

    try:
        third_party_res = call_third_party_payment(pay_req.order_id, pay_req.amount)
        return {
            "status": "success",
            "message": "Order paid successfully",
            "order_id": pay_req.order_id,
            "trade_no": third_party_res.get("trade_no")
        }
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Third-party payment gateway timeout. Please retry later.")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Third-party payment service error: {str(e)}")

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field
from typing import Optional
import os
import requests
import jwt
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from fastapi.staticfiles import StaticFiles
import logging
import time
import uuid


logger = logging.getLogger("app.request")

# JWT 签名与鉴权配置。部署时应为 APP_SECRET_KEY 提供至少 32 字节的随机密钥。
SECRET_KEY = os.getenv("APP_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("APP_SECRET_KEY environment variable is required")

ALGORITHM = "HS256"  # HMAC-SHA256 签名算法
ACCESS_TOKEN_EXPIRE_MINUTES = 30
WEB_AUTH_COOKIE = "web_access_token"

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """生成包含过期时间的 JWT。"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# 数据库目录固定在项目内，不随启动终端的当前目录改变。
PROJECT_ROOT = Path(__file__).resolve().parent
APP_DATABASE_PATH = PROJECT_ROOT / "data" / "app" / "dev.db"
TEST_DATABASE_PATH = PROJECT_ROOT / "data" / "tests" / "test_isolated.db"


def resolve_database_url(value: str) -> URL:
    """统一普通SQLite文件路径，并兼容整理目录前的两个默认地址。"""
    url = make_url(value)
    # 内存库、SQLite URI及其他数据库的连接信息不按普通文件路径处理。
    if (
        url.get_backend_name() != "sqlite"
        or not url.database
        or url.database == ":memory:"
        or url.database.startswith("file:")
    ):
        return url

    database_path = Path(url.database)
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    database_path = database_path.resolve()
    legacy_paths = {
        PROJECT_ROOT / "dev.db": APP_DATABASE_PATH,
        PROJECT_ROOT / "test_isolated.db": TEST_DATABASE_PATH,
    }
    # 旧启动命令仍指向迁移后的原库，避免在根目录重新生成空库。
    database_path = legacy_paths.get(database_path, database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return url.set(database=str(database_path))


# 根据运行环境隔离开发数据库与测试数据库，仍允许环境变量覆盖。
APP_ENV = os.getenv("APP_ENV", "development")
if APP_ENV == "testing":
    DATABASE_URL = os.getenv(
        "TEST_DATABASE_URL",
        f"sqlite:///{TEST_DATABASE_PATH.as_posix()}",
    )
else:
    DATABASE_URL = os.getenv(
        "APP_DATABASE_URL",
        f"sqlite:///{APP_DATABASE_PATH.as_posix()}",
    )

DATABASE_URL = resolve_database_url(DATABASE_URL)
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ItemModel(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    price = Column(Float)
    category = Column(String)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 数据库为空时写入固定种子数据，供接口演示和测试使用。
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

app = FastAPI(title="用户与商城模拟系统", description="商品目录与支付接口测试服务")


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

# 演示环境使用的内存用户数据。
fake_db = {
    "admin": {"password": "Admin@123", "role": "admin"},
    "testuser": {"password": "test1234", "role": "user"}
}

# 保留静态管理员 Token，以兼容旧版 x-token 测试。
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

def call_third_party_payment(order_id: str, amount: float):
    """第三方支付调用边界；当前返回本地模拟结果，供测试注入异常。"""
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
    """校验用户凭证并签发 JWT。"""
    user, user_info = authenticate_credentials(request)

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

@app.get("/items/search")
def search_items(keyword: Optional[str] = None, max_price: Optional[float] = None, db: Session = Depends(get_db)):
    """按关键词和最高价格筛选商品。"""
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

@app.post("/items", status_code=201)
def create_item(item_in: ItemCreate, db: Session = Depends(get_db)):
    """新增商品并持久化到数据库。"""
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

@app.get("/items/{item_id}")
def get_item_by_id(item_id: int, db: Session = Depends(get_db)):
    """根据商品 ID 查询详情。"""
    db_item = db.query(ItemModel).filter(ItemModel.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    
    return {
        "status": "success",
        "data": {"id": db_item.id, "name": db_item.name, "price": db_item.price, "category": db_item.category}
    }

@app.put("/items/{item_id}")
def update_item(item_id: int, item_update: ItemUpdate, db: Session = Depends(get_db)):
    """修改商品名称或价格。"""
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

@app.delete("/items/{item_id}", status_code=204)
def delete_item(
    item_id: int, 
    authorization: Optional[str] = Header(None),
    x_token: Optional[str] = Header(None), 
    web_access_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    """校验身份和管理员角色后删除商品。"""
    user_role = None
    
    # 优先使用 Authorization 请求头中的 Bearer JWT。
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        payload = decode_access_token(token)
        user_role = payload.get("role")
    elif web_access_token:
        payload = decode_access_token(web_access_token)
        user_role = payload.get("role")
    # 兼容旧版静态 x-token 鉴权。
    elif ADMIN_SECRET_TOKEN and x_token == ADMIN_SECRET_TOKEN:
        user_role = "admin"
    else:
        raise HTTPException(status_code=401, detail="Missing or invalid authentication token in request header")

    # RBAC 权限检查：只有 admin 角色可以删除商品。
    if user_role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden: Admin role required for deletion")
        
    db_item = db.query(ItemModel).filter(ItemModel.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
        
    db.delete(db_item)
    db.commit()
    return Response(status_code=204)

@app.post("/orders/pay")
def pay_order(pay_req: PayRequest):
    """处理支付请求，将下游超时和服务异常分别映射为 504 和 502。"""
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

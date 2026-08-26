"""商品目录 Agent 工具调用 MVP。

当前不调用真实模型，也不执行系统命令，只提供一个可测试的工具边界，
为后续 Agent、RAG 和 MCP 学习打基础。
"""

from __future__ import annotations

import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping

from main import ItemModel, SessionLocal


# 权限集合只保存权限名称，例如 catalog:read；调用工具前会检查它。
PermissionSet = frozenset[str]
ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class ToolExecutionError(Exception):
    """可控的工具失败，用于向 Agent 调用方返回稳定错误。"""

    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


@dataclass(frozen=True)
class ToolSpec:
    """描述一个工具的契约和实际处理函数。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    required_permission: str
    handler: ToolHandler
    timeout_seconds: float = 1.0


@dataclass(frozen=True)
class ToolCall:
    """描述 Agent 计划发起的一次工具调用。"""

    tool_name: str
    arguments: dict[str, Any]
    trace_id: str
    permissions: PermissionSet = field(default_factory=frozenset)


def parse_tool_call(
    raw_call: Mapping[str, Any],
    permissions: PermissionSet,
    trace_id: str | None = None,
) -> ToolCall:
    """把模型风格的字典解析为受控的 ToolCall。"""
    if not isinstance(raw_call, Mapping):
        raise ToolExecutionError("INVALID_ARGUMENT", "tool call must be an object")

    # 权限和 trace 由应用程序注入，不能接受模型在输出中自行声明。
    allowed_fields = {"tool_name", "arguments"}
    unknown_fields = sorted(set(raw_call) - allowed_fields)
    if unknown_fields:
        raise ToolExecutionError(
            "INVALID_ARGUMENT", f"unknown tool call field(s): {', '.join(unknown_fields)}"
        )

    tool_name = raw_call.get("tool_name")
    arguments = raw_call.get("arguments")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ToolExecutionError("INVALID_ARGUMENT", "tool_name must be a non-empty string")
    if not isinstance(arguments, Mapping):
        raise ToolExecutionError("INVALID_ARGUMENT", "arguments must be an object")

    return ToolCall(
        tool_name=tool_name,
        arguments=dict(arguments),
        trace_id=trace_id or uuid.uuid4().hex,
        permissions=permissions,
    )


@dataclass(frozen=True)
class TraceEvent:
    """记录一次调用的最小定位证据。"""

    trace_id: str
    tool_name: str
    status: str
    duration_ms: float
    error_type: str | None = None


@dataclass(frozen=True)
class ToolExecutionResult:
    """执行器统一返回的成功或失败结果。"""

    ok: bool
    data: dict[str, Any] | None
    error_type: str | None
    message: str | None
    trace: TraceEvent

    def to_dict(self) -> dict[str, Any]:
        """转换为适合 API 或测试报告使用的字典。"""
        return asdict(self)


def _is_number(value: Any) -> bool:
    """判断数值类型，同时排除 bool，因为 bool 是 int 的子类。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_arguments(tool: ToolSpec, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """校验本 MVP 使用的简化 JSON Schema。

    当前只有两个工具，因此只实现项目实际用到的少量规则，不增加第三方依赖。
    """
    if not isinstance(arguments, Mapping):
        raise ToolExecutionError("INVALID_ARGUMENT", "arguments must be an object")

    schema = tool.input_schema
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    missing = [name for name in required if name not in arguments]
    if missing:
        raise ToolExecutionError(
            "INVALID_ARGUMENT", f"missing required argument(s): {', '.join(missing)}"
        )

    unknown = sorted(set(arguments) - set(properties))
    if unknown and schema.get("additionalProperties") is False:
        raise ToolExecutionError(
            "INVALID_ARGUMENT", f"unknown argument(s): {', '.join(unknown)}"
        )

    for name, value in arguments.items():
        if name not in properties:
            continue
        property_schema = properties[name]
        allowed_types = property_schema.get("type", [])
        if isinstance(allowed_types, str):
            allowed_types = [allowed_types]
        if value is None and "null" in allowed_types:
            continue

        type_valid = any(
            {
                "string": isinstance(value, str),
                "integer": isinstance(value, int) and not isinstance(value, bool),
                "number": _is_number(value),
                "boolean": isinstance(value, bool),
            }.get(type_name, False)
            for type_name in allowed_types
        )
        if not type_valid:
            expected = ", ".join(allowed_types)
            raise ToolExecutionError(
                "INVALID_ARGUMENT", f"{name} must be of type {expected}"
            )

    return dict(arguments)


def _item_to_dict(item: ItemModel) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "price": item.price,
        "category": item.category,
    }


def search_items_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """复用项目真实 SQLite 数据库的只读商品搜索能力。"""
    keyword = arguments.get("keyword")
    max_price = arguments.get("max_price")
    if max_price is not None and max_price <= 0:
        raise ToolExecutionError("INVALID_ARGUMENT", "max_price must be greater than 0")

    db = SessionLocal()
    try:
        query = db.query(ItemModel)
        if keyword:
            query = query.filter(ItemModel.name.ilike(f"%{keyword}%"))
        if max_price is not None:
            query = query.filter(ItemModel.price <= max_price)
        items = query.all()
        data = [_item_to_dict(item) for item in items]
        return {"status": "success", "total": len(data), "data": data}
    finally:
        db.close()


def get_item_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """复用项目真实 SQLite 数据库的商品详情查询能力。"""
    db = SessionLocal()
    try:
        item = db.query(ItemModel).filter(ItemModel.id == arguments["item_id"]).first()
        if item is None:
            raise ToolExecutionError(
                "ITEM_NOT_FOUND", f"Item {arguments['item_id']} not found"
            )
        return {"status": "success", "data": _item_to_dict(item)}
    finally:
        db.close()


class ToolRegistry:
    """通过白名单统一管理和执行工具。"""

    def __init__(self, tools: list[ToolSpec] | None = None):
        self._tools = {tool.name: tool for tool in tools or []}

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def list_tools(self) -> list[dict[str, Any]]:
        """只暴露工具契约，不把 Python 函数对象暴露给调用方。"""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "required_permission": tool.required_permission,
            }
            for tool in self._tools.values()
        ]

    def execute(self, call: ToolCall) -> ToolExecutionResult:
        """按固定顺序完成工具发现、鉴权、校验、超时控制和 trace。"""
        started_at = time.perf_counter()
        tool = self._tools.get(call.tool_name)

        if tool is None:
            return self._failure(call, started_at, "TOOL_NOT_FOUND", "tool is not registered")

        # 先鉴权再执行业务函数，避免无权限请求触碰数据库或其他副作用。
        if tool.required_permission not in call.permissions:
            return self._failure(
                call,
                started_at,
                "PERMISSION_DENIED",
                f"permission required: {tool.required_permission}",
            )

        try:
            arguments = _validate_arguments(tool, call.arguments)
        except ToolExecutionError as exc:
            return self._failure(call, started_at, exc.error_type, exc.message)

        # 每次调用单独设置超时；超时结果不能继续被当作成功结果使用。
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(tool.handler, arguments)
        try:
            data = future.result(timeout=tool.timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            return self._failure(call, started_at, "TIMEOUT", "tool execution timed out")
        except ToolExecutionError as exc:
            executor.shutdown(wait=True, cancel_futures=True)
            return self._failure(call, started_at, exc.error_type, exc.message)
        except Exception as exc:  # 未预期异常也转换为稳定的工具错误契约。
            executor.shutdown(wait=True, cancel_futures=True)
            return self._failure(call, started_at, "TOOL_ERROR", str(exc))
        else:
            executor.shutdown(wait=True, cancel_futures=True)
            duration_ms = (time.perf_counter() - started_at) * 1000
            return ToolExecutionResult(
                ok=True,
                data=data,
                error_type=None,
                message=None,
                trace=TraceEvent(
                    trace_id=call.trace_id,
                    tool_name=call.tool_name,
                    status="success",
                    duration_ms=round(duration_ms, 2),
                ),
            )

    @staticmethod
    def _failure(
        call: ToolCall,
        started_at: float,
        error_type: str,
        message: str,
    ) -> ToolExecutionResult:
        duration_ms = (time.perf_counter() - started_at) * 1000
        return ToolExecutionResult(
            ok=False,
            data=None,
            error_type=error_type,
            message=message,
            trace=TraceEvent(
                trace_id=call.trace_id,
                tool_name=call.tool_name,
                status="error",
                duration_ms=round(duration_ms, 2),
                error_type=error_type,
            ),
        )


SEARCH_ITEMS_SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {"type": ["string", "null"]},
        "max_price": {"type": ["number", "null"]},
    },
    "required": [],
    "additionalProperties": False,
}

GET_ITEM_SCHEMA = {
    "type": "object",
    "properties": {"item_id": {"type": "integer"}},
    "required": ["item_id"],
    "additionalProperties": False,
}


def build_catalog_registry() -> ToolRegistry:
    """构建默认的只读商品工具白名单。"""
    return ToolRegistry(
        [
            ToolSpec(
                name="search_items",
                description="Search catalog items by name keyword and maximum price.",
                input_schema=SEARCH_ITEMS_SCHEMA,
                required_permission="catalog:read",
                handler=search_items_tool,
            ),
            ToolSpec(
                name="get_item",
                description="Get one catalog item by its numeric ID.",
                input_schema=GET_ITEM_SCHEMA,
                required_permission="catalog:read",
                handler=get_item_tool,
            ),
        ]
    )


class OfflineQueryPlanner:
    """用简单规则模拟模型，只负责生成 ToolCall，不执行工具。"""

    def plan(
        self,
        user_text: str,
        permissions: PermissionSet,
        trace_id: str | None = None,
    ) -> ToolCall:
        """把用户文字解析为结构化调用计划。"""
        trace_id = trace_id or uuid.uuid4().hex
        if not isinstance(user_text, str) or not user_text.strip():
            raise ToolExecutionError("INVALID_ARGUMENT", "user_text is required")

        text = user_text.strip()
        # 这是 MVP 的“文字识别”环节：用规则提取 ID 或价格，不代表真实模型理解。
        item_match = re.search(r"(?:商品\s*ID|item\s*id|id)\s*[:：]?\s*(\d+)", text, re.I)
        if item_match:
            return ToolCall(
                "get_item",
                {"item_id": int(item_match.group(1))},
                trace_id,
                permissions,
            )

        max_price_match = re.search(r"(\d+(?:\.\d+)?)\s*元(?:以内|以下)", text)
        max_price = float(max_price_match.group(1)) if max_price_match else None
        keyword = re.sub(r"(帮我|请|查询|查找|搜索|商品|价格|\d+(?:\.\d+)?\s*元(?:以内|以下)?)", "", text)
        keyword = re.sub(r"\s+", " ", keyword).strip(" 的") or None
        return ToolCall(
            "search_items",
            {"keyword": keyword, "max_price": max_price},
            trace_id,
            permissions,
        )


class ToolCallingAgent:
    """把 Planner 产出的 ToolCall 交给受控执行器。"""

    def __init__(
        self,
        planner: OfflineQueryPlanner | None = None,
        registry: ToolRegistry | None = None,
    ):
        self.planner = planner or OfflineQueryPlanner()
        self.registry = registry or build_catalog_registry()

    def run(
        self,
        user_text: str,
        permissions: PermissionSet,
        trace_id: str | None = None,
    ) -> ToolExecutionResult:
        """先规划调用，再统一执行；Planner 不拥有工具执行权限。"""
        started_at = time.perf_counter()
        trace_id = trace_id or uuid.uuid4().hex
        try:
            planned = self.planner.plan(user_text, permissions, trace_id)
            # 兼容两种 Planner：内部 Stub 可直接返回 ToolCall，模型适配器通常返回 dict。
            call = (
                planned
                if isinstance(planned, ToolCall)
                else parse_tool_call(planned, permissions, trace_id)
            )
        except ToolExecutionError as exc:
            # 规划阶段失败时也返回统一结果，方便上层按错误类型处理。
            call = ToolCall("planner", {}, trace_id, permissions)
            return self.registry._failure(call, started_at, exc.error_type, exc.message)

        return self.registry.execute(call)


class OfflineQueryAgent(ToolCallingAgent):
    """兼容旧名称的离线 Agent，实际由 Planner 和 Executor 两层组成。"""

    pass


__all__ = [
    "GET_ITEM_SCHEMA",
    "SEARCH_ITEMS_SCHEMA",
    "OfflineQueryPlanner",
    "OfflineQueryAgent",
    "parse_tool_call",
    "ToolCall",
    "ToolExecutionError",
    "ToolExecutionResult",
    "ToolCallingAgent",
    "ToolRegistry",
    "ToolSpec",
    "TraceEvent",
    "build_catalog_registry",
]

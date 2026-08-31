"""通过本机Ollama接入真实Qwen Tool Calling Planner。"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence
from urllib import error as url_error
from urllib import request as url_request
from urllib.parse import urlparse

from agent_mvp import (
    PermissionSet,
    ToolExecutionError,
    ToolRegistry,
)


OllamaPlannerTransport = Callable[[url_request.Request, float], bytes]


OLLAMA_PLANNER_SYSTEM_PROMPT = """你是SDET商品系统的工具规划器。
必须且只能从系统提供的工具中选择一个工具，并严格按照工具Schema填写参数。
不要直接回答用户问题，不要编造工具，不要输出权限或trace_id。
权限、工具校验和实际执行均由应用程序负责。"""

# v0保留原文；v1只是待评测的候选方案，不预先断言它一定更好。
OLLAMA_PLANNER_PROMPTS = {
    "v0": OLLAMA_PLANNER_SYSTEM_PROMPT,
    "v1": OLLAMA_PLANNER_SYSTEM_PROMPT + """
工具分工：明确商品ID的详情查询使用get_item；按名称关键词或最高价格查商品使用search_items；
询问接口规则、鉴权、状态码、日志或商品创建规则使用search_knowledge。
保留用户给出的ID、名称关键词和价格上限，不添加用户没有提供的筛选条件。
知识查询可以改写，但必须保留问题原意、否定条件和限定范围，不能改成预设答案。""",
}


def get_planner_prompt(version: str) -> str:
    """只允许选择已登记的提示词版本，避免拼写错误后悄悄回退。"""
    if version not in OLLAMA_PLANNER_PROMPTS:
        raise ValueError("prompt_version必须是v0或v1")
    return OLLAMA_PLANNER_PROMPTS[version]


def _send_ollama_planner_request(
    http_request: url_request.Request,
    timeout_seconds: float,
) -> bytes:
    """发送真实HTTP请求；测试时可替换为不会访问网络的假传输函数。"""
    with url_request.urlopen(http_request, timeout=timeout_seconds) as response:
        return response.read()


def build_ollama_tool_definitions(
    tool_contracts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """把内部工具契约转换为Ollama原生Function Calling格式。"""
    definitions: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for contract in tool_contracts:
        name = contract.get("name")
        description = contract.get("description")
        input_schema = contract.get("input_schema")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("tool contract name must be a non-empty string")
        if name in seen_names:
            raise ValueError(f"duplicate tool contract: {name}")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"tool description is required: {name}")
        if not isinstance(input_schema, Mapping):
            raise ValueError(f"tool input_schema must be an object: {name}")

        seen_names.add(name)
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": dict(input_schema),
                },
            }
        )

    if not definitions:
        raise ValueError("at least one tool contract is required")
    return definitions


@dataclass(frozen=True)
class OllamaToolPlanner:
    """让Qwen只提出工具调用计划，程序仍保留最终执行权。"""

    tool_contracts: tuple[Mapping[str, Any], ...]
    model: str = "qwen3:4b-instruct"
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 60.0
    max_output_tokens: int = 256
    temperature: float = 0.0
    transport: OllamaPlannerTransport = field(
        default=_send_ollama_planner_request,
        repr=False,
        compare=False,
    )
    prompt_version: str = "v0"

    def __post_init__(self) -> None:
        """启动前校验连接配置和工具契约，尽早暴露配置错误。"""
        get_planner_prompt(self.prompt_version)
        parsed_url = urlparse(self.base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("base_url must be a valid HTTP or HTTPS URL")
        if not self.model.strip():
            raise ValueError("model must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be greater than zero")
        if self.temperature < 0:
            raise ValueError("temperature must not be negative")
        build_ollama_tool_definitions(self.tool_contracts)

    @classmethod
    def from_registry(
        cls,
        registry: ToolRegistry,
        **configuration: Any,
    ) -> "OllamaToolPlanner":
        """只读取注册表公开的工具契约，不接触内部Python处理函数。"""
        return cls(
            tool_contracts=tuple(registry.list_tools()),
            **configuration,
        )

    def plan(
        self,
        user_text: str,
        permissions: PermissionSet,
        trace_id: str | None = None,
    ) -> Mapping[str, Any]:
        """请求模型选择一个工具；permissions和trace_id不会发送给模型。"""
        if not isinstance(user_text, str) or not user_text.strip():
            raise ToolExecutionError("INVALID_ARGUMENT", "user_text is required")

        # permissions与trace_id仅用于满足统一Planner接口，安全字段由应用程序持有。
        del permissions, trace_id
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": get_planner_prompt(self.prompt_version)},
                {"role": "user", "content": user_text.strip()},
            ],
            "tools": build_ollama_tool_definitions(self.tool_contracts),
            "stream": False,
            "think": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_output_tokens,
            },
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = url_request.Request(
            url=f"{self.base_url.rstrip('/')}/api/chat",
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )

        raw_response = self._request(http_request)
        return self._parse_response(raw_response)

    def _request(self, http_request: url_request.Request) -> bytes:
        """把网络异常转换为ToolCallingAgent能够统一处理的规划错误。"""
        try:
            return self.transport(http_request, self.timeout_seconds)
        except url_error.HTTPError as exc:
            raise ToolExecutionError(
                "PLANNER_HTTP_ERROR",
                f"Ollama planner returned HTTP {exc.code}",
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ToolExecutionError(
                "PLANNER_TIMEOUT",
                "Ollama planner request timed out",
            ) from exc
        except url_error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ToolExecutionError(
                    "PLANNER_TIMEOUT",
                    "Ollama planner request timed out",
                ) from exc
            raise ToolExecutionError(
                "PLANNER_UNAVAILABLE",
                f"cannot connect to Ollama planner at {self.base_url}",
            ) from exc
        except (ConnectionError, OSError) as exc:
            raise ToolExecutionError(
                "PLANNER_UNAVAILABLE",
                f"cannot connect to Ollama planner at {self.base_url}",
            ) from exc

    @staticmethod
    def _parse_response(raw_response: bytes) -> Mapping[str, Any]:
        """提取原生tool_calls，并缩减为内部解析器允许的两个字段。"""
        try:
            response_data = json.loads(raw_response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolExecutionError(
                "PLANNER_INVALID_RESPONSE",
                "Ollama planner response must be UTF-8 JSON",
            ) from exc

        if not isinstance(response_data, Mapping):
            raise ToolExecutionError(
                "PLANNER_INVALID_RESPONSE",
                "Ollama planner response must be an object",
            )
        if response_data.get("error"):
            raise ToolExecutionError(
                "PLANNER_MODEL_ERROR",
                str(response_data["error"]),
            )

        message = response_data.get("message")
        if not isinstance(message, Mapping):
            raise ToolExecutionError(
                "PLANNER_INVALID_RESPONSE",
                "Ollama planner response is missing message",
            )
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            raise ToolExecutionError(
                "PLANNER_NO_TOOL_CALL",
                "model did not select a tool",
            )
        if len(tool_calls) != 1:
            raise ToolExecutionError(
                "PLANNER_MULTIPLE_TOOL_CALLS",
                "this Agent supports exactly one tool call per turn",
            )

        function = tool_calls[0].get("function") if isinstance(tool_calls[0], Mapping) else None
        if not isinstance(function, Mapping):
            raise ToolExecutionError(
                "PLANNER_INVALID_RESPONSE",
                "tool call is missing function",
            )
        tool_name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ToolExecutionError(
                "PLANNER_INVALID_RESPONSE",
                "tool call function name must be a non-empty string",
            )
        if not isinstance(arguments, Mapping):
            raise ToolExecutionError(
                "PLANNER_INVALID_RESPONSE",
                "tool call arguments must be an object",
            )

        # 不透传模型响应中的其他字段，权限和trace仍由ToolCallingAgent注入。
        return {
            "tool_name": tool_name,
            "arguments": dict(arguments),
        }


__all__ = [
    "OLLAMA_PLANNER_SYSTEM_PROMPT",
    "OllamaPlannerTransport",
    "OllamaToolPlanner",
    "build_ollama_tool_definitions",
]

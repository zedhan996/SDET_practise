"""启动独立 MCP 服务进程，发现工具并完成一次商品查询冒烟检查。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import Client, StdioServerParameters


PROJECT_ROOT = Path(__file__).resolve().parent


async def main(item_id: int) -> None:
    """依次完成连接、工具发现和调用；离开上下文时关闭连接与子进程。"""
    database = PROJECT_ROOT / "data" / "app" / "dev.db"
    if not database.is_file():
        raise RuntimeError("应用库 data/app/dev.db 不存在，请先确认项目环境。")
    secret = os.environ.get("APP_SECRET_KEY")
    if not secret:
        raise RuntimeError("请先设置 APP_SECRET_KEY，原应用初始化需要此配置。")

    # 明确选择应用库，避免继承其他终端残留的测试库配置；不传递无关密钥。
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-u", str(PROJECT_ROOT / "mcp_catalog_server.py")],
        cwd=str(PROJECT_ROOT),
        env={
            "APP_ENV": "development",
            "APP_DATABASE_URL": f"sqlite:///{database.as_posix()}",
            "APP_SECRET_KEY": secret,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
    )
    # 给整个冒烟流程设定等待上限；这与 Registry 的单工具超时是两层限制。
    async with asyncio.timeout(30), Client(parameters) as client:
        discovered = await client.list_tools()
        names = [tool.name for tool in discovered.tools]
        print("发现工具：", names)
        assert names == ["get_item"], "当前阶段应只暴露一个商品详情工具"
        schema = discovered.tools[0].input_schema
        print("输入 Schema：", json.dumps(schema, ensure_ascii=False, indent=2))

        # 此处明确指定工具及参数，尚未让大模型决定调用哪个工具。
        response = await client.call_tool("get_item", {"item_id": item_id})
        print("MCP 工具是否失败：", response.is_error)
        if response.is_error:
            print("错误内容：", response.content)
        assert not response.is_error, "MCP 工具调用失败，请检查错误内容和服务端日志"
        data = response.structured_content
        print("工具结果：", json.dumps(data, ensure_ascii=False, indent=2))
        assert data is not None and data["ok"] is True
        assert data["data"]["data"]["id"] == item_id, "返回商品 ID 与请求不一致"
        print("PASS：工具发现、跨进程调用、商品 ID 断言通过")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP 商品详情查询冒烟检查")
    parser.add_argument("--item-id", type=int, default=101, help="查询的商品 ID")
    asyncio.run(main(parser.parse_args().item_id))

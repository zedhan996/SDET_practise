"""项目辅助 CLI：检查运行环境并筛选 pytest 用例。"""

import argparse
import os
import platform
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def check_python_version():
    """检查当前 Python 是否为项目要求的 3.11 系列。"""
    actual_version = platform.python_version()
    passed = sys.version_info[:2] == (3, 11)
    return "Python version", passed, actual_version


def check_required_file(filename):
    """检查项目根目录下的必要文件是否存在。"""
    file_path = PROJECT_ROOT / filename
    return (
        f"Required file: {filename}",
        file_path.is_file(),
        str(file_path),
    )


def check_dependency(module_name):
    """检查当前 Python 环境能否找到指定导入模块。"""
    installed = find_spec(module_name) is not None
    detail = "available" if installed else "missing"
    return f"Dependency: {module_name}", installed, detail


def check_environment_variable(name):
    """只检查配置是否存在，不把密钥内容打印到终端。"""
    configured = bool(os.getenv(name))
    detail = "configured" if configured else "missing"
    return f"Environment variable: {name}", configured, detail


def run_environment_check():
    """执行全部环境检查，并返回适合 shell/CI 判断的退出码。"""
    database_variable = (
        "TEST_DATABASE_URL"
        if os.getenv("APP_ENV") == "testing"
        else "APP_DATABASE_URL"
    )
    checks = [
        check_python_version(),
        check_required_file("main.py"),
        check_required_file("tests/api/test_api.py"),
        check_required_file("requirements.txt"),
        check_dependency("fastapi"),
        check_dependency("pytest"),
        check_dependency("sqlalchemy"),
        check_dependency("jwt"),
        check_environment_variable("APP_SECRET_KEY"),
        check_environment_variable(database_variable),
    ]

    for name, passed, detail in checks:
        status = "PASS" if passed else "FAIL"
        print(f"{status} {name}: {detail}")

    return 0 if all(passed for _, passed, _ in checks) else 1


def build_pytest_command(args):
    """把 CLI 参数转换成安全的 pytest 参数列表。"""
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(PROJECT_ROOT / "tests" / "api" / "test_api.py"),
        "-q",
    ]

    if args.keyword:
        command.extend(["-k", args.keyword])

    if args.last_failed:
        command.append("--lf")

    return command


def run_tests(args):
    """启动 pytest，并把 pytest 的退出码原样交给调用方。"""
    command = build_pytest_command(args)
    result = subprocess.run(command, cwd=PROJECT_ROOT)
    return result.returncode


def build_parser():
    """创建 CLI 参数解析器和 check/test 两个子命令。"""
    parser = argparse.ArgumentParser(
        description="Web 后端测开项目辅助工具"
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser(
        "check",
        help="检查项目运行环境",
    )

    test_parser = subparsers.add_parser(
        "test",
        help="筛选并运行 pytest",
    )
    test_options = test_parser.add_mutually_exclusive_group()
    test_options.add_argument(
        "--keyword",
        help="只运行测试名称中包含关键字的用例",
    )
    test_options.add_argument(
        "--last-failed",
        action="store_true",
        help="只重新运行上一次失败的测试",
    )

    return parser


def main():
    """解析用户命令并分发到对应功能。"""
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "check":
        return run_environment_check()

    if args.command == "test":
        return run_tests(args)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

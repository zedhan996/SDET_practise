import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
QA_TOOL = PROJECT_ROOT / "qa_tool.py"


def run_cli(*args):
    """用当前 Python 环境启动 CLI，并捕获输出供断言使用。"""
    return subprocess.run(
        [sys.executable, str(QA_TOOL), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def test_cli_help_lists_commands():
    result = run_cli("--help")

    assert result.returncode == 0
    assert "check" in result.stdout
    assert "test" in result.stdout


def test_cli_check_reports_environment():
    result = run_cli("check")

    assert result.returncode == 0
    assert "PASS Python version" in result.stdout
    assert "PASS Dependency: pytest" in result.stdout


def test_cli_keyword_runs_matching_tests():
    result = run_cli("test", "--keyword", "login_success")

    assert result.returncode == 0
    assert "1 passed" in result.stdout


def test_cli_rejects_conflicting_test_options():
    result = run_cli(
        "test",
        "--keyword",
        "login",
        "--last-failed",
    )

    assert result.returncode == 2
    assert "not allowed with argument" in result.stderr

"""验证包迁移后的子进程启动配置，不启动真实服务端。"""

from pathlib import Path
import sys

from app.mcp.client import PROJECT_ROOT, build_server_parameters


def test_server_launch_uses_module_and_project_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    expected_root = Path(__file__).resolve().parents[2]
    database = expected_root / "data" / "app" / "dev.db"
    parameters = build_server_parameters(database, "test-only-secret")

    assert PROJECT_ROOT == expected_root
    assert parameters.command == sys.executable
    assert parameters.args == ["-u", "-m", "app.mcp.server"]
    assert parameters.cwd == str(expected_root)
    assert parameters.env == {
        "APP_ENV": "development",
        "APP_DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "APP_SECRET_KEY": "test-only-secret",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }

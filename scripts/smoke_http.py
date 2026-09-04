"""部署后 HTTP Smoke 验收；通过 python -m scripts.smoke_http 运行。"""

import argparse
import os
import sys
import time

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
TIMEOUT_SECONDS = 3


def wait_until_healthy(session, base_url, attempts=10, interval_seconds=1):
    """在有限次数内等待服务就绪，耗尽次数后明确失败。"""
    last_error = "没有收到响应"

    for attempt in range(1, attempts + 1):
        try:
            response = session.get(f"{base_url}/health", timeout=TIMEOUT_SECONDS)
            if response.status_code == 200 and response.json().get("status") == "ok":
                print(f"PASS: health check ({attempt}/{attempts})")
                return
            last_error = f"HTTP {response.status_code}: {response.text}"
        except (requests.RequestException, ValueError) as exc:
            last_error = str(exc)

        if attempt < attempts:
            time.sleep(interval_seconds)

    raise RuntimeError(f"服务未在规定时间内就绪：{last_error}")


def check_root(session, base_url):
    response = session.get(f"{base_url}/", timeout=TIMEOUT_SECONDS)
    assert response.status_code == 200
    assert response.json() == {"message": "Hello, SDET!"}


def check_login(session, base_url):
    response = session.post(
        f"{base_url}/login",
        json={"username": "admin", "password": "Admin@123"},
        timeout=TIMEOUT_SECONDS,
    )
    body = response.json()
    assert response.status_code == 200
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def check_item_search(session, base_url):
    response = session.get(
        f"{base_url}/items/search",
        params={"keyword": "iPhone"},
        timeout=TIMEOUT_SECONDS,
    )
    body = response.json()
    assert response.status_code == 200
    assert body["total"] >= 1
    assert any(item["name"] == "iPhone 15" for item in body["data"])


def run_smoke(base_url, attempts=10, interval_seconds=1):
    """运行部署后关键链路检查；失败时由调用方转换成非零退出码。"""
    checks = [check_root, check_login, check_item_search]

    with requests.Session() as session:
        wait_until_healthy(session, base_url, attempts, interval_seconds)
        for check in checks:
            check(session, base_url)
            print(f"PASS: {check.__name__}")

    print(f"Smoke tests passed against {base_url}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="部署后 HTTP Smoke 验收")
    parser.add_argument(
        "--base-url",
        default=os.getenv("SMOKE_BASE_URL", DEFAULT_BASE_URL),
        help="待验收服务地址；默认读取 SMOKE_BASE_URL",
    )
    parser.add_argument("--health-attempts", type=int, default=10)
    parser.add_argument("--health-interval", type=float, default=1)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    base_url = args.base_url.rstrip("/")

    if args.health_attempts < 1 or args.health_interval < 0:
        print("FAIL: 健康检查次数必须大于0，间隔不能小于0", file=sys.stderr)
        return 2

    try:
        run_smoke(base_url, args.health_attempts, args.health_interval)
    except (AssertionError, KeyError, requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"FAIL: Smoke验收未通过：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

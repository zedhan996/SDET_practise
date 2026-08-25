import os

import requests


BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT_SECONDS = 3


def test_root_is_reachable(session):
    response = session.get(f"{BASE_URL}/", timeout=TIMEOUT_SECONDS)

    assert response.status_code == 200
    assert response.json() == {"message": "Hello, SDET!"}


def test_login_returns_access_token(session):
    response = session.post(
        f"{BASE_URL}/login",
        json={"username": "admin", "password": "Admin@123"},
        timeout=TIMEOUT_SECONDS,
    )
    body = response.json()

    assert response.status_code == 200
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_item_search_reads_database(session):
    response = session.get(
        f"{BASE_URL}/items/search",
        params={"keyword": "iPhone"},
        timeout=TIMEOUT_SECONDS,
    )
    body = response.json()

    assert response.status_code == 200
    assert body["total"] >= 1
    assert any(item["name"] == "iPhone 15" for item in body["data"])


def main():
    checks = [
        test_root_is_reachable,
        test_login_returns_access_token,
        test_item_search_reads_database,
    ]

    with requests.Session() as session:
        for check in checks:
            check(session)
            print(f"PASS: {check.__name__}")

    print(f"Smoke tests passed against {BASE_URL}")


if __name__ == "__main__":
    main()

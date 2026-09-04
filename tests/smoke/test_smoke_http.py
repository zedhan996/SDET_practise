from unittest.mock import Mock, call

import requests

from scripts import smoke_http


def make_response(status_code=200, body=None, text=""):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = body or {}
    response.text = text
    return response


def test_health_check_retries_then_succeeds(mocker):
    session = Mock()
    session.get.side_effect = [
        requests.ConnectionError("服务尚未启动"),
        make_response(body={"status": "ok"}),
    ]
    sleep = mocker.patch("scripts.smoke_http.time.sleep")

    smoke_http.wait_until_healthy(
        session,
        "http://service",
        attempts=2,
        interval_seconds=0.5,
    )

    assert session.get.call_count == 2
    sleep.assert_called_once_with(0.5)


def test_health_check_exhaustion_is_a_failure(mocker):
    session = Mock()
    session.get.return_value = make_response(
        status_code=503,
        text="Service Unavailable",
    )
    mocker.patch("scripts.smoke_http.time.sleep")

    try:
        smoke_http.wait_until_healthy(session, "http://service", attempts=2)
    except RuntimeError as exc:
        assert "HTTP 503" in str(exc)
    else:
        raise AssertionError("健康检查耗尽后应当失败")


def test_run_smoke_checks_three_business_endpoints(mocker):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.get.side_effect = [
        make_response(body={"status": "ok"}),
        make_response(body={"message": "Hello, SDET!"}),
        make_response(
            body={
                "total": 1,
                "data": [{"id": 101, "name": "iPhone 15"}],
            }
        ),
    ]
    session.post.return_value = make_response(
        body={"token_type": "bearer", "access_token": "test-token"}
    )
    mocker.patch("scripts.smoke_http.requests.Session", return_value=session)

    smoke_http.run_smoke("http://service", attempts=1, interval_seconds=0)

    assert session.get.call_args_list == [
        call("http://service/health", timeout=3),
        call("http://service/", timeout=3),
        call(
            "http://service/items/search",
            params={"keyword": "iPhone"},
            timeout=3,
        ),
    ]
    session.post.assert_called_once()


def test_main_returns_one_when_smoke_fails(mocker):
    mocker.patch("scripts.smoke_http.run_smoke", side_effect=RuntimeError("服务未就绪"))

    exit_code = smoke_http.main(
        ["--base-url", "http://service", "--health-attempts", "1"]
    )

    assert exit_code == 1


def test_main_rejects_invalid_retry_configuration():
    exit_code = smoke_http.main(["--health-attempts", "0"])

    assert exit_code == 2

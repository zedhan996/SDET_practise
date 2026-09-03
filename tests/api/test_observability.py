def test_request_id_is_returned(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]


def test_request_id_can_be_provided_by_caller(client):
    request_id = "test-request-001"

    response = client.get("/", headers={"X-Request-ID": request_id})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id

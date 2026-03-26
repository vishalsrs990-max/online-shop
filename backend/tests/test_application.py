import application as app_module


def test_to_json_safe_decimal():
    from decimal import Decimal
    result = app_module.to_json_safe({"price": Decimal("25.00")})
    assert result == {"price": 25.0}


def test_health_endpoint(monkeypatch):
    monkeypatch.setattr(app_module, "resolve_queue_url", lambda: "")
    client = app_module.application.test_client()

    response = client.get("/health")

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["queueConfigured"] is False


def test_create_order_requires_items():
    client = app_module.application.test_client()

    response = client.post("/orders", json={
        "email": "test@example.com",
        "paymentStatus": "PAID",
        "paymentRef": "PAY123"
    })

    assert response.status_code == 400
    assert response.get_json()["message"] == "items required"

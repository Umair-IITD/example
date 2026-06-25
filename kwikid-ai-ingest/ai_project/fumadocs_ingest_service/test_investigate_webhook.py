import pytest
from fastapi.testclient import TestClient
import hmac
import hashlib
import json
from app.main import app
from app.config import get_settings

client = TestClient(app)

def test_missing_token():
    response = client.post("/freshdesk/webhook", json={"test": "data"})
    assert response.status_code == 401
    assert response.json() == {"detail": {"error": "invalid_webhook_token"}}

def test_invalid_token():
    headers = {"X-Webhook-Token": "invalid_token_string"}
    response = client.post("/freshdesk/webhook", headers=headers, json={"test": "data"})
    assert response.status_code == 401
    assert response.json() == {"detail": {"error": "invalid_webhook_token"}}

def test_valid_token():
    settings = get_settings()
    secret = settings.freshdesk_webhook_secret
    body = json.dumps({"test": "data"}).encode("utf-8")
    valid_hmac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    
    headers = {"X-Webhook-Token": valid_hmac}
    response = client.post("/freshdesk/webhook", headers=headers, content=body)
    # The request will fail later with 400 because payload isn't a valid ticket
    # but it should pass the 401 check
    assert response.status_code != 401

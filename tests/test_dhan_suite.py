"""Integration tests for all 13 DhanHQ API capabilities in DhanLens."""
import os, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from fastapi.testclient import TestClient
from main import app
from dhan_client import (
    get_client,
    OrderRequest,
    ForeverOrderRequest,
    SuperOrderRequest,
    PositionConvertRequest,
    MarginCalculationItem,
    EdisFormRequest,
)

client = TestClient(app)


def test_01_config_and_health():
    res = client.get("/api/dhan/config")
    assert res.status_code == 200
    data = res.json()
    assert "endpoints_supported" in data
    assert len(data["endpoints_supported"]) == 13


def test_02_funds_and_margin():
    res = client.get("/api/dhan/funds")
    assert res.status_code == 200
    data = res.json()
    assert "available_balance" in data

    # Margin calculation
    calc_res = client.post("/api/dhan/margin/calculate", json={
        "exchange_segment": "NSE_EQ",
        "transaction_type": "BUY",
        "product_type": "CNC",
        "security_id": "1333",
        "quantity": 10,
        "price": 1600.0
    })
    assert calc_res.status_code == 200
    assert calc_res.json()["total_margin"] > 0


def test_03_orders_api():
    # Order book
    res = client.get("/api/dhan/orders")
    assert res.status_code == 200
    assert "orders" in res.json()

    # Slice order
    slice_res = client.post("/api/dhan/orders/slice", json={
        "security_id": "1333",
        "exchange_segment": "NSE_EQ",
        "transaction_type": "BUY",
        "order_type": "LIMIT",
        "product_type": "CNC",
        "quantity": 100,
        "price": 1600.0
    })
    assert slice_res.status_code == 200


def test_04_conditional_forever_orders():
    res = client.get("/api/dhan/forever/orders")
    assert res.status_code == 200
    assert "forever_orders" in res.json()


def test_05_super_orders():
    res = client.get("/api/dhan/super/orders")
    assert res.status_code == 200
    assert "super_orders" in res.json()


def test_06_positions_and_holdings():
    pos_res = client.get("/api/dhan/positions")
    assert pos_res.status_code == 200

    hold_res = client.get("/api/dhan/holdings")
    assert hold_res.status_code == 200
    assert "holdings" in hold_res.json()


def test_07_us_stocks_api():
    res = client.get("/api/dhan/global/holdings")
    assert res.status_code == 200
    assert "account_status" in res.json()


def test_08_trades_and_ledger():
    trd_res = client.get("/api/dhan/trades")
    assert trd_res.status_code == 200

    led_res = client.get("/api/dhan/ledger?from_date=2026-01-01&to_date=2026-10-17")
    assert led_res.status_code == 200
    assert "entries" in led_res.json()


def test_09_kill_switch_api():
    res = client.get("/api/dhan/killswitch")
    assert res.status_code == 200


def test_10_pnl_exit_api():
    res = client.get("/api/dhan/pnlexit")
    assert res.status_code == 200


def test_11_edis_api():
    res = client.get("/api/dhan/edis/tpin")
    assert res.status_code == 200
    assert "status" in res.json()


def test_12_postback_webhook():
    res = client.post("/api/dhan/postback", json={
        "orderId": "TEST_ORDER_999",
        "orderStatus": "TRADED",
        "transactionType": "BUY",
        "securityId": "1333",
        "price": 1650.0,
        "quantity": 10
    })
    assert res.status_code == 200
    info_res = client.get("/api/dhan/postback")
    assert info_res.status_code == 200
    assert info_res.json()["postback_count"] >= 1


def test_13_live_order_feed():
    res = client.get("/api/dhan/live/orders")
    assert res.status_code == 200
    assert "updates" in res.json()


def test_14_static_ip_api():
    res = client.get("/api/dhan/ip")
    assert res.status_code == 200
    d = res.json()
    assert "dhan_client_id" in d
    assert "current_public_ip" in d

    post_res = client.post("/api/dhan/ip", json={"ip": "1.2.3.4", "ip_flag": "PRIMARY"})
    assert post_res.status_code == 200
    assert post_res.json()["status"] == "SUCCESS"

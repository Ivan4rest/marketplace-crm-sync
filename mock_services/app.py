"""Учебные «маркетплейс» и «CRM» в одном приложении — чтобы показать синхронизацию без доступа к настоящим системам.

Запуск: uvicorn mock_services.app:app
Данные хранятся в памяти. POST /demo/advance имитирует активность: новый заказ, смена статусов и остатков.
"""
import itertools
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Учебные маркетплейс и CRM")
NEXT_STATUS = {"new": "paid", "paid": "shipped", "shipped": "delivered"}
CATALOG = [
    ("KB-101", "Клавиатура механическая", 5900.0),
    ("MS-220", "Мышь беспроводная", 2400.0),
    ("HS-305", "Гарнитура с микрофоном", 4300.0),
]
CUSTOMERS = [("Анна Смирнова", "+7 900 000-00-01"), ("Пётр Иванов", "+7 900 000-00-02"), ("Мария Кузнецова", "+7 900 000-00-03")]


def reset():
    app.state.orders = {}
    app.state.deals = {}
    app.state.market_stocks = {}
    app.state.crm_stocks = {sku: 20 for sku, _, _ in CATALOG}
    app.state.order_numbers = itertools.count(1001)
    app.state.deal_numbers = itertools.count(1)
    app.state.clock = itertools.count(1)
    for _ in range(3):
        add_order()


def stamp() -> str:
    # Счётчик в конце гарантирует строго возрастающие отметки даже в пределах одной секунды
    return f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%S}.{next(app.state.clock):06d}Z"


def add_order() -> dict:
    number = next(app.state.order_numbers)
    sku, name, price = CATALOG[number % len(CATALOG)]
    customer, phone = CUSTOMERS[number % len(CUSTOMERS)]
    order = {
        "id": f"MP-{number}",
        "status": "new",
        "updated_at": stamp(),
        "customer": {"name": customer, "phone": phone},
        "items": [{"sku": sku, "name": name, "quantity": 1 + number % 2, "price": price}],
    }
    app.state.orders[order["id"]] = order
    return order


reset()


@app.get("/marketplace/orders")
def list_orders(updated_since: str | None = None, page: int = 1, limit: int = 50):
    orders = sorted(app.state.orders.values(), key=lambda order: order["updated_at"])
    if updated_since:
        orders = [order for order in orders if order["updated_at"] >= updated_since]
    start = (page - 1) * limit
    return {"orders": orders[start:start + limit], "has_more": start + limit < len(orders)}


@app.put("/marketplace/stocks")
def put_stocks(body: dict):
    for row in body["stocks"]:
        app.state.market_stocks[row["sku"]] = row["quantity"]
    return {"updated": len(body["stocks"])}


@app.get("/marketplace/stocks")
def get_market_stocks():
    return {"stocks": app.state.market_stocks}


@app.post("/crm/deals", status_code=201)
def create_deal(fields: dict):
    deal = {"id": f"D-{next(app.state.deal_numbers)}", **fields}
    app.state.deals[deal["id"]] = deal
    return deal


@app.patch("/crm/deals/{deal_id}")
def update_deal(deal_id: str, fields: dict):
    if deal_id not in app.state.deals:
        raise HTTPException(404, "Сделка не найдена")
    app.state.deals[deal_id].update(fields)
    return app.state.deals[deal_id]


@app.get("/crm/deals")
def list_deals(external_id: str | None = None):
    deals = list(app.state.deals.values())
    if external_id:
        deals = [deal for deal in deals if deal.get("external_id") == external_id]
    return {"deals": deals}


@app.get("/crm/stocks")
def get_crm_stocks():
    return {"stocks": [{"sku": sku, "quantity": quantity} for sku, quantity in app.state.crm_stocks.items()]}


@app.post("/demo/advance")
def advance():
    """Двигает по одному шагу каждый незавершённый заказ, добавляет новый и списывает остаток."""
    for order in list(app.state.orders.values()):
        if order["status"] in NEXT_STATUS:
            order["status"], order["updated_at"] = NEXT_STATUS[order["status"]], stamp()
    new = add_order()
    sku = new["items"][0]["sku"]
    app.state.crm_stocks[sku] = max(app.state.crm_stocks[sku] - new["items"][0]["quantity"], 0)
    return {"new_order": new["id"]}

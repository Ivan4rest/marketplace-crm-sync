"""Клиенты внешних систем. Чтобы подключить настоящий маркетплейс или CRM,
достаточно переписать методы этих двух классов — логика синхронизации не меняется.
"""
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Item:
    sku: str
    name: str
    quantity: int
    price: float


@dataclass(frozen=True)
class Order:
    id: str
    status: str
    updated_at: str  # ISO 8601, по этому полю идёт выборка изменений
    customer: str
    phone: str
    items: tuple[Item, ...]

    @property
    def total(self) -> float:
        return round(sum(item.quantity * item.price for item in self.items), 2)


class ApiError(Exception):
    """Внешняя система ответила ошибкой или недоступна."""


def request(client: httpx.Client, method: str, url: str, **kwargs) -> dict:
    try:
        response = client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as error:
        raise ApiError(f"{method} {url}: HTTP {error.response.status_code}") from error
    except httpx.TransportError as error:
        raise ApiError(f"{method} {url}: {type(error).__name__}") from error


class Marketplace:
    def __init__(self, client: httpx.Client, page_size: int = 50):
        self.client, self.page_size = client, page_size

    def orders_since(self, cursor: str | None) -> list[Order]:
        """Все заказы, изменённые не раньше cursor, от старых к новым."""
        orders, page = [], 1
        while True:
            params = {"page": page, "limit": self.page_size}
            if cursor:
                params["updated_since"] = cursor
            data = request(self.client, "GET", "/orders", params=params)
            orders += [
                Order(
                    id=row["id"],
                    status=row["status"],
                    updated_at=row["updated_at"],
                    customer=row["customer"]["name"],
                    phone=row["customer"]["phone"],
                    items=tuple(Item(**item) for item in row["items"]),
                )
                for row in data["orders"]
            ]
            if not data["has_more"]:
                return orders
            page += 1

    def set_stocks(self, stocks: dict[str, int]) -> None:
        request(self.client, "PUT", "/stocks", json={"stocks": [{"sku": k, "quantity": v} for k, v in stocks.items()]})


class Crm:
    def __init__(self, client: httpx.Client):
        self.client = client

    def create_deal(self, fields: dict) -> str:
        return request(self.client, "POST", "/deals", json=fields)["id"]

    def update_deal(self, deal_id: str, fields: dict) -> None:
        request(self.client, "PATCH", f"/deals/{deal_id}", json=fields)

    def find_deal(self, external_id: str) -> str | None:
        deals = request(self.client, "GET", "/deals", params={"external_id": external_id})["deals"]
        return deals[0]["id"] if deals else None

    def stocks(self) -> dict[str, int]:
        return {row["sku"]: row["quantity"] for row in request(self.client, "GET", "/stocks")["stocks"]}

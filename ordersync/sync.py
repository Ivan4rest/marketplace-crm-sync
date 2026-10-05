"""Синхронизация: заказы маркетплейса → сделки CRM, остатки CRM → маркетплейс."""
import logging
from dataclasses import dataclass, field

from ordersync.clients import ApiError, Crm, Marketplace, Order
from ordersync.state import State

log = logging.getLogger("ordersync")

# статус заказа на маркетплейсе → этап сделки в CRM
STAGES = {
    "new": "Новый заказ",
    "paid": "Оплачен",
    "shipped": "Отгружен",
    "delivered": "Успешно завершён",
    "cancelled": "Отменён",
}


@dataclass
class Report:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"создано {self.created}, обновлено {self.updated}, без изменений {self.skipped}, ошибок {len(self.failed)}"


def deal_fields(order: Order) -> dict:
    return {
        "external_id": order.id,
        "title": f"Заказ {order.id}",
        "stage": STAGES.get(order.status, "Новый заказ"),
        "amount": order.total,
        "contact": {"name": order.customer, "phone": order.phone},
        "comment": "\n".join(f"{item.name} ({item.sku}) × {item.quantity}" for item in order.items),
    }


def push_order(order: Order, crm: Crm, state: State) -> str:
    """Создаёт или обновляет сделку. Возвращает created | updated | skipped."""
    link = state.link(order.id)
    if link and link["updated_at"] == order.updated_at:
        return "skipped"
    if link:
        crm.update_deal(link["deal_id"], deal_fields(order))
        state.save_link(order.id, link["deal_id"], order.updated_at)
        return "updated"
    # Сделка могла быть создана в прошлый раз, а связь не сохранилась из-за сбоя, — ищем, чтобы не задвоить
    deal_id = crm.find_deal(order.id)
    if deal_id:
        crm.update_deal(deal_id, deal_fields(order))
        outcome = "updated"
    else:
        deal_id = crm.create_deal(deal_fields(order))
        outcome = "created"
    state.save_link(order.id, deal_id, order.updated_at)
    return outcome


def sync_orders(market: Marketplace, crm: Crm, state: State) -> Report:
    report = Report()
    cursor = state.get("orders_cursor")
    orders = market.orders_since(cursor)
    for order in orders:
        try:
            outcome = push_order(order, crm, state)
        except ApiError as error:
            log.warning("Заказ %s не передан: %s", order.id, error)
            state.save_failure(order.id, order.updated_at, str(error))
            report.failed.append(order.id)
            continue
        setattr(report, outcome, getattr(report, outcome) + 1)
    # Курсор не уходит дальше самого раннего непереданного заказа: его запросим снова в следующий раз.
    # Уже переданные заказы при повторе пропускаются по совпадению updated_at.
    pending = [row["updated_at"] for row in state.failures()]
    reached = max((order.updated_at for order in orders), default=cursor)
    new_cursor = min(pending + [reached]) if pending and reached else reached
    if new_cursor:
        state.set("orders_cursor", new_cursor)
    return report


def sync_stocks(market: Marketplace, crm: Crm, state: State) -> int:
    """Отправляет на маркетплейс только изменившиеся остатки. Возвращает их количество."""
    current, sent = crm.stocks(), state.sent_stocks()
    changed = {sku: quantity for sku, quantity in current.items() if sent.get(sku) != quantity}
    if changed:
        market.set_stocks(changed)
        state.save_sent_stocks(changed)
    return len(changed)

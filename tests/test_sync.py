import httpx
import pytest
from fastapi.testclient import TestClient

from mock_services import app as mock
from ordersync.clients import ApiError, Crm, Marketplace
from ordersync.state import State
from ordersync.sync import sync_orders, sync_stocks


class Env:
    """Синхронизатор, подключённый к учебным сервисам; умеет «ронять» CRM для выбранных запросов."""

    def __init__(self, tmp_path):
        mock.reset()
        self.http = TestClient(mock.app)
        self.fail = lambda request: False
        self.calls = []

        def crm_handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(f"{request.method} {request.url.path}")
            if self.fail(request):
                return httpx.Response(500)
            return self.forward(request)

        self.market = Marketplace(httpx.Client(transport=httpx.MockTransport(self.forward), base_url="http://test/marketplace"), page_size=2)
        self.crm = Crm(httpx.Client(transport=httpx.MockTransport(crm_handler), base_url="http://test/crm"))
        self.state = State(str(tmp_path / "state.db"))

    def forward(self, request: httpx.Request) -> httpx.Response:
        response = self.http.request(request.method, str(request.url), content=request.content, headers=dict(request.headers))
        return httpx.Response(response.status_code, content=response.content, headers={"content-type": "application/json"})

    def sync(self):
        return sync_orders(self.market, self.crm, self.state)

    @property
    def deals(self):
        return {deal["external_id"]: deal for deal in mock.app.state.deals.values()}


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_first_run_creates_deals_across_pages(env):
    report = env.sync()
    assert (report.created, report.updated, report.failed) == (3, 0, [])
    deal = env.deals["MP-1001"]
    assert deal["stage"] == "Новый заказ"
    assert deal["amount"] == 8600.0  # 2 × 4300
    assert deal["contact"] == {"name": "Мария Кузнецова", "phone": "+7 900 000-00-03"}
    assert "Гарнитура с микрофоном (HS-305) × 2" in deal["comment"]


def test_second_run_without_changes_touches_nothing(env):
    env.sync()
    env.calls.clear()
    report = env.sync()
    assert (report.created, report.updated) == (0, 0)
    assert env.calls == []
    assert len(env.deals) == 3


def test_status_change_updates_deal_and_new_order_is_created(env):
    env.sync()
    env.http.post("/demo/advance")
    report = env.sync()
    assert (report.created, report.updated) == (1, 3)
    assert env.deals["MP-1001"]["stage"] == "Оплачен"
    assert env.deals["MP-1004"]["stage"] == "Новый заказ"
    assert len(env.deals) == 4


def test_failed_order_is_retried_without_duplicates(env):
    env.fail = lambda request: request.method == "POST" and b"MP-1002" in request.content
    report = env.sync()
    assert report.failed == ["MP-1002"] and report.created == 2
    assert [row["order_id"] for row in env.state.failures()] == ["MP-1002"]

    env.fail = lambda request: False
    report = env.sync()
    assert (report.created, report.failed) == (1, [])
    assert env.state.failures() == []
    assert sorted(env.deals) == ["MP-1001", "MP-1002", "MP-1003"]
    assert len(mock.app.state.deals) == 3


def test_deal_created_but_link_lost_is_not_duplicated(env, monkeypatch):
    # CRM создала сделку, но процесс упал до сохранения связи
    original = env.state.save_link
    monkeypatch.setattr(env.state, "save_link", lambda *args: (_ for _ in ()).throw(RuntimeError("сбой")))
    with pytest.raises(RuntimeError):
        env.sync()
    assert len(mock.app.state.deals) == 1
    monkeypatch.setattr(env.state, "save_link", original)
    report = env.sync()
    assert (report.created, report.updated) == (2, 1)
    assert len(mock.app.state.deals) == 3


def test_marketplace_outage_changes_nothing(env):
    env.sync()
    cursor = env.state.get("orders_cursor")
    env.market.client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)), base_url="http://test/marketplace")
    with pytest.raises(ApiError):
        env.sync()
    assert env.state.get("orders_cursor") == cursor


def test_stocks_are_sent_only_when_changed(env):
    assert sync_stocks(env.market, env.crm, env.state) == 3
    assert mock.app.state.market_stocks == {"KB-101": 20, "MS-220": 20, "HS-305": 20}
    assert sync_stocks(env.market, env.crm, env.state) == 0
    env.http.post("/demo/advance")
    assert sync_stocks(env.market, env.crm, env.state) == 1
    assert sorted(mock.app.state.market_stocks.values()) == [19, 20, 20]

"""Запуск: python -m ordersync            — один проход
          python -m ordersync --every 60  — повторять каждые 60 секунд
"""
import argparse
import logging
import os
import sys
import time

import httpx

from ordersync.clients import ApiError, Crm, Marketplace
from ordersync.state import State
from ordersync.sync import sync_orders, sync_stocks


def client(url_var: str, token_var: str, default_url: str) -> httpx.Client:
    headers = {"Authorization": f"Bearer {os.environ[token_var]}"} if os.getenv(token_var) else {}
    return httpx.Client(base_url=os.getenv(url_var, default_url), headers=headers, timeout=20)


def main() -> int:
    parser = argparse.ArgumentParser(prog="ordersync", description="Синхронизация заказов и остатков")
    parser.add_argument("--every", type=int, metavar="СЕК", help="повторять с таким интервалом")
    parser.add_argument("--db", default=os.getenv("STATE_DB", "state.db"), help="файл состояния")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log = logging.getLogger("ordersync")

    market = Marketplace(client("MARKETPLACE_URL", "MARKETPLACE_TOKEN", "http://localhost:8000/marketplace"))
    crm = Crm(client("CRM_URL", "CRM_TOKEN", "http://localhost:8000/crm"))
    state = State(args.db)
    while True:
        try:
            log.info("Заказы: %s", sync_orders(market, crm, state))
            log.info("Остатки: отправлено %s", sync_stocks(market, crm, state))
        except ApiError as error:
            # Недоступен сам источник — пропускаем проход, состояние не меняется
            log.error("Проход не выполнен: %s", error)
            if not args.every:
                return 1
        if not args.every:
            return 0
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())

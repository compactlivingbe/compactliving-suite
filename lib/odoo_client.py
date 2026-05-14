"""Odoo XML-RPC client - werkt met API-key (anders dan JSON-RPC sessie-auth)."""
import xmlrpc.client
from typing import Any


class OdooClient:
    def __init__(self, url: str, db: str, login: str, api_key: str):
        self.url = url.rstrip('/')
        self.db = db
        self.login = login
        self.api_key = api_key
        self.common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        self.uid = self.common.authenticate(db, login, api_key, {})
        if not self.uid:
            raise RuntimeError(f"Auth failed (uid=False) for {login}@{db}")

    def call(self, model: str, method: str, args: list = None, kwargs: dict = None) -> Any:
        args = args or []
        kwargs = kwargs or {}
        return self.models.execute_kw(self.db, self.uid, self.api_key, model, method, args, kwargs)

    def search_read(self, model: str, domain: list, fields: list, limit: int = 100, order: str = None) -> list:
        kwargs = {"limit": limit}
        if order:
            kwargs["order"] = order
        return self.call(model, "search_read", [domain, fields], kwargs)

    def create(self, model: str, vals: dict) -> int:
        return self.call(model, "create", [vals])

    def write(self, model: str, ids: list, vals: dict) -> bool:
        return self.call(model, "write", [ids, vals])

    def read(self, model: str, ids: list, fields: list) -> list:
        return self.call(model, "read", [ids, fields])

    def find_partner(self, name: str, vat: str = None):
        """Zoek leverancier op naam of BTW-nummer. Return partner record of None."""
        if vat:
            res = self.search_read("res.partner", [("vat", "=", vat)], ["id", "name", "vat"], 5)
            if res:
                return res[0]
        # Fallback op naam (leveranciers met supplier_rank > 0)
        res = self.search_read(
            "res.partner",
            [("supplier_rank", ">", 0), ("name", "ilike", name)],
            ["id", "name", "vat"], 5
        )
        return res[0] if res else None

    def find_purchase_journal(self) -> int:
        res = self.search_read("account.journal", [("type", "=", "purchase")], ["id", "name"], 1)
        return res[0]["id"] if res else None

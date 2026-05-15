"""v2: Odoo JSON-RPC client via /jsonrpc endpoint (werkt met API-key OF wachtwoord).
Equivalent van XML-RPC maar over JSON HTTP — robuuster onder Streamlit/cloud.
"""
import json
import requests
from typing import Any


class OdooClient:
    def __init__(self, url: str, db: str, login: str, api_key: str = None, password: str = None):
        self.url = url.rstrip('/')
        self.db = db
        self.login = login
        # API-key OF wachtwoord (beide werken voor /jsonrpc execute_kw)
        self.password = api_key or password
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"
        self.uid = None
        self._authenticate()

    def _jsonrpc(self, service: str, method: str, args: list, retry=True):
        """Low-level JSON-RPC call via /jsonrpc."""
        try:
            r = self.session.post(
                f"{self.url}/jsonrpc",
                data=json.dumps({"jsonrpc": "2.0", "method": "call",
                                  "params": {"service": service,
                                             "method": method, "args": args}}),
                timeout=120,
            )
            r.raise_for_status()
            d = r.json()
            if "error" in d:
                raise RuntimeError(f"Odoo error: {json.dumps(d['error'])[:500]}")
            return d.get("result")
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            if retry:
                # Refresh session + retry 1x
                self.session = requests.Session()
                self.session.headers["Content-Type"] = "application/json"
                return self._jsonrpc(service, method, args, retry=False)
            raise

    def _authenticate(self):
        uid = self._jsonrpc("common", "authenticate",
                             [self.db, self.login, self.password, {}])
        if not uid:
            raise RuntimeError(
                f"Odoo auth failed: uid=False voor login={self.login} (verkeerde credentials?)"
            )
        self.uid = uid

    def call(self, model: str, method: str, args: list = None, kwargs: dict = None) -> Any:
        """Roept Odoo model.method aan via execute_kw (XML-RPC equivalent over JSON)."""
        args = args or []
        kwargs = kwargs or {}
        return self._jsonrpc(
            "object", "execute_kw",
            [self.db, self.uid, self.password, model, method, args, kwargs]
        )

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
        if vat:
            res = self.search_read("res.partner", [("vat", "=", vat)], ["id", "name", "vat"], 5)
            if res:
                return res[0]
        res = self.search_read(
            "res.partner",
            [("supplier_rank", ">", 0), ("name", "ilike", name)],
            ["id", "name", "vat"], 5
        )
        return res[0] if res else None

    def find_purchase_journal(self) -> int:
        res = self.search_read("account.journal", [("type", "=", "purchase")], ["id", "name"], 1)
        return res[0]["id"] if res else None

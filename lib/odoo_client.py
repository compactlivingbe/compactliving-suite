"""Odoo JSON-RPC client (vervangt XML-RPC) - werkt met API-key OF wachtwoord.
Gebruikt requests.Session, robuuster onder Streamlit Cloud (geen XML-RPC state issues).
"""
import json
import requests
from typing import Any


class OdooClient:
    def __init__(self, url: str, db: str, login: str, api_key: str = None, password: str = None):
        self.url = url.rstrip('/')
        self.db = db
        self.login = login
        # API-key heeft voorrang; password is fallback
        self.password = api_key or password
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"
        self.uid = None
        self._authenticate()

    def _authenticate(self):
        """JSON-RPC authenticatie. Sets self.uid + cookie."""
        # Methode 1: /web/session/authenticate (zet ook session cookie)
        r = self.session.post(
            f"{self.url}/web/session/authenticate",
            data=json.dumps({"jsonrpc": "2.0", "params":
                              {"db": self.db, "login": self.login, "password": self.password}}),
            timeout=30,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("result") and d["result"].get("uid"):
            self.uid = d["result"]["uid"]
            return
        # Methode 2: fallback via common/authenticate (XML-RPC equivalent in JSON)
        r = self.session.post(
            f"{self.url}/jsonrpc",
            data=json.dumps({
                "jsonrpc": "2.0", "method": "call",
                "params": {"service": "common", "method": "authenticate",
                           "args": [self.db, self.login, self.password, {}]}
            }),
            timeout=30,
        )
        r.raise_for_status()
        d = r.json()
        uid = d.get("result")
        if not uid:
            raise RuntimeError(f"Odoo auth failed: {d}")
        self.uid = uid

    def call(self, model: str, method: str, args: list = None, kwargs: dict = None) -> Any:
        """Roept Odoo model.method aan. Ondersteunt zowel API-key als session-based auth."""
        args = args or []
        kwargs = kwargs or {}
        # /web/dataset/call_kw (gebruikt session cookie)
        try:
            r = self.session.post(
                f"{self.url}/web/dataset/call_kw",
                data=json.dumps({"jsonrpc": "2.0", "method": "call",
                                  "params": {"model": model, "method": method,
                                             "args": args, "kwargs": kwargs}}),
                timeout=120,
            )
            r.raise_for_status()
            d = r.json()
            if "error" in d:
                # Session expired? Probeer opnieuw te authenticeren + retry
                err = d.get("error", {})
                msg = json.dumps(err)[:300]
                if "session" in msg.lower() or "expired" in msg.lower() or err.get("code") == 100:
                    self._authenticate()
                    r = self.session.post(
                        f"{self.url}/web/dataset/call_kw",
                        data=json.dumps({"jsonrpc": "2.0", "method": "call",
                                          "params": {"model": model, "method": method,
                                                     "args": args, "kwargs": kwargs}}),
                        timeout=120,
                    )
                    r.raise_for_status()
                    d = r.json()
                if "error" in d:
                    raise RuntimeError(f"Odoo error: {json.dumps(d['error'])[:300]}")
            return d.get("result")
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            # Force nieuwe sessie + retry 1x
            self.session = requests.Session()
            self.session.headers["Content-Type"] = "application/json"
            self._authenticate()
            r = self.session.post(
                f"{self.url}/web/dataset/call_kw",
                data=json.dumps({"jsonrpc": "2.0", "method": "call",
                                  "params": {"model": model, "method": method,
                                             "args": args, "kwargs": kwargs}}),
                timeout=120,
            )
            r.raise_for_status()
            d = r.json()
            if "error" in d:
                raise RuntimeError(f"Odoo error after retry: {json.dumps(d['error'])[:300]}")
            return d.get("result")

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

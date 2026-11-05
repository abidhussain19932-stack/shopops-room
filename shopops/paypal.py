"""PayPal REST client, just what the fulfilment agent needs: OAuth + refunds."""
from __future__ import annotations

import time
import uuid

import httpx

from .config import settings


class PayPalError(Exception):
    pass


class PayPal:
    def __init__(self, client_id: str | None = None, secret: str | None = None,
                 env: str | None = None, http: httpx.AsyncClient | None = None):
        self.client_id = client_id or settings.paypal_client_id
        self.secret = secret or settings.paypal_client_secret
        env = env or settings.paypal_env
        self.base = "https://api-m.paypal.com" if env == "live" else "https://api-m.sandbox.paypal.com"
        self.http = http or httpx.AsyncClient(timeout=25, trust_env=False)
        self._token, self._exp = "", 0.0

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.secret)

    async def token(self) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        r = await self.http.post(f"{self.base}/v1/oauth2/token", auth=(self.client_id, self.secret),
                                 data={"grant_type": "client_credentials"})
        if r.status_code != 200:
            raise PayPalError(f"PayPal auth failed: {r.status_code}")
        j = r.json()
        self._token, self._exp = j["access_token"], time.time() + j.get("expires_in", 3000)
        return self._token

    async def refund_capture(self, capture_id: str, amount: float | None, currency: str,
                             note: str, request_id: str | None = None) -> dict:
        """Refund a captured payment (full when amount is None). Idempotent per request_id."""
        body: dict = {"note_to_payer": note[:255]}
        if amount is not None:
            body["amount"] = {"value": f"{amount:.2f}", "currency_code": currency}
        r = await self.http.post(
            f"{self.base}/v2/payments/captures/{capture_id}/refund", json=body,
            headers={"Authorization": f"Bearer {await self.token()}",
                     "Content-Type": "application/json",
                     "PayPal-Request-Id": request_id or str(uuid.uuid4())})
        if r.status_code >= 400:
            raise PayPalError(f"refund rejected: {r.status_code} {r.text[:300]}")
        return r.json()

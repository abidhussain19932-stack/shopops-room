"""Settings, read from the environment (.env for the stack, agents.env for keys)."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv(os.getenv("AGENTS_ENV_FILE", "agents.env"))
except Exception:  # pragma: no cover
    pass

AGENTS = {
    "sales-agent": "ShopOps Sales: posts paid PayPal orders and customer escalations from the "
                   "ShopPal storefront bot into the ops room.",
    "inventory-agent": "ShopOps Inventory: reserves stock for paid orders, flags low stock and "
                       "drafts supplier purchase orders for the owner to approve.",
    "fulfillment-agent": "ShopOps Fulfilment: packs and ships orders and issues PayPal refunds, "
                         "only after the owner approves in chat.",
}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def agent_env_prefix(name: str) -> str:
    """'inventory-agent' -> 'INVENTORY_AGENT'."""
    return name.upper().replace("-", "_").replace(".", "_")


@dataclass
class Settings:
    switch_url: str = _env("SWITCH_URL", "http://localhost:8000").rstrip("/")
    registration_token: str = _env("AGENT_REGISTRATION_TOKEN")
    admin_email: str = _env("GATEWAY_ADMIN_EMAIL")
    admin_password: str = _env("GATEWAY_ADMIN_PASSWORD")
    telegram_bot_token: str = _env("TELEGRAM_BOT_TOKEN")
    telegram_bot_username: str = _env("TELEGRAM_BOT_USERNAME", "AMBshopOpsbot")
    owner_telegram_username: str = _env("OWNER_TELEGRAM_USERNAME")
    llm_api_key: str = _env("GROQ_API_KEY") or _env("LLM_API_KEY")
    llm_base_url: str = _env("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    llm_model: str = _env("LLM_MODEL", "openai/gpt-oss-120b")
    paypal_client_id: str = _env("PAYPAL_CLIENT_ID")
    paypal_client_secret: str = _env("PAYPAL_CLIENT_SECRET")
    paypal_env: str = _env("PAYPAL_ENV", "sandbox")
    store_path: str = _env("SHOPOPS_DB", "data/shopops.db")
    catalog_path: str = _env("CATALOG_PATH", "data/catalog.json")

    def agent_credentials(self, name: str) -> tuple[str, str]:
        p = agent_env_prefix(name)
        return _env(f"{p}_ID"), _env(f"{p}_KEY")


settings = Settings()

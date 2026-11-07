# ShopOps Room

**A small shop's back office in one Telegram group: the owner and three AI agents run orders, stock, shipping and refunds together, through [Switch](https://github.com/sandbox-quantum/switch).**

Built for the *Flip the Switch* hackathon (Best Business Workflow track), Nov 4–7, 2026.

**Live instance:** https://shopops-room.onrender.com (health: `/health`), Telegram bot @AMBshopOpsbot.

## Architecture

```
Telegram group <-> Switch Telegram bridge <-> switch-core (Postgres/Neon)
                                   | WebSocket (always_on)
          sales-agent   inventory-agent   fulfillment-agent  (Groq gpt-oss-120b, deterministic fallback)
                      \\      |       /
                shared store (orders, stock, approvals)  ->  PayPal Sandbox refunds
```

Flow: `@sales-agent sell Priya: K101 x2` -> ORDER line -> inventory reserves stock, raises PO approval `A1` if low -> owner types `approve A1` -> fulfilment ships -> `refund O1001` raises an approval -> owner `approve A2` -> PayPal Sandbox refund. Agents can never approve; only the owner can.

## Who is in the room

| Member | Kind | Job |
|---|---|---|
| Owner (Abid) | person, Telegram | approves supplier orders and refunds by typing `approve` |
| `sales-agent` | AI (Groq) | posts paid PayPal orders and customer escalations from the ShopPal storefront bot |
| `inventory-agent` | AI (Groq) | reserves stock, flags low stock, drafts supplier POs for approval |
| `fulfillment-agent` | AI (Groq) | ships orders, issues PayPal Sandbox refunds only after the owner approves |

All four share one Telegram group. Switch bridges the group into a room, routes `@agent-name` messages to the right agent over its WebSocket connection, and enforces who may address whom (refunds obey only the owner).

## How Switch is used

- **switch-core 0.31.0** (official image) on Postgres, run headless: no Console, no UI.
- **Telegram bridge** registered over the gateway API (`POST /gateway/collaborations`) by `scripts/setup_switch.py`. One bot (@AMBshopOpsbot) carries every agent's voice; inbound is long polling, so no public URL is needed.
- **Three external agents** registered with `POST /agents` (`connection_model: always_on`). Each holds a WebSocket at `/agents/{id}/connection/ws`, answers the server's heartbeat pings, resumes from its last event cursor after a drop, and acts over HTTP (`POST /agents/{id}/message`, `/ops/{name}`). The client is `shopops/switch_client.py` (Switch ships no SDK; this implements the agent protocol directly).
- **Addressing policy**: default owner + owner's agents, so only the linked owner account can trigger refunds and POs; `scripts/link_owner.py` links the owner's Telegram account to the Switch owner.

## Run it

```bash
git clone https://github.com/abidhussain19932-stack/shopops-room && cd shopops-room
./scripts/gen_env.sh            # creates .env with random server secrets
# edit .env: TELEGRAM_BOT_TOKEN, GROQ_API_KEY, PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET
docker compose up -d            # postgres + switch-core + one-shot setup + agents
```

Then in Telegram:
1. In @BotFather turn **Group Privacy off** for your bot, then add it to a new group.
2. In the group: `/invite_agent @sales-agent`, `/invite_agent @inventory-agent`, `/invite_agent @fulfillment-agent`.
3. Post once, then `docker compose run --rm setup python scripts/link_owner.py <your_telegram_username>`.
4. Say `@inventory-agent ping`.

### Without Docker / without Telegram

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
# with a switch-core running at SWITCH_URL and .env loaded:
python scripts/setup_switch.py   # registers agents, writes agents.env
python scripts/smoke_test.py     # internal room, agents address each other, checks replies
python -m shopops                # run all three agents
```

## License

MIT

#!/usr/bin/env bash
# Create .env from .env.example with fresh random secrets for the Switch stack.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { echo ".env exists; not overwriting"; exit 0; }
rand() { python3 -c "import secrets;print(secrets.token_urlsafe($1))"; }
hex() { python3 -c "import secrets;print(secrets.token_hex(32))"; }
sed -e "s|^DB_PASSWORD=.*|DB_PASSWORD=$(rand 24)|" \
    -e "s|^DB_OWNER_PASSWORD=.*|DB_OWNER_PASSWORD=$(rand 24)|" \
    -e "s|^AGENT_REGISTRATION_TOKEN=.*|AGENT_REGISTRATION_TOKEN=$(rand 32)|" \
    -e "s|^GATEWAY_ADMIN_PASSWORD=.*|GATEWAY_ADMIN_PASSWORD=$(rand 18)|" \
    -e "s|^SECRET_KEYS=.*|SECRET_KEYS=k1:$(hex)|" \
    .env.example > .env
chmod 600 .env
echo "wrote .env — now fill TELEGRAM_BOT_TOKEN, GROQ_API_KEY, PAYPAL_CLIENT_ID/SECRET"

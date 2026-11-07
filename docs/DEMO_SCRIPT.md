# Demo script (2-3 min), record Telegram group "ShopPal" on screen
1. (0:00-0:20) Say: one shop owner, three AI agents, one Telegram group, wired together by Switch. Show member list: you, sales, inventory, fulfilment agents. Show https://github.com/abidhussain19932-stack/shopops-room briefly.
2. (0:20-0:40) Type `@inventory-agent show stock`. Agent lists all products.
3. (0:40-1:10) Type `@sales-agent sell Priya: K101 x2`. Point out sales posts the order, inventory reserves stock and raises a supplier PO approval if stock is low, with no human relaying.
4. (1:10-1:40) Type `approve A1`. PO sent. Explain: agents cannot approve, Switch addressing limits it to the owner.
5. (1:40-2:10) Fulfilment ships with a tracking number. Type `@sales-agent escalate ...` to show an escalation.
6. (2:10-2:40) Type `@fulfillment-agent refund O1001`; approval request appears; type `approve A2`; PayPal Sandbox refund confirmed.
7. (2:40-3:00) Close: Switch gives routing, identity and permissions; Groq powers the agents; live at https://shopops-room.onrender.com. Best Business Workflow.
Tip: wake the host first by opening /health; run the flow once before recording.

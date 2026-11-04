from shopops.agents.base import strip_mentions
from shopops.config import agent_env_prefix
from shopops.switch_client import Message, SwitchAgent


def test_message_from_event():
    ev = {"type": "message", "room_id": "r1", "payload": {
        "addressed": True, "sender": "@u:localhost", "sender_name": "owner",
        "message_id": "m1", "body": "@inventory-agent reserve 2 mugs", "timestamp": 1}}
    m = Message.from_event(ev)
    assert m.room_id == "r1" and m.addressed and m.sender_name == "owner"


def test_strip_mentions():
    assert strip_mentions("@inventory-agent ping") == "ping"
    assert strip_mentions("fulfillment-agent: ship #1003") == "ship #1003"


def test_prefix():
    assert agent_env_prefix("fulfillment-agent") == "FULFILLMENT_AGENT"


def test_ws_url_resumes_from_cursor():
    a = SwitchAgent("http://localhost:8000", "aid", "key", "x")
    assert "start_from=head" in a._ws_url() and a._ws_url().startswith("ws://")
    a.cursor, a.generation = 42, 3
    u = a._ws_url()
    assert "start_from=42" in u and "expected_generation=3" in u
    assert a.connection_id in u

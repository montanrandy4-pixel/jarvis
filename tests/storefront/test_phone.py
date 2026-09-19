"""SMS and voice. The signature test is the one that matters most:
the voice webhook sits on a public URL, and without verification anyone who
found it could ring up and be read the shop's revenue.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from storefront import server, sms, voice
from storefront.alerts import CRITICAL, INFO, WARNING, Alert, AlertState, Dispatcher
from storefront.config import Config

TOKEN = "test-auth-token"
URL = "https://tunnel.example.com/voice"


def sign(url: str, params: dict, token: str = TOKEN) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


# ---------------------------------------------------------------- signature

def test_a_correctly_signed_request_verifies():
    params = {"CallSid": "CA1", "From": "+15551234567"}
    assert voice.verify(sign(URL, params), URL, params, TOKEN)


def test_a_tampered_parameter_fails():
    params = {"CallSid": "CA1", "From": "+15551234567"}
    signature = sign(URL, params)
    params["From"] = "+15559999999"
    assert not voice.verify(signature, URL, params, TOKEN)


def test_a_signature_for_a_different_url_fails():
    params = {"CallSid": "CA1"}
    assert not voice.verify(sign("https://evil.example.com/voice", params),
                            URL, params, TOKEN)


def test_the_wrong_token_fails():
    params = {"CallSid": "CA1"}
    assert not voice.verify(sign(URL, params, "other-token"), URL, params, TOKEN)


@pytest.mark.parametrize("signature,url,token", [
    ("", URL, TOKEN),          # no signature at all
    ("abc", URL, ""),          # no token configured -- must fail closed
    ("abc", "", TOKEN),        # no public url configured
])
def test_missing_pieces_fail_closed(signature, url, token):
    assert not voice.verify(signature, url, {"a": "1"}, token)


# ------------------------------------------------------------------ answers

SNAPSHOT = {
    "products": [
        {"title": "Rate Calculator", "state": "ok", "problems": []},
        {"title": "Contract Pack", "state": "blocker", "problems": ["no file"]},
        {"title": "Tax Tracker", "state": "warning", "problems": ["no image"]},
    ],
    "summary": {"orders": 3, "revenue": 217.0, "currency": "USD",
                "average": 72.33, "needs_review": 1},
    "alerts": [{"severity": "critical", "title": "Contract Pack: no file"}],
}


@pytest.mark.parametrize("question,intent", [
    ("how much money did we make", "revenue"),
    ("what's the revenue", "revenue"),
    ("any orders today", "orders"),
    ("is anything broken", "problems"),
    ("what's wrong", "problems"),
    ("how many products", "products"),
    ("what alerts have fired", "alerts"),
    ("how's the shop doing", "status"),
    ("what's the weather", "unknown"),
])
def test_questions_route_to_the_right_intent(question, intent):
    assert voice.classify(question) == intent


def test_revenue_answer_uses_real_numbers():
    reply = voice.answer("how much have we made", SNAPSHOT)
    assert "217.00" in reply and "3 orders" in reply


def test_problem_answer_names_the_blocked_product():
    reply = voice.answer("what is wrong", SNAPSHOT)
    assert "Contract Pack" in reply
    assert "cannot be delivered" in reply


def test_status_answer_leads_with_the_bad_news():
    assert voice.answer("how is everything", SNAPSHOT).startswith("Not good")


def test_status_is_positive_when_nothing_is_blocked():
    clean = {**SNAPSHOT, "products": [{"title": "A", "state": "ok", "problems": []}]}
    assert voice.answer("how is everything", clean).startswith("All good")


@pytest.mark.parametrize("flagged,expected", [
    (0, "Nothing needs review"),
    (1, "One of them needs review"),
    (2, "2 of them need review"),
])
def test_review_counts_read_naturally_aloud(flagged, expected):
    snap = {**SNAPSHOT, "summary": {**SNAPSHOT["summary"], "needs_review": flagged}}
    assert expected in voice.answer("any orders", snap)


def test_an_empty_shop_says_so_rather_than_inventing():
    empty = {"products": [], "summary": {}, "alerts": []}
    assert "not checked" in voice.answer("what is wrong", empty)
    assert "No orders" in voice.answer("how many orders", empty)


def test_an_unrecognised_question_admits_it():
    reply = voice.answer("what is the capital of France", SNAPSHOT)
    assert reply == voice.UNSURE
    assert "revenue" in reply           # and says what it can answer


def test_twiml_is_well_formed_and_escaped():
    from xml.etree import ElementTree
    xml = voice.spoken_answer("what's wrong", SNAPSHOT)
    ElementTree.fromstring(xml)          # raises if malformed
    nasty = {**SNAPSHOT, "products": [
        {"title": "A <script>&", "state": "blocker", "problems": ["x"]}]}
    ElementTree.fromstring(voice.spoken_answer("what is wrong", nasty))


def test_a_rejected_call_reveals_nothing_about_the_shop():
    xml = voice.rejected()
    for leak in ("revenue", "217", "Contract Pack", "order"):
        assert leak not in xml


# ---------------------------------------------------------------------- sms

def test_compose_leads_with_the_worst_and_counts_the_rest():
    body = sms.compose([Alert(WARNING, "minor"), Alert(CRITICAL, "major", "detail"),
                        Alert(INFO, "trivial")], shop="solostack")
    assert body.startswith("solostack: major -- detail")
    assert "+2 more" in body


def test_compose_of_nothing_is_empty():
    assert sms.compose([]) == ""


def test_a_long_message_is_truncated():
    body = sms.compose([Alert(CRITICAL, "x" * 900)])
    assert len(body) <= sms.MAX_BODY


def test_unconfigured_twilio_refuses_rather_than_pretending():
    with pytest.raises(sms.SmsError):
        sms.Twilio().send("hello")
    assert sms.to_sms([Alert(CRITICAL, "x")], sms.Twilio()) is False


def test_only_alerts_above_the_threshold_are_texted(monkeypatch):
    sent = []
    twilio = sms.Twilio("sid", "token", "+15550000000", "+15551111111")
    monkeypatch.setattr(twilio, "send", lambda body, **k: sent.append(body) or "SM1")
    sms.to_sms([Alert(WARNING, "not urgent")], twilio, minimum=CRITICAL)
    assert sent == []
    sms.to_sms([Alert(CRITICAL, "urgent")], twilio, minimum=CRITICAL)
    assert len(sent) == 1


def test_a_failing_send_does_not_raise(monkeypatch):
    twilio = sms.Twilio("sid", "token", "+1", "+2")

    def boom(body, **k):
        raise sms.SmsError("twilio is down")

    monkeypatch.setattr(twilio, "send", boom)
    assert sms.to_sms([Alert(CRITICAL, "x")], twilio) is False


# -------------------------------------------------------------- the webhook

@pytest.fixture
def phone_server(tmp_path, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    config = Config(store_domain="shop.myshopify.com", state_dir=tmp_path,
                    voice_public_url="https://tunnel.example.com")
    ws = server.Workspace(
        config=config,
        dispatcher=Dispatcher(state=AlertState.load(tmp_path / "a.json"), console=False),
    )
    ws.products = SNAPSHOT["products"]
    ws.summary = SNAPSHOT["summary"]
    httpd = server.build(ws, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield ws, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def post(url, path, params, signature):
    data = urllib.parse.urlencode(params).encode()
    request = urllib.request.Request(
        url + path, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "X-Twilio-Signature": signature},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_an_unsigned_call_is_refused_and_leaks_nothing(phone_server):
    _, url = phone_server
    status, body = post(url, "/voice", {"CallSid": "CA1"}, "")
    assert status == 403
    assert "217" not in body and "revenue" not in body


def test_a_signed_call_is_greeted(phone_server):
    _, url = phone_server
    params = {"CallSid": "CA1"}
    status, body = post(url, "/voice", params,
                        sign("https://tunnel.example.com/voice", params))
    assert status == 200
    assert "<Gather" in body and voice.GREETING in body


def test_a_signed_question_is_answered_from_live_state(phone_server):
    _, url = phone_server
    params = {"CallSid": "CA1", "SpeechResult": "what is wrong"}
    status, body = post(url, "/voice/answer", params,
                        sign("https://tunnel.example.com/voice/answer", params))
    assert status == 200
    assert "Contract Pack" in body


def test_the_voice_webhook_is_read_only(phone_server):
    """There is no route that changes anything, however it is signed."""
    ws, url = phone_server
    before = json.dumps(ws.snapshot(), sort_keys=True)
    for path in ("/voice", "/voice/answer"):
        params = {"CallSid": "CA1", "SpeechResult": "delete everything"}
        post(url, path, params, sign(f"https://tunnel.example.com{path}", params))
    assert json.dumps(ws.snapshot(), sort_keys=True) == before

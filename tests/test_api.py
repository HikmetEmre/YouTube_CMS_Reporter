"""The HTTP API: the guided month, and visitor isolation in demo mode."""

import pytest
from fastapi.testclient import TestClient

from cms_reporter import synth, workspace
from cms_reporter.api import app
from cms_reporter.config import load_config

PERIOD = "2026-10"
ASSET_CSV = (
    "Country,Asset Title,Custom ID,Asset ID,Asset Channel ID,Partner Revenue,Asset Labels\n"
    "TR,My Song,acme_song,A1,UC1,100,Acme Label\n"
    "US,My Tune,acme_tune,A2,UC1,50,Acme Label\n"
    "DE,Other,zzz,A3,UC2,10,\n"
)


@pytest.fixture
def client(settings, cfg, tmp_path, monkeypatch):
    """A self-hosted instance with its own copy of the config and one synthetic month."""
    config = tmp_path / "network.yaml"
    config.write_text(settings.config_path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("CMS_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("CMS_CONFIG", str(config))
    monkeypatch.delenv("CMS_DEMO_MODE", raising=False)
    synth.generate_period(settings, cfg, PERIOD)
    return TestClient(app)


def test_guided_month_from_upload_to_packages(client):
    assert client.post("/api/periods", json={"period": "2026-13"}).status_code == 400
    assert client.post("/api/periods", json={"period": "2026-11"}).json() == {"period": "2026-11"}
    month = client.get("/api/periods/2026-11").json()
    assert month["steps"] == {"upload": "todo", "match": "todo", "review": "todo", "send": "todo"}

    # step 1: upload - bad files are refused with a reason and not kept
    files = [("files", ("mine.csv", ASSET_CSV, "text/csv")), ("files", ("notes.csv", "a,b\n1,2\n", "text/csv"))]
    result = client.post("/api/periods/2026-11/files/music", files=files).json()
    assert result["saved"] == ["mine.csv"] and result["rejected"][0]["name"] == "notes.csv"
    client.put("/api/periods/2026-11/inputs", json={"fx_rate": 40, "shorts_pool_usd": {"music": 16}})

    # step 2: nothing matches the sample config; the app suggests matching by label
    month = client.get("/api/periods/2026-11").json()
    assert month["steps"]["match"] == "attention" and month["totals"]["unidentified_count"] == 3
    music = month["verticals"][0]
    assert {"kind": "label", "value": "Acme Label", "assets": 2, "revenue": 150.0} in music["groups"]

    body = {"vertical": "music", "kind": "label", "value": "Acme Label", "producer": "Acme Records"}
    assert client.post("/api/match", json=body).status_code == 400  # a new client needs a rate
    assert client.post("/api/match", json={**body, "new_commission": 0.2}).status_code == 200
    one = {"vertical": "music", "kind": "asset", "value": "A3", "producer": "Acme Records"}
    assert client.post("/api/match", json=one).status_code == 200

    # step 3: review - 160 revenue + 16 shorts - 5 US tax = 171 net, 20% commission
    month = client.get("/api/periods/2026-11").json()
    assert month["steps"] == {"upload": "done", "match": "done", "review": "done", "send": "todo"}
    line = month["combined"][0]
    assert line["Producer"] == "Acme Records" and line["Net Revenue"] == pytest.approx(171)
    assert line["Partner Payment"] == pytest.approx(136.8) and month["totals"]["unidentified"] == 0

    # step 4: preview, generate, download
    pdf = client.get("/api/periods/2026-11/clients/acme-records/preview.pdf")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    packages = client.post("/api/periods/2026-11/packages").json()
    acme = packages["clients"][0]
    assert not packages["stale"] and len(acme["files"]) == 3 and acme["password"]
    got = client.get(f"/api/periods/2026-11/download/acme-records/{acme['files'][0]}")
    assert got.status_code == 200 and len(got.content) > 1000
    assert client.get("/api/periods/2026-11/download/acme-records/..%2F..%2F..%2Fperiod.yaml").status_code == 404
    assert client.get("/api/periods/2026-11").json()["steps"]["send"] == "done"

    # changing the month afterwards marks the packages as out of date
    client.put("/api/periods/2026-11/inputs", json={"fx_rate": 41, "shorts_pool_usd": {"music": 30}})
    month = client.get("/api/periods/2026-11").json()
    assert month["packages"]["stale"] and month["steps"]["send"] == "attention"
    assert client.post("/api/periods/2026-11/deliver", json={}).status_code == 400  # SMTP not configured


def test_overview_clients_channels_and_alerts(client):
    meta = client.get("/api/meta").json()
    assert meta["demo"] is False and meta["periods"][0] == {"period": PERIOD, "label": "October 2026"}
    month = client.get(f"/api/periods/{PERIOD}").json()
    assert month["totals"]["clients"] == 11 and month["trend"][-1]["period"] == PERIOD

    cards = client.get(f"/api/periods/{PERIOD}/clients").json()
    lumen = next(c for c in cards if c["slug"] == "lumen-music-group")
    assert lumen["verticals"] == ["Music", "Non-Music"]
    detail = client.get(f"/api/periods/{PERIOD}/clients/lumen-music-group").json()
    assert detail["totals"]["Partner Payment"] == pytest.approx(lumen["payment"]) and len(detail["top_assets"]) == 10
    assert client.get(f"/api/periods/{PERIOD}/clients/nobody").status_code == 404
    assert client.get("/api/periods/2020-01").status_code == 404

    snapshot = client.post("/api/backups").json()
    assert snapshot["live"] is False and len(snapshot["latest"]["channels"]) == 19
    assert client.get("/api/conflicts").json()["conflicts"] == []
    assert client.post("/api/demo/reset").status_code == 403


def test_demo_visitors_get_private_workspaces(settings, monkeypatch):
    monkeypatch.setenv("CMS_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("CMS_CONFIG", str(settings.config_path))
    monkeypatch.setenv("CMS_DEMO_MODE", "1")
    alice, bob = TestClient(app), TestClient(app)

    meta = alice.get("/api/meta").json()
    assert meta["demo"] and len(meta["periods"]) == workspace.SEED_MONTHS and meta["expires_at"]
    latest = meta["periods"][0]["period"]

    # Alice adds a client and uploads a file; Bob sees neither
    alice.post("/api/producers", json={"vertical": "music", "name": "Alice Only", "commission": 0.3})
    alice.post(f"/api/periods/{latest}/files/music", files=[("files", ("alice.csv", ASSET_CSV, "text/csv"))])
    names = lambda c: [p["name"] for p in c.get("/api/meta").json()["verticals"][0]["producers"]]  # noqa: E731
    files = lambda c: [f["name"] for f in c.get(f"/api/periods/{latest}").json()["files"]["music"]]  # noqa: E731
    assert "Alice Only" in names(alice) and "Alice Only" not in names(bob)
    assert "alice.csv" in files(alice) and "alice.csv" not in files(bob)
    assert "Alice Only" not in load_config(settings.config_path).producer_names  # the shared config is untouched

    # the demo never contacts anyone, and a reset restores the seed
    assert alice.post(f"/api/periods/{latest}/deliver", json={}).status_code == 403
    checked = alice.post("/api/conflicts/check").json()
    assert checked["new"] == 3 and checked["channels"] == ["shown here only"]
    assert alice.post("/api/demo/reset").json() == {"reset": True}
    assert "Alice Only" not in names(alice) and "alice.csv" not in files(alice)

    # expired sessions are deleted
    sessions = settings.data_dir / "sessions"
    assert len(list(sessions.iterdir())) == 2
    workspace._last_purge = 0
    workspace.purge_expired(now=__import__("time").time() + workspace.SESSION_TTL + 5)
    assert list(sessions.iterdir()) == []


def test_test_alert_reaches_telegram_or_whatsapp_and_is_limited_in_the_demo(settings, monkeypatch):
    monkeypatch.setenv("CMS_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("CMS_CONFIG", str(settings.config_path))
    monkeypatch.setenv("CMS_DEMO_MODE", "1")
    for name in ("TELEGRAM_BOT_TOKEN", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM"):
        monkeypatch.delenv(name, raising=False)
    calls = []

    class Reply:
        def __init__(self, ok):
            self.ok = ok

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError("403 Forbidden: bot was blocked by the user")

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Reply(ok="999999999" not in str(kwargs))

    monkeypatch.setattr("cms_reporter.conflicts.requests.post", fake_post)
    visitor = TestClient(app)

    # not connected: the page can still show the preview text, but nothing is sent
    info = visitor.get("/api/conflicts").json()["test"]
    assert "Ownership conflict: Silent Harbor" in info["text"] and info["remaining"] == 3
    assert info["channels"]["telegram"]["available"] is False
    assert visitor.post("/api/conflicts/test", json={"channel": "telegram", "to": "123456789"}).status_code == 400
    assert calls == []

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "55555555")  # the operator's own chat: never a visitor's default
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACxxx")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "+14155238886")

    # bad or missing destinations are refused before anything is sent
    assert visitor.post("/api/conflicts/test", json={"channel": "telegram"}).status_code == 400
    assert visitor.post("/api/conflicts/test", json={"channel": "whatsapp", "to": "05551234567"}).status_code == 400
    assert visitor.post("/api/conflicts/test", json={"channel": "sms", "to": "+905551234567"}).status_code == 400
    assert calls == []

    sent = visitor.post("/api/conflicts/test", json={"channel": "telegram", "to": "123456789"}).json()
    assert sent["sent"] and sent["remaining"] == 2
    url, kwargs = calls[-1]
    assert url == "https://api.telegram.org/botTOKEN/sendMessage" and kwargs["json"]["chat_id"] == "123456789"
    assert "Silent Harbor" in kwargs["json"]["text"] and "made-up data" in kwargs["json"]["text"]

    sent = visitor.post("/api/conflicts/test", json={"channel": "whatsapp", "to": "+90 555 123 45 67"}).json()
    url, kwargs = calls[-1]
    assert "ACxxx/Messages.json" in url and kwargs["data"]["To"] == "whatsapp:+905551234567"
    assert kwargs["data"]["From"] == "whatsapp:+14155238886" and sent["remaining"] == 1

    # a refusal by the provider is explained and does not use up a send
    refused = visitor.post("/api/conflicts/test", json={"channel": "telegram", "to": "999999999"})
    assert refused.status_code == 502 and "press Start" in refused.json()["detail"]
    assert visitor.get("/api/conflicts").json()["test"]["remaining"] == 1

    assert visitor.post("/api/conflicts/test", json={"channel": "telegram", "to": "123456789"}).status_code == 200
    assert visitor.post("/api/conflicts/test", json={"channel": "telegram", "to": "123456789"}).status_code == 429
    assert TestClient(app).get("/api/conflicts").json()["test"]["remaining"] == 3  # limits are per visitor

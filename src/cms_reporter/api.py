"""HTTP API and static hosting for the web interface."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pandas as pd
import yaml
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import backup, conflicts, delivery, security, store, workspace
from .analytics import DemoAnalytics, NoAnalytics
from .config import NetworkConfig, normalize_id, slugify
from .ingest import IngestError, read_report
from .pipeline import PeriodResult, report_data, run_period, write_outputs
from .report_pdf import render_report
from .settlement import MONEY
from .workspace import Workspace

COOKIE = "cms_session"
MAX_FILE = 25 * 1024 * 1024
MAX_DEMO_WORKSPACE = 60 * 1024 * 1024
PERIOD_RE = r"\d{4}-(0[1-9]|1[0-2])"
STATIC = Path(__file__).with_name("static")

app = FastAPI(title="CMS Reporter", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.on_event("startup")
def startup() -> None:
    security.master_key()
    if workspace.demo_mode():
        workspace.ensure_seed()


@app.exception_handler(ValueError)  # ConfigError, IngestError and PeriodError are ValueErrors
@app.exception_handler(security.KeyError_)
async def friendly(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


def ws(request: Request, response: Response) -> Workspace:
    if not workspace.demo_mode():
        return workspace.default_workspace()
    workspace.purge_expired()
    session = request.cookies.get(COOKIE)
    if not session or not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", session):
        session = workspace.new_session_id()
    response.set_cookie(COOKIE, session, max_age=workspace.SESSION_TTL, httponly=True, samesite="lax")
    return workspace.session_workspace(session)


def records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient="records"))


def month_label(period: str) -> str:
    return pd.Period(period, freq="M").strftime("%B %Y")


def check_period(period: str) -> str:
    if not re.fullmatch(PERIOD_RE, period):
        raise HTTPException(400, "Months are written YYYY-MM, for example 2026-11.")
    return period


def run(w: Workspace, period: str) -> PeriodResult:
    check_period(period)
    if not (w.settings.inbox / period).is_dir():
        raise HTTPException(404, f"There is no month {period} yet.")
    return run_period(w.settings, w.cfg, period)


def producer_slug(cfg: NetworkConfig, slug: str) -> str:
    for name in cfg.producer_names:
        if slugify(name) == slug:
            return name
    raise HTTPException(404, "Unknown client.")


# ---------------------------------------------------------------- overview


@app.get("/api/meta")
def meta(w: Workspace = Depends(ws)) -> dict:
    cfg = w.cfg
    return {
        "network": cfg.name,
        "currency": cfg.local_currency,
        "demo": w.demo,
        "expires_at": w.expires_at,
        "periods": [{"period": p, "label": month_label(p)} for p in reversed(w.settings.periods())],
        "withholding": cfg.withholding,
        "verticals": [
            {
                "key": v.key,
                "label": v.label,
                "shorts_mode": v.shorts_mode,
                "producers": [
                    {
                        "name": p.name,
                        "slug": p.slug,
                        "commission": p.commission,
                        "contact_email": p.contact_email,
                        "patterns": p.patterns,
                        "labels": p.labels,
                        "channels": p.channels,
                    }
                    for p in v.producers
                ],
            }
            for v in cfg.verticals.values()
        ],
    }


def _files(w: Workspace, period: str, cfg: NetworkConfig) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for key in cfg.verticals:
        entries = []
        for path in sorted((w.settings.inbox / period / key).glob("*.csv")):
            try:
                frame = read_report(path)
                kept = frame[frame["Partner Revenue"] > 0]
                entries.append(
                    {
                        "name": path.name,
                        "rows": len(kept),
                        "revenue": float(kept["Partner Revenue"].sum()),
                        "kind": "Shorts by channel" if (frame["Report Type"] == "shorts").any() else "Assets",
                        "error": None,
                    }
                )
            except (IngestError, ValueError, UnicodeDecodeError) as exc:
                entries.append({"name": path.name, "rows": 0, "revenue": 0.0, "kind": "", "error": str(exc)})
        out[key] = entries
    return out


def _groups(unidentified: pd.DataFrame) -> list[dict]:
    """Bulk-match suggestions: unmatched assets that share a label or a custom-ID prefix."""
    out = []
    assets = unidentified[unidentified["Asset ID"] != ""]
    labels = assets[assets["Asset Labels"] != ""].groupby("Asset Labels")["Partner Revenue"].agg(["size", "sum"])
    for label, row in labels.iterrows():
        if "|" not in label:
            out.append({"kind": "label", "value": label, "assets": int(row["size"]), "revenue": float(row["sum"])})
    prefix = assets["Custom ID"].map(lambda c: re.split(r"[_\s-]", normalize_id(c.replace(" ", "_")), maxsplit=1)[0])
    prefixes = assets.assign(prefix=prefix)
    prefixes = prefixes[prefixes["prefix"].str.len() >= 3].groupby("prefix")["Partner Revenue"].agg(["size", "sum"])
    for value, row in prefixes.iterrows():
        if row["size"] >= 2:
            out.append({"kind": "pattern", "value": value, "assets": int(row["size"]), "revenue": float(row["sum"])})
    return sorted(out, key=lambda g: -g["revenue"])


def _manifest_path(w: Workspace, period: str) -> Path:
    return w.settings.output / period / "manifest.json"


def _fingerprint(result: PeriodResult) -> str:
    table = result.combined
    return f"{len(table)}:{table['Partner Payment'].sum():.6f}:{result.fx_rate or 0:.6f}"


def _packages(w: Workspace, cfg: NetworkConfig, result: PeriodResult) -> dict | None:
    path = _manifest_path(w, result.period)
    if not path.exists():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    clients = [
        {
            "producer": item.producer,
            "slug": slugify(item.producer),
            "email": item.recipient,
            "files": [f.name for f in item.files],
            "password": security.password_for(slugify(item.producer)),
        }
        for item in delivery.plan(w.settings, cfg, result.period)
    ]
    return {
        "generated_at": manifest["generated_at"],
        "stale": manifest["fingerprint"] != _fingerprint(result),
        "clients": clients,
        "internal": {
            "file": f"settlement_{result.period}.xlsx",
            "password": security.password_for(security.INTERNAL),
        },
    }


@app.get("/api/periods/{period}")
def period_detail(period: str, w: Workspace = Depends(ws)) -> dict:
    cfg = w.cfg
    result = run(w, period)
    store.save_history(w.settings.store, period, {k: v.settlement for k, v in result.verticals.items()})
    history = store.read_history(w.settings.store)
    trend = history[history["period"] <= period].groupby("period")[["Revenue", "Partner Payment"]].sum().tail(12)

    files = _files(w, period, cfg)
    has_files = any(files.values())
    unidentified = sum(len(v.unidentified) for v in result.verticals.values())
    packages = _packages(w, cfg, result)
    combined = result.combined
    return {
        "period": period,
        "label": month_label(period),
        "currency": result.currency,
        "fx_rate": result.fx_rate,
        "inputs": {
            "payout_local": result.inputs.payout_local,
            "fx_rate": result.inputs.fx_rate,
            "shorts_pool_usd": result.inputs.shorts_pool_usd,
            "other_earnings": [e.__dict__ for e in result.inputs.other_earnings],
        },
        "totals": {
            "revenue": result.total_usd,
            "withholding": float(combined["Withholding Tax"].sum()),
            "commission": float(combined["Network Commission"].sum()),
            "payment": float(combined["Partner Payment"].sum()),
            "unidentified": result.unidentified_revenue,
            "unidentified_count": unidentified,
            "clients": len(combined),
        },
        "files": files,
        "verticals": [
            {
                "key": v.key,
                "label": v.label,
                "shorts_mode": cfg.verticals[v.key].shorts_mode,
                "settlement": records(v.settlement),
                "unidentified": records(v.unidentified),
                "groups": _groups(v.unidentified),
            }
            for v in result.verticals.values()
        ],
        "combined": records(combined),
        "trend": [
            {"period": p, "revenue": float(r["Revenue"]), "payment": float(r["Partner Payment"])}
            for p, r in trend.iterrows()
        ],
        "steps": {
            "upload": "done" if has_files else "todo",
            "match": "todo" if not has_files else ("attention" if unidentified else "done"),
            "review": "todo" if not has_files else ("done" if result.fx_rate else "attention"),
            "send": "done" if packages and not packages["stale"] else ("attention" if packages else "todo"),
        },
        "packages": packages,
    }


# ---------------------------------------------------------------- step 1: upload


class NewPeriod(BaseModel):
    period: str


@app.post("/api/periods")
def create_period(body: NewPeriod, w: Workspace = Depends(ws)) -> dict:
    period = check_period(body.period)
    root = w.settings.inbox / period
    for key in w.cfg.verticals:
        (root / key).mkdir(parents=True, exist_ok=True)
    if not (root / "period.yaml").exists():
        (root / "period.yaml").write_text(yaml.safe_dump({"period": period}), encoding="utf-8")
    return {"period": period}


@app.post("/api/periods/{period}/files/{vertical}")
async def upload(period: str, vertical: str, files: list[UploadFile] = File(...), w: Workspace = Depends(ws)) -> dict:
    check_period(period)
    if vertical not in w.cfg.verticals:
        raise HTTPException(404, "Unknown vertical.")
    folder = w.settings.inbox / period / vertical
    folder.mkdir(parents=True, exist_ok=True)
    saved, rejected = [], []
    for file in files:
        name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(file.filename or "report.csv").name)
        if not name.lower().endswith(".csv"):
            rejected.append({"name": name, "reason": "Only CSV files exported from CMS are accepted."})
            continue
        data = await file.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            rejected.append({"name": name, "reason": "The file is larger than 25 MB."})
            continue
        if w.demo and workspace.workspace_size(w) + len(data) > MAX_DEMO_WORKSPACE:
            rejected.append({"name": name, "reason": "The demo workspace is full (60 MB)."})
            continue
        target = folder / name
        target.write_bytes(data)
        try:
            read_report(target)
            saved.append(name)
        except (IngestError, ValueError, UnicodeDecodeError) as exc:
            target.unlink(missing_ok=True)
            rejected.append({"name": name, "reason": str(exc).split(": ", 1)[-1]})
    return {"saved": saved, "rejected": rejected}


@app.delete("/api/periods/{period}/files/{vertical}/{name}")
def delete_file(period: str, vertical: str, name: str, w: Workspace = Depends(ws)) -> dict:
    check_period(period)
    folder = (w.settings.inbox / period / vertical).resolve()
    target = (folder / name).resolve()
    if target.parent != folder or not target.is_file():
        raise HTTPException(404, "No such file.")
    target.unlink()
    return {"deleted": name}


class Inputs(BaseModel):
    payout_local: float | None = Field(None, ge=0)
    fx_rate: float | None = Field(None, ge=0)
    shorts_pool_usd: dict[str, float] = {}


@app.put("/api/periods/{period}/inputs")
def save_inputs(period: str, body: Inputs, w: Workspace = Depends(ws)) -> dict:
    path = w.settings.inbox / check_period(period) / "period.yaml"
    if not path.parent.is_dir():
        raise HTTPException(404, f"There is no month {period} yet.")
    current = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    fx = {"currency": w.cfg.local_currency}
    if body.fx_rate:
        fx["rate"] = body.fx_rate
    if body.payout_local:
        fx["payout_local"] = body.payout_local
    current.update(
        {"period": period, "fx": fx, "shorts_pool_usd": {k: v for k, v in body.shorts_pool_usd.items() if v}}
    )
    path.write_text(yaml.safe_dump(current, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"saved": True}


# ---------------------------------------------------------------- step 2: match


class Match(BaseModel):
    vertical: str
    kind: str  # asset | label | pattern | channel
    value: str
    producer: str
    new_commission: float | None = Field(None, ge=0, le=1)  # set to create the client first


@app.post("/api/match")
def match(body: Match, w: Workspace = Depends(ws)) -> dict:
    cfg = w.cfg
    vertical = cfg.verticals.get(body.vertical)
    if vertical is None:
        raise HTTPException(404, "Unknown vertical.")
    if body.producer not in vertical.producer_names:
        if body.new_commission is None:
            raise HTTPException(400, f"{body.producer} is not a {vertical.label} client yet. Set a commission rate.")
        workspace.add_producer(w.settings.config_path, body.vertical, body.producer, body.new_commission)
    if body.kind == "asset":
        store.add_override(w.settings.store, body.vertical, body.value, body.producer, "assigned in the app")
    else:
        workspace.add_rule(w.settings.config_path, body.vertical, body.producer, body.kind, body.value)
    return {"matched": True}


class NewProducer(BaseModel):
    vertical: str
    name: str
    commission: float = Field(ge=0, le=1)
    contact_email: str | None = None


@app.post("/api/producers")
def create_producer(body: NewProducer, w: Workspace = Depends(ws)) -> dict:
    workspace.add_producer(w.settings.config_path, body.vertical, body.name, body.commission, body.contact_email)
    return {"created": body.name}


# ---------------------------------------------------------------- steps 3 and 4: review and send


@app.get("/api/periods/{period}/clients")
def clients(period: str, w: Workspace = Depends(ws)) -> list[dict]:
    cfg = w.cfg
    result = run(w, period)
    history = store.read_history(w.settings.store)
    history = history[history["period"] <= period]
    out = []
    for row in result.combined.to_dict("records"):
        name = row["Producer"]
        mine = history[history["Producer"] == name].groupby("period")["Partner Payment"].sum().tail(6)
        previous = float(mine.iloc[-2]) if len(mine) >= 2 else None
        out.append(
            {
                "producer": name,
                "slug": slugify(name),
                "email": cfg.contact_email(name),
                "verticals": [v.label for v in result.verticals.values() if name in set(v.settlement["Producer"])],
                "revenue": row["Revenue"],
                "payment": row["Partner Payment"],
                "commission": row["Network Commission"],
                "previous_payment": previous,
                "history": [{"period": p, "payment": float(v)} for p, v in mine.items()],
            }
        )
    return out


@app.get("/api/periods/{period}/clients/{slug}")
def client_detail(period: str, slug: str, w: Workspace = Depends(ws)) -> dict:
    cfg = w.cfg
    name = producer_slug(cfg, slug)
    result = run(w, period)
    if name not in set(result.combined["Producer"]):
        raise HTTPException(404, f"{name} has no revenue in {month_label(period)}.")
    data = report_data(cfg, result, name, result.raw_rows(name), store.read_history(w.settings.store), NoAnalytics())
    statement = data.statement
    return {
        "producer": name,
        "slug": slug,
        "email": cfg.contact_email(name),
        "channels": cfg.channels_of(name),
        "statement": records(statement),
        "totals": {c: float(statement[c].sum()) for c in MONEY},
        "top_assets": [{"name": k, "value": float(v)} for k, v in data.top_assets.items()],
        "countries": [{"name": k, "value": float(v)} for k, v in data.countries.items()],
        "history": [{"period": k, "value": float(v)} for k, v in data.history.items()],
    }


@app.get("/api/periods/{period}/clients/{slug}/preview.pdf")
def preview(period: str, slug: str, w: Workspace = Depends(ws)) -> Response:
    """The client's report, rendered on the fly without a password, for on-screen review only."""
    cfg = w.cfg
    name = producer_slug(cfg, slug)
    result = run(w, period)
    if name not in set(result.combined["Producer"]):
        raise HTTPException(404, f"{name} has no revenue in {month_label(period)}.")
    analytics = DemoAnalytics() if w.demo else NoAnalytics()
    data = report_data(cfg, result, name, result.raw_rows(name), store.read_history(w.settings.store), analytics)
    headers = {"Content-Disposition": f'inline; filename="{slug}_{period}_preview.pdf"', "Cache-Control": "no-store"}
    return Response(render_report(data), media_type="application/pdf", headers=headers)


@app.post("/api/periods/{period}/packages")
def generate(period: str, w: Workspace = Depends(ws)) -> dict:
    cfg = w.cfg
    result = run(w, period)
    if result.combined.empty:
        raise HTTPException(400, "There is nothing to send yet: no revenue is matched to a client.")
    write_outputs(w.settings, cfg, result, DemoAnalytics() if w.demo else NoAnalytics())
    manifest = {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "fingerprint": _fingerprint(result)}
    _manifest_path(w, period).write_text(json.dumps(manifest), encoding="utf-8")
    return _packages(w, cfg, result) or {}


@app.get("/api/periods/{period}/download/{owner}/{name}")
def download(period: str, owner: str, name: str, w: Workspace = Depends(ws)) -> FileResponse:
    base = (w.settings.output / check_period(period)).resolve()
    folder = base / "internal" if owner == "internal" else base / "clients" / owner
    target = (folder / name).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "No such file. Generate the packages first.")
    return FileResponse(target, filename=target.name)


class Send(BaseModel):
    only: str | None = None


@app.post("/api/periods/{period}/deliver")
def deliver(period: str, body: Send, w: Workspace = Depends(ws)) -> list[dict]:
    if w.demo:
        raise HTTPException(403, "The demo never sends e-mail. Download the files instead.")
    if not os.environ.get("SMTP_HOST"):
        raise HTTPException(400, "E-mail is not set up. Add the SMTP settings to .env, or download the files.")
    sent = delivery.deliver(w.settings, w.cfg, check_period(period), send=True, only=body.only)
    return [{"producer": d.producer, "recipient": d.recipient, "status": d.status} for d in sent]


# ---------------------------------------------------------------- channels and alerts


def _snapshots(w: Workspace) -> list[Path]:
    return sorted(w.settings.backups.glob("*/channels.csv"), reverse=True)


@app.get("/api/backups")
def backups(w: Workspace = Depends(ws)) -> dict:
    snaps = _snapshots(w)
    latest = None
    if snaps:
        channels = pd.read_csv(snaps[0]).drop(columns=["Description"], errors="ignore")
        changes_file = snaps[0].parent / "changes.csv"
        latest = {
            "taken": snaps[0].parent.name,
            "channels": records(channels),
            "changes": records(pd.read_csv(changes_file, dtype=str, keep_default_na=False)),
        }
    live = bool(os.environ.get("YOUTUBE_API_KEY")) and not w.demo
    return {"snapshots": [s.parent.name for s in snaps], "latest": latest, "live": live}


@app.post("/api/backups")
def take_backup(w: Workspace = Depends(ws)) -> dict:
    cfg = w.cfg
    key = os.environ.get("YOUTUBE_API_KEY")
    source = backup.YouTubeDataSource(key) if key and not w.demo else backup.DemoSource(cfg)
    backup.snapshot(w.settings, cfg, source)
    return backups(w)


class RecordOnly:
    """Demo mode: conflicts are listed in the app and sent nowhere."""

    name = "shown here only"

    def send(self, text: str) -> None:
        return None


def _notifiers(w: Workspace) -> list:
    return [RecordOnly()] if w.demo else conflicts.notifiers_from_env()


@app.get("/api/conflicts")
def list_conflicts(w: Workspace = Depends(ws)) -> dict:
    seen = sorted(conflicts.read_seen(w.settings).values(), key=lambda c: c["notified_at"], reverse=True)
    return {"conflicts": seen, "channels": [n.name for n in _notifiers(w)], "test": _test_info(w)}


# A fixed, made-up alert. Visitors choose where it goes, never what it says.
SAMPLE_ALERT = conflicts.Conflict(
    id="test-alert",
    kind="Ownership conflict",
    title="Silent Harbor",
    asset_id="A100000000000001",
    other_party="Example Rights Ltd",
    territories="DE, AT, CH",
    status="Action required",
    due="within 7 days",
    source="CMS Reporter test alert (made-up data)",
)
MAX_DEMO_TEST_SENDS = 3


def _chat_channels() -> dict:
    env = os.environ.get
    return {
        "telegram": {
            "available": bool(env("TELEGRAM_BOT_TOKEN")),
            "bot": (env("TELEGRAM_BOT_USERNAME") or "").lstrip("@") or None,
            "has_default": bool(env("TELEGRAM_CHAT_ID")),
        },
        "whatsapp": {
            "available": bool(env("TWILIO_ACCOUNT_SID") and env("TWILIO_AUTH_TOKEN") and env("TWILIO_WHATSAPP_FROM")),
            "sender": env("TWILIO_WHATSAPP_FROM") or None,
            "join_code": env("TWILIO_SANDBOX_JOIN") or None,
            "has_default": bool(env("WHATSAPP_TO")),
        },
    }


def _test_sends_path(w: Workspace) -> Path:
    return w.settings.store / "test_sends.json"


def _test_sends(w: Workspace) -> int:
    path = _test_sends_path(w)
    return int(json.loads(path.read_text())["count"]) if path.exists() else 0


def _test_info(w: Workspace) -> dict:
    remaining = max(MAX_DEMO_TEST_SENDS - _test_sends(w), 0) if w.demo else None
    return {"text": SAMPLE_ALERT.text(), "channels": _chat_channels(), "remaining": remaining}


class TestAlert(BaseModel):
    channel: str  # telegram | whatsapp
    to: str | None = None


@app.post("/api/conflicts/test")
def test_alert(body: TestAlert, w: Workspace = Depends(ws)) -> dict:
    """Send the sample alert to a real Telegram chat or WhatsApp number.

    Works in the public demo too, so a visitor can see an alert arrive on their own
    phone. That is safe to expose because the text is fixed, each visitor gets a few
    sends, and Telegram only delivers to people who have started the bot.
    """
    env = os.environ.get
    channel = _chat_channels().get(body.channel)
    if channel is None:
        raise HTTPException(400, "Choose Telegram or WhatsApp.")
    name = "Telegram" if body.channel == "telegram" else "WhatsApp"
    if not channel["available"]:
        raise HTTPException(400, f"This instance is not connected to {name}, so the preview is all it can show.")
    if w.demo and _test_sends(w) >= MAX_DEMO_TEST_SENDS:
        raise HTTPException(429, "That was the last test alert for this demo session.")

    to = (body.to or "").strip().replace(" ", "")
    if not to and not w.demo:
        to = (
            env("TELEGRAM_CHAT_ID", "")
            if body.channel == "telegram"
            else (env("WHATSAPP_TO", "").split(",")[0]).strip()
        )
    if body.channel == "telegram":
        if not re.fullmatch(r"-?\d{5,16}|@[A-Za-z0-9_]{5,32}", to):
            raise HTTPException(400, "Enter your Telegram chat ID: a number such as 123456789.")
        notifier: conflicts.Notifier = conflicts.TelegramNotifier(env("TELEGRAM_BOT_TOKEN"), to)
    else:
        if not re.fullmatch(r"\+\d{8,15}", to):
            raise HTTPException(400, "Enter the phone number in international format, such as +905551234567.")
        notifier = conflicts.WhatsAppNotifier(
            env("TWILIO_ACCOUNT_SID"), env("TWILIO_AUTH_TOKEN"), env("TWILIO_WHATSAPP_FROM"), [to]
        )

    try:
        notifier.send(SAMPLE_ALERT.text())
    except Exception as exc:  # network errors and refusals from the provider
        hint = (
            "Open the bot in Telegram and press Start first, then try again."
            if body.channel == "telegram"
            else "The number must have joined the WhatsApp sender first."
        )
        raise HTTPException(502, f"{name} did not accept the message. {hint}") from exc
    if w.demo:
        w.settings.store.mkdir(parents=True, exist_ok=True)
        _test_sends_path(w).write_text(json.dumps({"count": _test_sends(w) + 1}))
    return {"sent": True, "channel": body.channel, **_test_info(w)}


@app.post("/api/conflicts/check")
def check_conflicts(w: Workspace = Depends(ws)) -> dict:
    if w.demo:
        conflicts.write_demo_conflicts(w.settings)
        sources: list = [conflicts.CsvFolderSource(w.settings.conflicts_inbox)]
    else:
        sources = conflicts.sources_from_env(w.settings)
    fresh = conflicts.check(w.settings, sources, _notifiers(w))
    return {**list_conflicts(w), "new": len(fresh)}


@app.post("/api/demo/reset")
def reset(request: Request, w: Workspace = Depends(ws)) -> dict:
    if not w.demo or not w.session:
        raise HTTPException(403, "Only the demo can be reset.")
    workspace.session_workspace(w.session, reset=True)
    return {"reset": True}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------- the web app itself

if STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404, "Not found.")
        file = (STATIC / path).resolve()
        if path and STATIC.resolve() in file.parents and file.is_file():
            return FileResponse(file)
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

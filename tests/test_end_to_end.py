"""The whole month on synthetic exports, plus backup, alerts and the web app."""

import io
from datetime import datetime

import pandas as pd
import pytest
from pypdf import PdfReader

from cms_reporter import backup, conflicts, delivery, security, store, synth
from cms_reporter.analytics import DemoAnalytics
from cms_reporter.config import slugify
from cms_reporter.ingest import load_vertical
from cms_reporter.pipeline import run_period, write_outputs

PERIOD = "2026-10"


@pytest.fixture
def month(settings, cfg):
    synth.generate_period(settings, cfg, PERIOD)
    return run_period(settings, cfg, PERIOD)


def test_every_dollar_is_either_settled_or_reported_unidentified(settings, cfg, month):
    for key, v in month.verticals.items():
        loaded = load_vertical(settings.inbox / PERIOD / key)["Partner Revenue"].sum()
        assert v.settlement["Revenue"].sum() + v.unidentified_revenue == pytest.approx(loaded)
        assert v.unidentified_revenue > 0  # the generator plants unlabelled assets
        assert set(v.settlement["Producer"]) <= set(cfg.verticals[key].producer_names)
    table = month.combined
    assert (table["Network Commission"] + table["Partner Payment"]).sum() == pytest.approx(table["Net Revenue"].sum())
    house = table.set_index("Producer").loc["Aurora Originals"]
    assert house["Partner Payment"] == pytest.approx(0)  # the network keeps its own catalogue
    assert month.fx_rate == pytest.approx(month.inputs.payout_local / month.total_usd)


def test_assigning_an_asset_moves_its_revenue_into_the_settlement(settings, cfg, month):
    before = month.verticals["music"]
    asset = before.unidentified.iloc[0]
    store.add_override(settings.store, "music", asset["Asset ID"], "Saffron Tapes")
    after = run_period(settings, cfg, PERIOD).verticals["music"]

    assert asset["Asset ID"] not in set(after.unidentified["Asset ID"])
    assert after.unidentified_revenue == pytest.approx(before.unidentified_revenue - asset["Partner Revenue"])
    gain = (
        after.settlement.set_index("Producer").loc["Saffron Tapes", "Revenue"]
        - before.settlement.set_index("Producer").loc["Saffron Tapes", "Revenue"]
    )
    assert gain == pytest.approx(asset["Partner Revenue"])


def test_outputs_are_complete_and_nothing_is_readable_without_a_password(settings, cfg, month):
    written = write_outputs(settings, cfg, month, DemoAnalytics())
    assert set(written) == {"internal", *month.combined["Producer"]}

    producer = "Lumen Music Group"  # appears in both verticals
    slug = slugify(producer)
    folder, password = written[producer], security.password_for(slugify(producer))
    names = {p.name for p in folder.iterdir()}
    assert names == {f"{slug}_{PERIOD}_report.pdf", f"{slug}_{PERIOD}_raw.xlsx", f"{slug}_{PERIOD}_raw_csv.zip"}

    raw = pd.read_excel(
        io.BytesIO(security.decrypt_xlsx((folder / f"{slug}_{PERIOD}_raw.xlsx").read_bytes(), password))
    )
    assert set(raw["Vertical"]) == {"Music", "Non-Music"}
    statement = month.statement(producer)
    assert raw["Partner Revenue"].sum() == pytest.approx(statement["Revenue"].sum())
    csv = security.read_encrypted_zip(folder / f"{slug}_{PERIOD}_raw_csv.zip", password)
    assert len(pd.read_csv(io.BytesIO(next(iter(csv.values()))))) == len(raw)

    pdf = PdfReader(folder / f"{slug}_{PERIOD}_report.pdf")
    assert pdf.is_encrypted and pdf.decrypt(password)
    text = pdf.pages[0].extract_text()
    assert producer in text and f"${statement['Partner Payment'].sum():,.2f}" in text

    internal = security.decrypt_xlsx(written["internal"].read_bytes(), security.password_for(security.INTERNAL))
    assert {"Combined", "Music", "Music unidentified", "Run"} <= set(pd.ExcelFile(io.BytesIO(internal)).sheet_names)

    # no plaintext leaks: every output file is one of the encrypted containers
    for path in settings.output.rglob("*"):
        if path.is_file():
            head = path.read_bytes()[:8]
            assert path.suffix in {".xlsx", ".zip", ".pdf"}
            assert not (path.suffix == ".xlsx" and head.startswith(b"PK")), path

    plan = delivery.deliver(settings, cfg, PERIOD)  # dry run: nothing is sent
    assert {d.producer for d in plan} == set(month.combined["Producer"])
    assert all(d.status == "planned" for d in plan if d.recipient)


def test_backup_snapshot_and_change_detection(settings, cfg):
    source = backup.DemoSource(cfg)
    folder, changes = backup.snapshot(settings, cfg, source, now=datetime(2026, 11, 1, 9, 0))
    channels = pd.read_csv(folder / "channels.csv")
    expected = {c for v in cfg.verticals.values() for p in v.producers for c in p.channels}
    assert set(channels["Channel ID"]) == expected and channels["Found"].all() and changes.empty
    assert (folder / "videos.csv").exists() and (folder / "snapshot.xlsx").exists()

    class Tampered(backup.DemoSource):
        def channels(self, ids):
            items = super().channels(ids)[1:]  # one channel disappears
            items[0]["snippet"]["title"] = "FREE CRYPTO LIVE"  # another is renamed
            return items

    _, changes = backup.snapshot(settings, cfg, Tampered(cfg), now=datetime(2026, 11, 2, 9, 0))
    assert sorted(c.split(" ")[0] for c in changes["Change"]) == ["NOT", "Title"]
    assert "FREE CRYPTO LIVE" in changes["After"].tolist()
    assert backup.duration_seconds("PT1H2M3S") == 3723 and backup.duration_seconds("P1DT1S") == 86401


def test_conflicts_are_announced_once_and_retried_when_delivery_fails(settings):
    conflicts.write_demo_conflicts(settings)
    sources = [conflicts.CsvFolderSource(settings.conflicts_inbox)]

    class Recorder:
        name = "recorder"

        def __init__(self, fail=False):
            self.sent, self.fail = [], fail

        def send(self, text):
            if self.fail:
                raise ConnectionError("down")
            self.sent.append(text)

    broken = Recorder(fail=True)
    assert len(conflicts.check(settings, sources, [broken])) == 3
    assert conflicts.read_seen(settings) == {}  # nothing delivered, so nothing is marked as announced

    ok = Recorder()
    assert len(conflicts.check(settings, sources, [broken, ok])) == 3
    assert len(ok.sent) == 3 and "Ownership conflict: Silent Harbor" in ok.sent[0] and "DE, AT, CH" in ok.sent[0]
    assert conflicts.check(settings, sources, [ok]) == [] and len(ok.sent) == 3

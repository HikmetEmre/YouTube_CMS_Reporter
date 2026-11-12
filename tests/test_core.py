"""Ingest, attribution and settlement arithmetic on small hand-checked inputs."""

import pandas as pd
import pytest

from cms_reporter import security, store
from cms_reporter.config import ConfigError, NetworkConfig, load_config, normalize_id
from cms_reporter.ingest import COLUMNS, IngestError, load_vertical
from cms_reporter.mapping import resolve, unidentified_summary
from cms_reporter.settlement import PeriodInputs, combine, resolve_fx, settle_vertical

HEADER = "Country,Asset Title,Custom ID,Asset ID,Asset Channel ID,Partner Revenue,Asset Labels,Extra\n"


def rows(*records):
    """records: (country, custom_id, asset_id, revenue, labels[, report_type, channel])"""
    data = []
    for r in records:
        r = (*r, "asset", "")[:7]
        data.append(
            {
                "Country": r[0],
                "Asset Title": f"Title {r[2]}",
                "Custom ID": r[1],
                "Asset ID": r[2],
                "Asset Channel ID": r[6],
                "Partner Revenue": r[3],
                "Asset Labels": r[4],
                "Report Type": r[5],
                "Source File": "t.csv",
            }
        )
    return pd.DataFrame(data, columns=COLUMNS)


def test_ingest_handles_title_rows_shorts_reports_and_non_positive_revenue(tmp_path):
    (tmp_path / "plain.csv").write_text(HEADER + "TR,Song,alpha_song,A1,UC_A,10.5,Alpha Label,x\n", encoding="utf-8")
    (tmp_path / "titled.csv").write_text(
        "Asset Summary Report,2026-10\n"
        + HEADER
        + "US,Tune,,A2,UC_B,2,,x\nDE,Zero,beta_z,A3,UC_B,0,,x\n"
        + "DE,Neg,beta_n,A4,UC_B,-1.5,,x\n",
        encoding="utf-8",
    )
    (tmp_path / "shorts.csv").write_text(
        "Channel ID,Channel Display Name,Net Partner Revenue (Post revshare)\n UC_A ,Alpha Channel,3.25\n",
        encoding="utf-8",
    )
    frame = load_vertical(tmp_path)

    assert sorted(frame["Partner Revenue"]) == [2.0, 3.25, 10.5]  # zero and negative rows dropped
    assert frame.loc[frame["Asset ID"] == "A2", "Custom ID"].iloc[0] == ""  # blank, not 'nan'
    shorts = frame[frame["Report Type"] == "shorts"].iloc[0]
    assert shorts["Asset Channel ID"] == "UC_A" and shorts["Asset Title"] == "Shorts - Alpha Channel"


def test_ingest_rejects_files_that_are_not_revenue_reports(tmp_path):
    (tmp_path / "other.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(IngestError, match="other.csv"):
        load_vertical(tmp_path)
    assert load_vertical(tmp_path / "missing").empty


def test_normalize_id():
    assert normalize_id(" BHR Silent_Harbor ") == "bhrsilent_harbor"
    assert normalize_id(float("nan")) == "" and normalize_id(None) == ""


def test_attribution_precedence(vertical):
    frame = rows(
        ("TR", "ALPHA Song One", "A1", 1, ""),  # untidy custom ID still matches
        ("TR", "alpha_song", "A2", 1, "Beta Label"),  # label beats custom ID
        ("TR", "", "A3", 1, "Other|Shared Imprint"),  # any of several labels
        ("TR", "beta_x", "A4", 1, "Beta Label"),  # override beats both
        ("TR", "mystery", "A5", 1, ""),  # nothing matches
        ("", "", "", 1, "", "shorts", "UC_B"),  # Shorts: by channel
        ("TR", "", "A6", 1, "", "asset", "UC_B"),  # a channel alone does not identify an asset
    )
    out = resolve(frame, vertical, {"A4": "Alpha"})
    assert out["Producer"].tolist()[:4] == ["Alpha", "Beta", "Beta", "Alpha"]
    assert out["Matched By"].tolist()[:4] == ["custom_id", "asset_label", "asset_label", "override"]
    assert pd.isna(out["Producer"].iloc[4]) and out["Producer"].iloc[5] == "Beta" and pd.isna(out["Producer"].iloc[6])

    summary = unidentified_summary(out)
    assert set(summary["Asset ID"]) == {"A5", "A6"} and summary["Partner Revenue"].sum() == 2


def test_settlement_arithmetic(vertical):
    cfg = NetworkConfig("Net", "TRY", {"US": 0.10}, {"music": vertical})
    identified = resolve(
        rows(
            ("US", "alpha_a", "A1", 100.0, ""),
            ("TR", "alpha_b", "A2", 200.0, ""),
            ("us", "beta_a", "B1", 50.0, ""),
            ("DE", "beta_b", "B2", 50.0, ""),
        ),
        vertical,
    )
    table = settle_vertical(identified, vertical, cfg, shorts_pool=40.0, other={"Beta": 10.0}).set_index("Producer")

    alpha, beta = table.loc["Alpha"], table.loc["Beta"]
    assert alpha["Shorts Share"] == pytest.approx(30.0) and beta["Shorts Share"] == pytest.approx(10.0)  # 300:100
    assert alpha["Withholding Tax"] == pytest.approx(10.0)  # 10% of the US 100 only
    assert beta["Withholding Tax"] == pytest.approx(5.0)  # country codes are case-insensitive
    assert alpha["Net Revenue"] == pytest.approx(300 + 30 - 10)
    assert alpha["Network Commission"] == pytest.approx(32.0) and alpha["Partner Payment"] == pytest.approx(288.0)
    assert beta["Net Revenue"] == pytest.approx(100 + 10 + 10 - 5)
    assert beta["Network Commission"] == pytest.approx(23.0) and beta["Partner Payment"] == pytest.approx(92.0)

    total = combine({"music": table.reset_index(), "other": table.reset_index()}).set_index("Producer")
    assert total.loc["Alpha", "Partner Payment"] == pytest.approx(576.0)


def test_per_channel_verticals_ignore_a_shorts_pool(vertical):
    from dataclasses import replace

    per_channel = replace(vertical, shorts_mode="per_channel")
    cfg = NetworkConfig("Net", "TRY", {}, {"music": per_channel})
    table = settle_vertical(resolve(rows(("TR", "alpha_a", "A1", 100.0, "")), per_channel), per_channel, cfg, 40.0)
    assert table["Shorts Share"].sum() == 0 and table["Net Revenue"].iloc[0] == 100


def test_fx_rate_is_stated_or_derived_from_the_payout():
    assert resolve_fx(PeriodInputs("2026-10", fx_rate=41.5, payout_local=1.0), 100) == 41.5
    assert resolve_fx(PeriodInputs("2026-10", payout_local=4000.0), 100.0) == pytest.approx(40.0)
    assert resolve_fx(PeriodInputs("2026-10"), 100.0) is None


def test_config_rejects_bad_rates_and_ambiguous_labels(tmp_path):
    def write(producers):
        path = tmp_path / "n.yaml"
        path.write_text(f"verticals:\n  music:\n    producers:\n{producers}", encoding="utf-8")
        return path

    with pytest.raises(ConfigError, match="between 0 and 1"):
        load_config(write("      - {name: A, commission: 20}\n"))
    with pytest.raises(ConfigError, match="claimed by both"):
        load_config(
            write(
                "      - {name: A, commission: 0.1, asset_labels: [X]}\n"
                "      - {name: B, commission: 0.1, asset_labels: [x]}\n"
            )
        )


def test_encryption_round_trips_and_needs_the_key(tmp_path, monkeypatch):
    import io

    buffer = io.BytesIO()
    pd.DataFrame({"a": [1, 2]}).to_excel(buffer, index=False)
    locked = security.encrypt_xlsx(buffer.getvalue(), "pw")
    assert not locked.startswith(b"PK")  # no longer a readable workbook
    assert pd.read_excel(io.BytesIO(security.decrypt_xlsx(locked, "pw")))["a"].tolist() == [1, 2]

    security.write_encrypted_zip(tmp_path / "x.zip", {"a.csv": b"secret,1"}, "pw")
    assert b"secret" not in (tmp_path / "x.zip").read_bytes()
    assert security.read_encrypted_zip(tmp_path / "x.zip", "pw") == {"a.csv": b"secret,1"}
    with pytest.raises(RuntimeError):
        security.read_encrypted_zip(tmp_path / "x.zip", "wrong")

    first = security.password_for("client-a")
    assert first == security.password_for("client-a") != security.password_for("client-b")
    monkeypatch.setenv("CMS_MASTER_KEY", "a-different-master-key-0123456789abcdef")
    assert security.password_for("client-a") != first
    monkeypatch.delenv("CMS_MASTER_KEY")
    with pytest.raises(security.KeyError_, match="keygen"):
        security.password_for("client-a")


def test_history_is_encrypted_and_replaces_a_rerun_month(tmp_path, monkeypatch):
    line = pd.DataFrame(
        [
            {
                "Producer": "Alpha",
                "Revenue": 123.45,
                "Shorts Share": 0,
                "Other Earnings": 0,
                "Withholding Tax": 0,
                "Net Revenue": 123.45,
                "Network Commission": 12.345,
                "Partner Payment": 111.105,
            }
        ]
    )
    store.save_history(tmp_path, "2026-09", {"music": line})
    store.save_history(tmp_path, "2026-10", {"music": line})
    store.save_history(tmp_path, "2026-10", {"music": line.assign(Revenue=200.0)})
    history = store.read_history(tmp_path)
    assert history["period"].tolist() == ["2026-09", "2026-10"] and history["Revenue"].tolist() == [123.45, 200.0]
    assert b"Alpha" not in (tmp_path / "history.csv.enc").read_bytes()

    monkeypatch.setenv("CMS_MASTER_KEY", "a-different-master-key-0123456789abcdef")
    with pytest.raises(security.KeyError_, match="different key"):
        store.read_history(tmp_path)

"""Command line interface: `cms-reporter <command>`."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd

from . import backup, conflicts, delivery, security, store, synth
from .analytics import AnalyticsProvider, DemoAnalytics, NoAnalytics, YouTubeAnalytics
from .config import ConfigError, NetworkConfig, Settings, load_config, slugify
from .ingest import IngestError
from .pipeline import PeriodResult, run_period, write_outputs
from .settlement import PeriodError

pd.set_option("display.width", 200, "display.max_columns", 20, "display.float_format", "{:,.2f}".format)


def analytics_provider(name: str) -> AnalyticsProvider:
    if name == "demo":
        return DemoAnalytics()
    if name == "youtube":
        secret, owner = os.environ.get("YT_CLIENT_SECRET_FILE"), os.environ.get("YT_CONTENT_OWNER_ID")
        if not secret or not owner:
            sys.exit("--analytics youtube needs YT_CLIENT_SECRET_FILE and YT_CONTENT_OWNER_ID in the environment.")
        return YouTubeAnalytics(Path(secret), owner)
    return NoAnalytics()


def print_summary(result: PeriodResult) -> None:
    print(f"\n=== {result.period} ===")
    print(f"Total revenue: ${result.total_usd:,.2f}", end="")
    print(f"   FX: {result.fx_rate:,.4f} {result.currency}/USD" if result.fx_rate else "   FX: not set")
    for v in result.verticals.values():
        print(f"\n-- {v.label} --")
        print(v.settlement.to_string(index=False) if not v.settlement.empty else "(no identified revenue)")
        if not v.unidentified.empty:
            print(
                f"\n!! {len(v.unidentified)} unidentified asset(s) worth ${v.unidentified_revenue:,.2f} "
                f"- see `cms-reporter unidentified {result.period}`"
            )


def cmd_keygen(args, settings, cfg) -> None:
    print(security.generate_master_key())


def cmd_serve(args, settings, cfg) -> None:
    import uvicorn

    security.master_key()
    port = int(os.environ.get("PORT", args.port))  # hosting platforms inject PORT
    uvicorn.run("cms_reporter.api:app", host=args.host, port=port, log_level="info")


def cmd_synth(args, settings, cfg) -> None:
    for period in synth.recent_periods(args.end, args.months):
        print("wrote", synth.generate_period(settings, cfg, period))


def cmd_run(args, settings, cfg) -> None:
    security.master_key()  # fail before doing any work if the key is missing
    for period in [args.period] if args.period else settings.periods():
        result = run_period(settings, cfg, period)
        print_summary(result)
        if not args.no_write:
            written = write_outputs(settings, cfg, result, analytics_provider(args.analytics))
            print(f"\nWrote {len(written) - 1} client package(s) and {written['internal']}")


def cmd_unidentified(args, settings, cfg) -> None:
    result = run_period(settings, cfg, args.period)
    for v in result.verticals.values():
        print(f"\n-- {v.label}: {len(v.unidentified)} unidentified asset(s), ${v.unidentified_revenue:,.2f} --")
        if not v.unidentified.empty:
            print(v.unidentified.to_string(index=False))
    print(
        "\nLook each asset up in CMS, then either fix its label there, add a rule to the network config, "
        'or run:\n  cms-reporter assign <vertical> <asset-id> "<producer>"'
    )


def cmd_assign(args, settings, cfg) -> None:
    vertical = cfg.verticals.get(args.vertical) or sys.exit(f"Unknown vertical. Choose from {list(cfg.verticals)}")
    if args.producer not in vertical.producer_names:
        sys.exit(f"{args.producer!r} is not a {vertical.label} producer. Choose from: {vertical.producer_names}")
    store.add_override(settings.store, args.vertical, args.asset_id, args.producer, args.note)
    print(f"{args.asset_id} -> {args.producer} ({vertical.label}). Re-run the period to apply.")


def cmd_passwords(args, settings, cfg) -> None:
    print(f"{'(internal workbooks)':32} {security.password_for(security.INTERNAL)}")
    for name in cfg.producer_names:
        print(f"{name:32} {security.password_for(slugify(name))}")


def cmd_deliver(args, settings, cfg) -> None:
    for item in delivery.deliver(settings, cfg, args.period, send=args.send, only=args.only):
        print(f"{item.status:28} {item.producer:28} -> {item.recipient or '-'}  ({len(item.files)} files)")
    if not args.send:
        print("\nDry run. Nothing was sent; add --send to e-mail these.")


def cmd_backup(args, settings, cfg) -> None:
    if args.demo:
        source: backup.ChannelSource = backup.DemoSource(cfg)
    else:
        key = os.environ.get("YOUTUBE_API_KEY") or sys.exit("Set YOUTUBE_API_KEY, or use --demo for synthetic data.")
        source = backup.YouTubeDataSource(key)
    folder, changes = backup.snapshot(settings, cfg, source, include_videos=not args.no_videos)
    print(f"Snapshot written to {folder}")
    print(changes.to_string(index=False) if not changes.empty else "No changes since the previous snapshot.")


def cmd_conflicts(args, settings, cfg) -> None:
    if args.demo:
        print("wrote", conflicts.write_demo_conflicts(settings))
    notifiers = conflicts.notifiers_from_env()
    while True:
        fresh = conflicts.check(settings, conflicts.sources_from_env(settings), notifiers)
        print(f"[conflicts] {len(fresh)} new, sent via {[n.name for n in notifiers]}")
        if not args.watch:
            return
        time.sleep(args.interval)


def cmd_demo(args, settings, cfg) -> None:
    """Everything, end to end, on synthetic data."""
    security.master_key()
    periods = synth.recent_periods(args.end, args.months)
    for period in periods:
        synth.generate_period(settings, cfg, period)
        result = run_period(settings, cfg, period)
        write_outputs(settings, cfg, result, DemoAnalytics())
        print(
            f"{period}: ${result.total_usd:,.2f} across {len(result.combined)} clients, "
            f"${result.unidentified_revenue:,.2f} unidentified"
        )
    print_summary(result)
    folder, _ = backup.snapshot(settings, cfg, backup.DemoSource(cfg))
    print(f"\nChannel snapshot: {folder}")
    conflicts.write_demo_conflicts(settings)
    conflicts.check(settings, [conflicts.CsvFolderSource(settings.conflicts_inbox)], [conflicts.ConsoleNotifier()])
    print(f"Outputs are in {settings.output}. Passwords: `cms-reporter passwords`.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cms-reporter", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_: str) -> argparse.ArgumentParser:
        command = sub.add_parser(name, help=help_)
        command.set_defaults(func=func)
        return command

    add("keygen", cmd_keygen, "print a new master key for CMS_MASTER_KEY")

    serve = add("serve", cmd_serve, "start the web interface")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    for name, func, help_ in (
        ("synth", cmd_synth, "generate synthetic CMS exports"),
        ("demo", cmd_demo, "generate synthetic data and run everything on it"),
    ):
        command = add(name, func, help_)
        command.add_argument(
            "--end", default=str(pd.Period.now("M") - 1), help="last month, YYYY-MM (default: last month)"
        )
        command.add_argument("--months", type=int, default=6)

    run = add("run", cmd_run, "settle a month and write encrypted outputs")
    run.add_argument("period", nargs="?", help="YYYY-MM (default: every month in the inbox)")
    run.add_argument(
        "--analytics", choices=["none", "demo", "youtube"], default="none", help="audience data for the PDF reports"
    )
    run.add_argument("--no-write", action="store_true", help="print the settlement only")

    add("unidentified", cmd_unidentified, "list assets that could not be attributed").add_argument("period")

    assign = add("assign", cmd_assign, "attribute an asset to a producer from now on")
    assign.add_argument("vertical")
    assign.add_argument("asset_id")
    assign.add_argument("producer")
    assign.add_argument("--note", default="")

    add("passwords", cmd_passwords, "show the file passwords derived from the master key")

    deliver = add("deliver", cmd_deliver, "e-mail client packages (dry run by default)")
    deliver.add_argument("period")
    deliver.add_argument("--send", action="store_true")
    deliver.add_argument("--only", help="a single producer name")

    snap = add("backup", cmd_backup, "snapshot channel metadata")
    snap.add_argument("--demo", action="store_true", help="synthetic channels instead of the YouTube API")
    snap.add_argument("--no-videos", action="store_true")

    alert = add("conflicts", cmd_conflicts, "announce new copyright/licence conflicts")
    alert.add_argument("--demo", action="store_true", help="drop a synthetic issue export in the inbox first")
    alert.add_argument("--watch", action="store_true", help="keep checking")
    alert.add_argument("--interval", type=int, default=900, help="seconds between checks with --watch")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = Settings()
    try:
        cfg: NetworkConfig | None = None if args.command == "keygen" else load_config(settings.config_path)
        args.func(args, settings, cfg)
    except (ConfigError, IngestError, PeriodError, security.KeyError_, FileNotFoundError, ValueError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()

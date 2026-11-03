from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .alerts import Alerts
from .config import load_config
from .dashboard import write_dashboard
from .filters import Profile
from .http import Http
from .models import SourceError
from .pipeline import load_state, run_check, save_state
from .sources import build_source

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="radar", description="Watch career sites for new matching jobs.")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Check every site, send alerts, and rebuild the dashboard.")
    run.add_argument("--no-alerts", action="store_true", help="Check without sending anything.")
    sub.add_parser("doctor", help="Test that every site can be read, and print the results.")
    sub.add_parser("test-alert", help="Send a test message to your alert channels.")
    check = sub.add_parser("check", help="See how a job title and location would be treated.")
    check.add_argument("title")
    check.add_argument("location", nargs="?", default="Atlanta, GA")
    check.add_argument("--description", default="")

    args = parser.parse_args(argv)
    config = load_config(args.root)
    http = Http()

    if args.command == "check":
        return _check(config, args)
    if args.command == "doctor":
        return _doctor(config, http)
    if args.command == "test-alert":
        return _test_alert(http)
    return _run(config, http, args)


def _run(config, http, args) -> int:
    state_path = args.root / "state" / "jobs.json"
    state = load_state(state_path)
    alerts = Alerts.from_env(http)
    summary = run_check(config, state, http, alerts, send=not args.no_alerts)
    save_state(state_path, state)
    write_dashboard(args.root / "site", config, state)
    _report(summary, config, state, alerts)
    # Failed sites are reported in the summary. They should not fail the run, so
    # state still saves and the dashboard still updates.
    return 0


def _report(summary, config, state, alerts):
    open_jobs = sum(1 for j in state["jobs"].values() if j["status"] == "open")
    lines = [
        f"Read {summary['ok']} of {len(config.sources)} sources ({summary['jobs_read']} postings).",
        f"Open matches: {open_jobs}. New: {len(summary['new'])}. Closed: {len(summary['closed'])}.",
    ]
    if not alerts.enabled:
        lines.append("Alerts are off: no DISCORD_WEBHOOK_URL or NTFY_TOPIC is set.")
    if summary.get("alerted"):
        lines.append(f"Alerts sent for {summary['alerted']} jobs.")
    if summary.get("alert_error"):
        lines.append(f"Alerts failed and will retry next run: {summary['alert_error']}")
    for item in summary["failed"]:
        lines.append(f"Could not read {item['source']}: {item['error']}")
    text = "\n".join(lines)
    print(text)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a") as handle:
            handle.write("\n\n".join(lines) + "\n")


def _doctor(config, http) -> int:
    profile = Profile(config.profile)

    def probe(source):
        try:
            jobs = source.fetch()
            matches = sum(1 for j in jobs if profile.title_decision(j.title).keep)
            return source.config, len(jobs), matches, None
        except SourceError as exc:
            return source.config, 0, 0, str(exc)
        except Exception as exc:
            return source.config, 0, 0, f"{type(exc).__name__}: {exc}"

    sources = [build_source(c, http, profile) for c in config.sources]
    with ThreadPoolExecutor(max_workers=config.settings.get("workers", 8)) as pool:
        results = list(pool.map(probe, sources))

    rows = ["| Source | Result | Postings | Title matches |", "| --- | --- | --- | --- |"]
    failures = 0
    for cfg, total, matches, error in results:
        if error:
            failures += 1
            rows.append(f"| {cfg.company} | Failed: {error[:120]} | | |")
        else:
            rows.append(f"| {cfg.company} | OK | {total} | {matches} |")
    rows.append(f"\n{len(results) - failures} of {len(results)} sources are working.")
    text = "\n".join(rows)
    print(text)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a") as handle:
            handle.write(text + "\n")
    return 1 if failures else 0


def _test_alert(http) -> int:
    alerts = Alerts.from_env(http)
    if not alerts.enabled:
        print("No alert channel is set. Add DISCORD_WEBHOOK_URL or NTFY_TOPIC.")
        return 1
    alerts.send_jobs(
        [
            {
                "company": "Job Radar",
                "title": "Test alert: your alerts are working",
                "location": "Atlanta, GA",
                "url": os.environ.get("DASHBOARD_URL") or "https://github.com",
                "tier": "fit",
                "tags": ["test"],
                "years": None,
            }
        ]
    )
    print("Test alert sent.")
    return 0


def _check(config, args) -> int:
    decision = Profile(config.profile).classify(args.title, args.location, args.description)
    if decision.keep:
        print(f"Keep as {decision.tier}. Location: {decision.location_class}. Notes: {decision.tags or 'none'}. Years: {decision.years}")
    else:
        print(f"Hide: {decision.reason}.")
    return 0

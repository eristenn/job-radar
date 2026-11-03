from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .filters import Profile
from .models import SourceError, is_safe_url
from .sources import build_source

STATE_VERSION = 1
ALERT_MAX_AGE_DAYS = 3
REJECT_TTL_DAYS = 60


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"version": STATE_VERSION, "jobs": {}, "rejected": {}, "sources": {}}


def save_state(path: Path, state: dict) -> None:
    """Write sorted JSON, so the file only changes when something real changes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def run_check(config, state, http, alerts, now=None, send=True) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    profile = Profile(config.profile)
    settings = config.settings
    sources = [build_source(c, http, profile) for c in config.sources]
    _apply_current_filters(state, profile, today)

    with ThreadPoolExecutor(max_workers=settings.get("workers", 8)) as pool:
        outcomes = list(pool.map(_fetch, sources))

    summary = {"new": [], "closed": [], "failed": [], "ok": 0, "jobs_read": 0}
    seen_by_source = {}
    healthy = set()

    for source, (jobs, error) in zip(sources, outcomes):
        health = state["sources"].setdefault(
            source.config.id,
            {"company": source.config.company, "failures": 0, "seeded": False, "warned": False},
        )
        health["company"] = source.config.company
        if error:
            health["failures"] += 1
            health["last_error"] = error[:200]
            summary["failed"].append({"source": source.config.company, "error": error[:200]})
            continue

        health["failures"] = 0
        health.pop("last_error", None)
        health["last_ok"] = today
        health["count"] = len(jobs)
        if source.complete:
            healthy.add(source.config.id)
        summary["ok"] += 1
        summary["jobs_read"] += len(jobs)
        seen_by_source[source.config.id] = {job.key for job in jobs}

        for job in jobs:
            _ingest(job, source, state, profile, health, now, today, summary)
        health["seeded"] = True

    _mark_closed(state, seen_by_source, healthy, today, settings, summary)
    _prune_rejected(state, now)

    if send:
        _send_job_alerts(state, alerts, profile, now, summary)
        _send_health_notices(state, alerts, settings)
    return summary


def _apply_current_filters(state, profile, today):
    """Drop saved jobs that no longer pass, so filter changes also clean up old matches."""
    for key, job in list(state["jobs"].items()):
        if job.get("status") != "open":
            continue
        decision = profile.classify(job["title"], job["location"], trusted_role=job.get("trusted", True))
        if not decision.keep:
            del state["jobs"][key]
            state["rejected"][key] = today


def _fetch(source):
    try:
        return source.fetch(), None
    except SourceError as exc:
        return [], str(exc)
    except Exception as exc:  # One broken source must never stop the others.
        return [], f"{type(exc).__name__}: {exc}"


def _ingest(job, source, state, profile, health, now, today, summary):
    known = state["jobs"].get(job.key)
    if known:
        if source.config.id not in known["sources"]:
            known["sources"] = sorted(known["sources"] + [source.config.id])
        if known.get("status") == "closed":
            known["status"] = "open"
            known.pop("closed_on", None)
        known.pop("missed", None)
        return

    if job.key in state["rejected"] or not is_safe_url(job.url):
        return

    strict = source.config.strict_location
    trusted = bool(job.extra.get("trusted_role"))
    first = profile.classify(job.title, job.location, job.description, strict_location=False, trusted_role=trusted)
    if not first.keep:
        return  # Title and location rejections are cheap to repeat, so they are not stored.

    try:
        source.enrich(job)
    except SourceError:
        pass  # Keep the job without extra detail rather than miss it.

    decision = profile.classify(job.title, job.location, job.description, strict_location=strict, trusted_role=trusted)
    if not decision.keep:
        state["rejected"][job.key] = today
        return

    state["jobs"][job.key] = {
        "key": job.key,
        "company": job.company,
        "title": job.title,
        "location": job.location,
        "url": job.url,
        "posted": job.posted,
        "first_seen": now.isoformat(timespec="minutes"),
        "tier": decision.tier,
        "tags": decision.tags,
        "years": decision.years,
        "status": "open",
        "trusted": trusted,
        "sources": [source.config.id],
        # The first read of any source only sets a baseline, so it never floods you.
        "alerted": None if health["seeded"] else "baseline",
    }
    if health["seeded"]:
        summary["new"].append(state["jobs"][job.key])


def _mark_closed(state, seen_by_source, healthy, today, settings, summary):
    limit = settings.get("close_after_missed_checks", 2)
    for job in state["jobs"].values():
        if job.get("status") != "open":
            continue
        if not all(s in healthy for s in job["sources"]):
            continue  # Never close a job because its source had a bad or partial run.
        if any(seen_by_source.get(s) == set() for s in job["sources"]):
            continue  # A source that suddenly returns nothing is suspect, not proof.
        if any(job["key"] in seen_by_source.get(s, ()) for s in job["sources"]):
            continue
        job["missed"] = job.get("missed", 0) + 1
        if job["missed"] >= limit:
            job["status"] = "closed"
            job["closed_on"] = today
            job.pop("missed", None)
            summary["closed"].append(job)


def _prune_rejected(state, now):
    cutoff = (now - timedelta(days=REJECT_TTL_DAYS)).date().isoformat()
    state["rejected"] = {k: v for k, v in state["rejected"].items() if v >= cutoff}


def _send_job_alerts(state, alerts, profile, now, summary):
    cutoff = (now - timedelta(days=ALERT_MAX_AGE_DAYS)).isoformat(timespec="minutes")
    pending = []
    for job in state["jobs"].values():
        if job["status"] != "open" or job.get("alerted") or job["tier"] not in profile.alert_tiers:
            continue
        if job["first_seen"] < cutoff:
            job["alerted"] = "expired"  # Too old to be useful when alerts are first turned on.
            continue
        pending.append(job)
    if not pending or not alerts.enabled:
        return
    pending.sort(key=lambda j: (j["tier"] != "fit", j["first_seen"]))
    try:
        alerts.send_jobs(pending)
    except SourceError as exc:
        summary["alert_error"] = str(exc)  # They stay pending and retry on the next run.
        return
    for job in pending:
        job["alerted"] = now.isoformat(timespec="minutes")
    summary["alerted"] = len(pending)


def _send_health_notices(state, alerts, settings):
    if not alerts.enabled:
        return
    limit = settings.get("warn_after_failures", 6)
    broke = [h for h in state["sources"].values() if h["failures"] >= limit and not h.get("warned")]
    fixed = [h for h in state["sources"].values() if h["failures"] == 0 and h.get("warned")]
    if broke:
        names = ", ".join(h["company"] for h in broke[:15])
        try:
            alerts.send_notice(f"Job Radar can't read these sites, so it could miss new roles there: {names}.")
            for health in broke:
                health["warned"] = True
        except SourceError:
            pass
    if fixed:
        names = ", ".join(h["company"] for h in fixed[:15])
        try:
            alerts.send_notice(f"Job Radar is reading these sites again: {names}.")
            for health in fixed:
                health["warned"] = False
        except SourceError:
            pass

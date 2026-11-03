from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import is_safe_url

TEMPLATE = Path(__file__).with_name("dashboard.html")


def build_payload(config, state, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    fields = ("key", "company", "title", "location", "url", "posted", "first_seen", "tier", "tags", "years")
    jobs = [
        {name: job.get(name) for name in fields}
        for job in state["jobs"].values()
        if job["status"] == "open" and is_safe_url(job["url"])
    ]
    jobs.sort(key=lambda j: j["first_seen"], reverse=True)
    health = [
        {
            "company": cfg.company,
            "ok": state["sources"].get(cfg.id, {}).get("failures", 1) == 0,
            "error": state["sources"].get(cfg.id, {}).get("last_error", "Not checked yet"),
            "count": state["sources"].get(cfg.id, {}).get("count"),
        }
        for cfg in config.sources
    ]
    return {
        "checked": now.isoformat(timespec="minutes"),
        "jobs": jobs,
        "health": health,
        "manual": [m for m in config.manual if is_safe_url(m.get("url", ""))],
    }


def write_dashboard(out_dir: Path, config, state, now=None) -> Path:
    payload = json.dumps(build_payload(config, state, now))
    # Job text comes from outside sites, so it must not be able to close the script tag.
    payload = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "index.html"
    target.write_text(TEMPLATE.read_text().replace("__RADAR_DATA__", payload))
    return target

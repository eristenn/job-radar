from __future__ import annotations

import os
import unicodedata

from .models import SourceError

TIER_LABEL = {"fit": "Good fit", "stretch": "Stretch"}
TIER_COLOR = {"fit": 0x0F7B6C, "stretch": 0xB7791F}
BATCH = 10  # Discord allows 10 embeds per message.
MAX_DETAILED = 30


class Alerts:
    def __init__(self, http, discord_url=None, ntfy_topic=None, dashboard_url=None):
        self.http = http
        self.discord_url = discord_url
        self.ntfy_topic = ntfy_topic
        self.dashboard_url = dashboard_url

    @classmethod
    def from_env(cls, http):
        return cls(
            http,
            discord_url=os.environ.get("DISCORD_WEBHOOK_URL") or None,
            ntfy_topic=os.environ.get("NTFY_TOPIC") or None,
            dashboard_url=os.environ.get("DASHBOARD_URL") or None,
        )

    @property
    def enabled(self):
        return bool(self.discord_url or self.ntfy_topic)

    def send_jobs(self, jobs) -> None:
        """Send new-job alerts. Raises if every channel fails, so they retry next run."""
        if not jobs:
            return
        self._deliver(lambda: self._discord_jobs(jobs), lambda: self._ntfy_jobs(jobs))

    def send_notice(self, message: str) -> None:
        self._deliver(
            lambda: self._discord({"content": message[:1900]}),
            lambda: self._ntfy("Job Radar", message),
        )

    def _deliver(self, discord, ntfy):
        attempts = []
        if self.discord_url:
            attempts.append(discord)
        if self.ntfy_topic:
            attempts.append(ntfy)
        errors = []
        for attempt in attempts:
            try:
                attempt()
            except SourceError as exc:
                errors.append(str(exc).split("failed:")[-1].strip())
        if attempts and len(errors) == len(attempts):
            raise SourceError("Could not send alerts: " + "; ".join(errors))

    def _discord(self, payload):
        self.http.request("POST", self.discord_url, body=payload)

    def _discord_jobs(self, jobs):
        detailed = jobs[:MAX_DETAILED]
        for start in range(0, len(detailed), BATCH):
            embeds = [self._embed(job) for job in detailed[start : start + BATCH]]
            content = None
            if start == 0:
                noun = "role" if len(jobs) == 1 else "roles"
                content = f"**{len(jobs)} new {noun} just went live**"
            self._discord({"content": content, "embeds": embeds})
        extra = len(jobs) - len(detailed)
        if extra > 0:
            link = f": {self.dashboard_url}" if self.dashboard_url else "."
            self._discord({"content": f"Plus {extra} more on your dashboard{link}"})

    @staticmethod
    def _embed(job):
        fields = [
            {"name": "Location", "value": job["location"][:200] or "Not listed", "inline": True},
            {"name": "Match", "value": TIER_LABEL.get(job["tier"], job["tier"]), "inline": True},
        ]
        if job.get("years") is not None:
            fields.append({"name": "Experience asked", "value": f'{job["years"]}+ years', "inline": True})
        if job.get("tags"):
            fields.append({"name": "Notes", "value": ", ".join(job["tags"]), "inline": True})
        return {
            "title": job["title"][:250],
            "url": job["url"],
            "description": job["company"][:200],
            "color": TIER_COLOR.get(job["tier"], 0x4A5568),
            "fields": fields,
        }

    def _ntfy_jobs(self, jobs):
        lines = [f'{job["title"]} at {job["company"]} ({job["location"][:40]})' for job in jobs[:6]]
        if len(jobs) > 6:
            lines.append(f"Plus {len(jobs) - 6} more")
        click = jobs[0]["url"] if len(jobs) == 1 else self.dashboard_url
        noun = "role" if len(jobs) == 1 else "roles"
        self._ntfy(f"{len(jobs)} new {noun} live", "\n".join(lines), click)

    def _ntfy(self, title, message, click=None):
        headers = {"Title": _ascii(title), "Content-Type": "text/plain; charset=utf-8"}
        if click:
            headers["Click"] = click
        self.http.request_raw("POST", f"https://ntfy.sh/{self.ntfy_topic}", message.encode(), headers)


def _ascii(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()

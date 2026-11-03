from __future__ import annotations

from datetime import datetime, timezone

from ..models import Job, SourceError, canonical_key
from .base import Source


class SimplifySource(Source):
    """The community-maintained new-grad list on GitHub."""

    pause = 0

    def fetch(self):
        rows = self.http.get_json(self.config.url)
        if not isinstance(rows, list):
            raise SourceError("Simplify list was not in the expected format")
        jobs = {}
        for row in rows:
            if not row.get("active") or not row.get("is_visible", True):
                continue
            url, title = row.get("url"), row.get("title")
            if not url or not title:
                continue
            posted = row.get("date_posted")
            job = Job(
                key=canonical_key(url),
                company=row.get("company_name") or "Unknown",
                title=title.strip(),
                location="; ".join(row.get("locations") or []),
                url=url,
                source_id=self.config.id,
                posted=datetime.fromtimestamp(posted, timezone.utc).date().isoformat() if posted else "",
            )
            jobs[job.key] = job
        return list(jobs.values())

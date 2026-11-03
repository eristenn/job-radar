from __future__ import annotations

from ..models import Job, SourceError, canonical_key
from .base import Source


class CyberJobsSource(Source):
    """HackedRico's 2027 cybersecurity list, which sorts roles into security categories."""

    pause = 0

    def fetch(self):
        rows = self.http.get_json(self.config.url)
        if not isinstance(rows, list):
            raise SourceError("2027 cyber jobs list was not in the expected format")
        jobs = {}
        for row in rows:
            if row.get("closed"):
                continue
            url, title = row.get("url"), row.get("role")
            if not url or not title:
                continue
            job = Job(
                key=canonical_key(url),
                company=row.get("company") or "Unknown",
                title=title.strip(),
                location=row.get("location") or "",
                url=url,
                source_id=self.config.id,
                posted=row.get("date_added") or "",
                extra={"trusted_role": row.get("category") in self.profile.trusted_categories},
            )
            jobs[job.key] = job
        return list(jobs.values())

from __future__ import annotations

from ..models import Job, SourceError, workday_key
from .base import Source

PAGE_SIZE = 20  # Workday rejects or silently empties anything above 20.
JSON_HEADERS = {"Accept": "application/json", "Accept-Language": "en-US"}


class WorkdaySource(Source):
    def __init__(self, config, http, profile):
        super().__init__(config, http, profile)
        cxs = f"{config.base}/wday/cxs/{config.tenant}/{config.site}"
        self.list_url = f"{cxs}/jobs"
        self.detail_base = cxs
        self.public_base = f"{config.base}{config.prefix}/{config.site}" if config.prefix else f"{config.base}/en-US/{config.site}"

    def fetch(self):
        terms = [""] if self.config.all_jobs else self.profile.search_terms
        pages = self.profile.max_pages * (4 if self.config.all_jobs else 1)
        jobs = {}
        for term in terms:
            total = None
            for page in range(pages):
                offset = page * PAGE_SIZE
                body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": term}
                data = self.http.post_json(self.list_url, body, headers=JSON_HEADERS)
                if not isinstance(data, dict) or "jobPostings" not in data:
                    raise SourceError(f"{self.config.company}: Workday response had no job list")
                postings = data["jobPostings"] or []
                # Workday can send total only on the first page.
                if total is None:
                    total = data.get("total") or 0
                for posting in postings:
                    job = self._to_job(posting)
                    if job:
                        jobs[job.key] = job
                self.wait()
                if not postings or offset + PAGE_SIZE >= total:
                    break
            else:
                self.complete = False
        return list(jobs.values())

    def _to_job(self, posting):
        path = posting.get("externalPath")
        title = posting.get("title")
        if not path or not title:
            return None
        url = f"{self.public_base}{path}"
        return Job(
            key=workday_key(self.config.tenant, path.rstrip("/").rsplit("/", 1)[-1]),
            company=self.config.company,
            title=title.strip(),
            location=(posting.get("locationsText") or "").strip(),
            url=url,
            source_id=self.config.id,
            posted=posting.get("postedOn") or "",
            extra={"path": path},
        )

    def enrich(self, job):
        path = job.extra.get("path")
        if not path:
            return
        data = self.http.get_json(f"{self.detail_base}{path}", headers=JSON_HEADERS)
        info = data.get("jobPostingInfo", data) if isinstance(data, dict) else {}
        places = [info.get("location")] + list(info.get("additionalLocations") or [])
        places = [p for p in places if p]
        if places:
            job.location = "; ".join(places)
        remote = info.get("remoteType")
        if remote and "remote" in str(remote).lower() and "remote" not in job.location.lower():
            job.location = f"{job.location} (Remote)".strip()
        job.description = info.get("jobDescription") or ""
        job.posted = info.get("startDate") or info.get("postedOn") or job.posted
        self.wait()

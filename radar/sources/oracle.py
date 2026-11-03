from __future__ import annotations

from urllib.parse import quote

from ..models import Job, SourceError, canonical_key
from .base import Source

PAGE_SIZE = 25
SAFE = ";=,"


class OracleSource(Source):
    def __init__(self, config, http, profile):
        super().__init__(config, http, profile)
        self.api = f"{config.base}/hcmRestApi/resources/latest"
        self.public = f"{config.base}/hcmUI/CandidateExperience/en/sites/{config.site}/job"

    def _list_url(self, term, offset):
        keyword = f'"{term}"' if " " in term else term
        finder = (
            f"findReqs;siteNumber={self.config.site},keyword={keyword},limit={PAGE_SIZE},"
            f"offset={offset},sortBy=POSTING_DATES_DESC"
        )
        return (
            f"{self.api}/recruitingCEJobRequisitions?onlyData=true"
            f"&expand=requisitionList.secondaryLocations&finder={quote(finder, safe=SAFE)}"
        )

    def fetch(self):
        jobs = {}
        for term in self.profile.search_terms:
            for page in range(self.profile.max_pages):
                offset = page * PAGE_SIZE
                data = self.http.get_json(self._list_url(term, offset))
                items = data.get("items") if isinstance(data, dict) else None
                if items is None:
                    raise SourceError(f"{self.config.company}: Oracle response had no job list")
                search = items[0] if items else {}
                rows = search.get("requisitionList") or []
                for row in rows:
                    job = self._to_job(row)
                    if job:
                        jobs[job.key] = job
                self.wait()
                total = search.get("TotalJobsCount") or 0
                if not rows or offset + PAGE_SIZE >= total:
                    break
            else:
                self.complete = False
        return list(jobs.values())

    def _to_job(self, row):
        job_id, title = row.get("Id"), row.get("Title")
        if not job_id or not title:
            return None
        places = [row.get("PrimaryLocation")]
        places += [s.get("Name") for s in (row.get("secondaryLocations") or []) if isinstance(s, dict)]
        url = f"{self.public}/{job_id}"
        return Job(
            key=canonical_key(url),
            company=self.config.company,
            title=title.strip(),
            location="; ".join(p for p in places if p),
            url=url,
            source_id=self.config.id,
            posted=row.get("PostedDate") or "",
            description=row.get("ShortDescriptionStr") or "",
            extra={"id": job_id},
        )

    def enrich(self, job):
        finder = f'ById;Id="{job.extra["id"]}",siteNumber={self.config.site}'
        url = (
            f"{self.api}/recruitingCEJobRequisitionDetails?onlyData=true&expand=all"
            f"&finder={quote(finder, safe=SAFE)}"
        )
        data = self.http.get_json(url)
        items = data.get("items") if isinstance(data, dict) else None
        if items:
            info = items[0]
            sections = [
                info.get("ExternalDescriptionStr"),
                info.get("ExternalResponsibilitiesStr"),
                info.get("ExternalQualificationsStr"),
            ]
            text = " ".join(s for s in sections if s)
            if text:
                job.description = text
        self.wait()

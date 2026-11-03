from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET

from ..filters import strip_html
from ..models import Job, SourceError, canonical_key
from .base import Source

PAGE_SIZE = 50
PAGE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "User-Agent": "Mozilla/5.0 (compatible; job-radar/1.0; personal job alert tool)",
}
LINK = re.compile(
    r'<a\b[^>]*href=["\']([^"\']*/careers/JobDetail[/?][^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S
)


class AvatureSource(Source):
    """Avature sites have no JSON API, so try the RSS feed and then the results page."""

    def fetch(self):
        jobs = self._from_feed()
        if not jobs:
            jobs = self._from_pages()
        if not jobs:
            note = getattr(self, "last_page_note", "")
            raise SourceError(f"{self.config.company}: could not read any jobs from the Avature site ({note})")
        return jobs

    def _root(self):
        return f"{self.config.base}/en_US/careers"

    def _from_feed(self):
        try:
            raw = self.http.get_text(f"{self._root()}/SearchJobs/feed/?jobRecordsPerPage=1000", headers=PAGE_HEADERS)
            root = ET.fromstring(raw)
        except (SourceError, ET.ParseError):
            return []
        jobs = {}
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if title and link:
                job = self._job(title, link, item.findtext("pubDate") or "")
                jobs[job.key] = job
        return list(jobs.values())

    def _from_pages(self):
        jobs = {}
        self.last_page_note = ""
        for page in range(self.profile.max_pages * 4):
            url = f"{self._root()}/SearchJobs/?jobRecordsPerPage={PAGE_SIZE}&jobOffset={page * PAGE_SIZE}"
            text = self.http.get_text(url, headers=PAGE_HEADERS)
            before = len(jobs)
            self.last_page_note = f"{len(text)} bytes, {text.count('JobDetail')} job links"
            for href, label in LINK.findall(text):
                title = strip_html(label)
                if not title or title.lower() in {"apply", "view job", "read more"}:
                    continue
                link = html.unescape(href)
                if link.startswith("/"):
                    link = f"{self.config.base}{link}"
                job = self._job(title, link)
                jobs.setdefault(job.key, job)
            self.wait()
            if len(jobs) == before:
                break
        else:
            self.complete = False
        return list(jobs.values())

    def _job(self, title, url, posted=""):
        return Job(
            key=canonical_key(url),
            company=self.config.company,
            title=title,
            location=self.config.assume_location,
            url=url,
            source_id=self.config.id,
            posted=posted,
        )

    def enrich(self, job):
        job.description = strip_html(self.http.get_text(job.url, headers=PAGE_HEADERS))
        self.wait()

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


class SourceError(RuntimeError):
    """A source failed or returned something we could not understand."""


@dataclass
class Job:
    key: str
    company: str
    title: str
    location: str
    url: str
    source_id: str
    posted: str = ""
    description: str = ""
    extra: dict = field(default_factory=dict)


def canonical_key(url: str) -> str:
    """Build one ID for a job, so the same posting from two feeds merges.

    Workday links differ by locale, site name and location slug, but they end
    in the same requisition ID (for example .../Software-Engineer-I_R0112713).
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.split("/") if p]
    last = parts[-1] if parts else ""

    if host.endswith("myworkdayjobs.com") or host.endswith("myworkdaysite.com"):
        if host.endswith("myworkdaysite.com") and len(parts) > 1 and parts[0] == "recruiting":
            tenant = parts[1].lower()
        else:
            tenant = host.split(".")[0]
        return workday_key(tenant, last)

    if host.endswith("oraclecloud.com"):
        match = re.search(r"/job/(\d+)", parsed.path)
        if match:
            return f"oracle:{host.split('.')[0]}:{match.group(1)}"

    if "/careers/jobdetail" in parsed.path.lower():
        match = re.search(r"(\d+)/?$", parsed.path)
        if match:
            return f"avature:{host}:{match.group(1)}"

    return f"url:{host}{parsed.path.rstrip('/').lower()}"


def workday_key(tenant: str, slug: str) -> str:
    req = slug.rsplit("_", 1)[-1] if "_" in slug else slug
    # "JR185231-1" is a repost of JR185231, but "R-0000033866" is a whole ID.
    req = re.sub(r"^([A-Za-z]*\d+)-\d{1,2}$", r"\1", req)
    return f"workday:{tenant.lower()}:{req.lower()}"


def is_safe_url(url: str) -> bool:
    return urlparse(url or "").scheme in {"http", "https"}

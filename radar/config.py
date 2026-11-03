from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

LOCALE = re.compile(r"[a-z]{2}[-_][A-Za-z]{2}")


@dataclass
class SourceConfig:
    id: str
    company: str
    type: str
    base: str
    tenant: str = ""
    site: str = ""
    prefix: str = ""
    all_jobs: bool = False
    assume_location: str = ""
    strict_location: bool = False
    url: str = ""


@dataclass
class Config:
    sources: list[SourceConfig]
    manual: list[dict]
    profile: dict
    settings: dict = field(default_factory=dict)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_source(entry: dict) -> SourceConfig:
    """Work out the job system from the careers link, so adding a company only takes a URL."""
    url = entry["url"]
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    base = f"{parsed.scheme}://{parsed.netloc}"
    parts = [p for p in parsed.path.split("/") if p and not LOCALE.fullmatch(p)]
    kind = entry.get("type", "")
    tenant = entry.get("tenant", "")
    site = entry.get("site", "")
    prefix = ""

    if kind in {"simplify", "cyberjobs"}:
        pass
    elif kind == "workday" or host.endswith("myworkdayjobs.com"):
        kind = "workday"
        tenant = tenant or host.split(".")[0]
        site = site or parts[0]
    elif host.endswith("myworkdaysite.com"):
        kind = "workday"
        tenant, site = parts[1], parts[2]
        prefix = f"/recruiting/{tenant}"
    elif kind == "oracle" or host.endswith("oraclecloud.com"):
        kind = "oracle"
        site = site or parts[parts.index("sites") + 1]
    elif kind == "avature" or host.endswith("avature.net"):
        kind = "avature"
    else:
        raise ValueError(f"Could not tell which job system {url} uses. Add it under [[manual]] instead.")

    name = entry["name"]
    return SourceConfig(
        id=entry.get("id") or slug(f"{name}-{site}" if site else name),
        company=name,
        type=kind,
        base=base,
        tenant=tenant,
        site=site,
        prefix=prefix,
        all_jobs=bool(entry.get("all_jobs", False)),
        assume_location=entry.get("assume_location", ""),
        strict_location=bool(entry.get("strict_location", False)),
        url=url,
    )


def load_config(root: Path) -> Config:
    companies = tomllib.loads((root / "companies.toml").read_text())
    profile = tomllib.loads((root / "profile.toml").read_text())
    sources = [parse_source(entry) for entry in companies.get("company", [])]
    seen = set()
    for source in sources:
        if source.id in seen:
            raise ValueError(f"Duplicate source id: {source.id}")
        seen.add(source.id)
    return Config(
        sources=sources,
        manual=companies.get("manual", []),
        profile=profile,
        settings=profile.get("settings", {}),
    )

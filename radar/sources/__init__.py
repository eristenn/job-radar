from __future__ import annotations

from ..config import SourceConfig
from .avature import AvatureSource
from .cyberjobs import CyberJobsSource
from .oracle import OracleSource
from .simplify import SimplifySource
from .workday import WorkdaySource

TYPES = {
    "workday": WorkdaySource,
    "oracle": OracleSource,
    "avature": AvatureSource,
    "simplify": SimplifySource,
    "cyberjobs": CyberJobsSource,
}


def build_source(config: SourceConfig, http, profile):
    return TYPES[config.type](config, http, profile)

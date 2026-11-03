from __future__ import annotations

import time


class Source:
    """A place jobs come from."""

    pause = 0.35  # Seconds between requests, to stay polite to each employer's site.

    def __init__(self, config, http, profile):
        self.config = config
        self.http = http
        self.profile = profile
        # False when a search hit its page limit. Jobs past that point went
        # unseen, so their absence can't be read as the job closing.
        self.complete = True

    def fetch(self):
        raise NotImplementedError

    def enrich(self, job) -> None:
        """Add details from the job page. Optional, and failures are non-fatal."""

    def wait(self):
        if self.pause:
            time.sleep(self.pause)

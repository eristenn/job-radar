from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request

from .models import SourceError

USER_AGENT = "job-radar/1.0 (personal job alert tool)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class Http:
    def __init__(self, timeout: int = 45, retries: int = 3, backoff: float = 2.0):
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff

    def request(self, method, url, body=None, headers=None) -> bytes:
        headers = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        return self.request_raw(method, url, data, headers)

    def request_raw(self, method, url, data=None, headers=None) -> bytes:
        merged = {"User-Agent": USER_AGENT, "Accept-Language": "en-US"}
        merged.update(headers or {})

        last_error = "unknown error"
        for attempt in range(self.retries):
            req = urllib.request.Request(url, data=data, headers=merged, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                if exc.code not in RETRY_STATUS:
                    break
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                self._sleep(attempt, retry_after)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = str(getattr(exc, "reason", exc))
                self._sleep(attempt)
        raise SourceError(f"{method} {url.split('?')[0]} failed: {last_error}")

    def _sleep(self, attempt, retry_after=None):
        if attempt >= self.retries - 1:
            return
        try:
            delay = float(retry_after) if retry_after else self.backoff * (2**attempt)
        except ValueError:
            delay = self.backoff * (2**attempt)
        time.sleep(min(delay, 60) + random.random() * 0.25)

    def get_text(self, url, headers=None) -> str:
        return self.request("GET", url, headers=headers).decode("utf-8", "replace")

    def get_json(self, url, headers=None):
        return self._parse(self.request("GET", url, headers={"Accept": "application/json", **(headers or {})}), url)

    def post_json(self, url, body, headers=None):
        return self._parse(self.request("POST", url, body=body, headers={"Accept": "application/json", **(headers or {})}), url)

    @staticmethod
    def _parse(raw, url):
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise SourceError(f"{url.split('?')[0]} did not return JSON") from exc

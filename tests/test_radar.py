import json
import tempfile
import threading
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from radar.alerts import Alerts
from radar.config import Config, load_config, parse_source
from radar.dashboard import write_dashboard
from radar.filters import Profile, years_required
from radar.http import Http
from radar.models import SourceError, canonical_key
from radar.pipeline import load_state, run_check
from radar.sources import build_source

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DATA = tomllib.loads((ROOT / "profile.toml").read_text())
PROFILE = Profile(PROFILE_DATA)


class TitleFilter(unittest.TestCase):
    def keep(self, title):
        return PROFILE.title_decision(title)

    def test_target_roles_are_kept(self):
        for title in [
            "Cybersecurity Analyst I",
            "Associate Cybersecurity Analyst",
            "SOC Analyst",
            "Threat Intelligence Analyst",
            "Security Engineer",
            "Fraud Analyst",
            "Incident Response Analyst",
            "Rotational Technology Associate",
            "Technology and Cybersecurity Staff Audit I",
            "Vulnerability Management Analyst",
            "Identity & Access Analyst",
        ]:
            self.assertTrue(self.keep(title).keep, title)

    def test_wrong_or_senior_roles_are_hidden(self):
        for title in [
            "Senior Cybersecurity Analyst",
            "Sr. Security Engineer",
            "Cybersecurity Staff Analyst",
            "Principal SOC Analyst",
            "Cybersecurity Manager",
            "Cyber Threat Intelligence Lead",
            "Cybersecurity Analyst III",
            "Security Officer",
            "Physical Security Specialist",
            "Asset Protection Specialist",
            "Securities Trader",
            "Social Security Claims Representative",
            "Cybersecurity Account Executive",
            "Software Engineer",
            "Cyber Security Internship",
            "Chief Cybersecurity Risk Officer",
            "SoC Design Verification Engineer",
            "SoC Firmware Engineer",
        ]:
            self.assertFalse(self.keep(title).keep, title)

    def test_level_two_is_a_stretch(self):
        self.assertEqual(self.keep("Cybersecurity Analyst II").tier, "stretch")
        self.assertEqual(self.keep("Security Engineer 2").tier, "stretch")
        self.assertEqual(self.keep("Cybersecurity Analyst I").tier, "fit")

    def test_entry_level_is_tagged(self):
        self.assertIn("entry level", self.keep("Associate Security Analyst").tags)
        self.assertIn("entry level", self.keep("Cybersecurity Analyst I").tags)


class LocationFilter(unittest.TestCase):
    def place(self, location, title="Security Analyst", description=""):
        return PROFILE.location_class(title, location, description)

    def test_formats(self):
        self.assertEqual(self.place("Atlanta, GA"), "atlanta")
        self.assertEqual(self.place("USA - Georgia - Alpharetta - 30005"), "atlanta")
        self.assertEqual(self.place("Dallas, TX; Atlanta, GA"), "atlanta")
        self.assertEqual(self.place("Remote - United States"), "remote")
        self.assertEqual(self.place("Dallas, TX", title="Security Analyst (Remote)"), "remote")
        self.assertEqual(self.place("3 Locations"), "multiple")
        self.assertEqual(self.place("Dallas, TX"), "elsewhere")
        self.assertEqual(self.place("IN---TDC-1-IN110"), "elsewhere")
        self.assertEqual(self.place("Savannah, Georgia"), "unknown")
        self.assertEqual(self.place("Bengaluru"), "elsewhere")
        self.assertEqual(self.place("Building 7", description="<p>Based in Atlanta.</p>"), "atlanta")
        self.assertEqual(self.place("Remote (US); St. Louis, MO", title="Analyst I (Hybrid, St. Louis)"), "elsewhere")
        self.assertEqual(self.place("Atlanta, GA", title="Security Analyst (Hybrid)"), "atlanta")


class Experience(unittest.TestCase):
    def test_lowest_required_figure_wins(self):
        self.assertEqual(years_required("Requires 2 years of experience with a degree, or 6 years of experience without."), 2)
        self.assertEqual(years_required("<li>5+ years of security experience</li>"), 5)
        self.assertEqual(years_required("0-2 years of experience"), 0)
        self.assertEqual(years_required("Three years of relevant experience."), 3)
        self.assertEqual(years_required("Minimum of 3 (three) years experience"), 3)
        self.assertIsNone(years_required("We have been a leader for 40 years."))
        self.assertIsNone(years_required(""))

    def test_required_beats_preferred(self):
        self.assertEqual(years_required("Requires 4 years of experience. 1 year of Splunk experience preferred."), 4)

    def test_tiers_from_description(self):
        hidden = PROFILE.classify("Security Analyst", "Atlanta, GA", "Requires 7 years of experience.")
        self.assertFalse(hidden.keep)
        stretch = PROFILE.classify("Security Analyst", "Atlanta, GA", "Requires 3 years of experience.")
        self.assertEqual(stretch.tier, "stretch")
        fit = PROFILE.classify("Security Analyst", "Atlanta, GA", "Requires 1 year of experience.")
        self.assertEqual(fit.tier, "fit")
        clear = PROFILE.classify("Security Analyst", "Atlanta, GA", "Must hold an active TS/SCI clearance.")
        self.assertEqual(clear.tier, "stretch")
        self.assertIn("active clearance", clear.tags)


class Keys(unittest.TestCase):
    def test_same_workday_job_merges_across_links(self):
        a = "https://truist.wd1.myworkdayjobs.com/en-US/Careers/job/Richmond-VA/Software-Engineer-I_R0112713"
        b = "https://truist.wd1.myworkdayjobs.com/careers/job/Software-Engineer-I_R0112713?source=x"
        self.assertEqual(canonical_key(a), canonical_key(b))

    def test_workday_repost_suffix_merges(self):
        a = "https://elevancehealth.wd1.myworkdayjobs.com/ELV-ET/job/NY/Intern_JR185231"
        b = "https://elevancehealth.wd1.myworkdayjobs.com/en-US/ANT/job/NY/Intern_JR185231-1"
        self.assertEqual(canonical_key(a), canonical_key(b))

    def test_hyphenated_ids_stay_whole(self):
        a = canonical_key("https://rb.wd5.myworkdayjobs.com/FRS/job/Atlanta-GA/Job-A_R-0000033866")
        b = canonical_key("https://rb.wd5.myworkdayjobs.com/FRS/job/Atlanta-GA/Job-B_R-0000033867")
        self.assertNotEqual(a, b)
        self.assertEqual(a, "workday:rb:r-0000033866")
        self.assertEqual(
            canonical_key("https://x.wd1.myworkdayjobs.com/S/job/A/Job_JR-0111003"), "workday:x:jr-0111003"
        )

    def test_oracle_and_avature(self):
        self.assertEqual(
            canonical_key("https://emje.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/18118"),
            "oracle:emje:18118",
        )
        self.assertEqual(
            canonical_key("https://delta.avature.net/en_US/careers/JobDetail/Security-Analyst/123"),
            "avature:delta.avature.net:123",
        )
        self.assertEqual(
            canonical_key("https://delta.avature.net/en_US/careers/JobDetail?jobId=32774"),
            "avature:delta.avature.net:32774",
        )


class ConfigFile(unittest.TestCase):
    def test_real_config_loads_and_links_parse(self):
        config = load_config(ROOT)
        self.assertGreater(len(config.sources), 40)
        by_name = {s.company: s for s in config.sources}
        self.assertEqual(by_name["The Home Depot"].type, "workday")
        self.assertEqual(by_name["The Home Depot"].tenant, "homedepot")
        self.assertEqual(by_name["Southern Company"].type, "oracle")
        self.assertEqual(by_name["Southern Company"].site, "SouthernCompanyJobs")
        self.assertEqual(by_name["American Express"].type, "oracle")
        self.assertEqual(config.sources[-1].type, "simplify", "community lists run after employer sites")
        self.assertEqual(by_name["Delta Air Lines"].type, "avature")
        self.assertEqual(by_name["Wells Fargo"].tenant, "wf")
        self.assertEqual(by_name["Wells Fargo"].prefix, "/recruiting/wf")

    def test_unknown_site_gets_a_clear_error(self):
        with self.assertRaises(ValueError):
            parse_source({"name": "X", "url": "https://careers.example.com/jobs"})


class FakeSites(BaseHTTPRequestHandler):
    """Behaves like Workday, Oracle, Avature and Discord, including their limits."""

    protocol_version = "HTTP/1.1"
    state = {}

    def log_message(self, *args):
        pass

    def _send(self, code, body, kind="application/json"):
        if isinstance(body, bytes):
            raw = body
        elif isinstance(body, str):
            raw = body.encode()
        else:
            raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        path = urlparse(self.path).path
        if path == "/discord":
            if self.state.get("discord_down"):
                return self._send(403, {"error": "denied"})
            self.state.setdefault("discord", []).append(body)
            return self._send(204, b"")
        if path == "/wday/cxs/acme/Careers/jobs":
            if self.state.get("workday_down"):
                return self._send(404, {"error": "gone"})
            if body["limit"] > 20:
                return self._send(400, {"error": "limit"})
            self.state.setdefault("workday_terms", []).append(body["searchText"])
            jobs = self.state.get("workday_jobs", [])
            page = jobs[body["offset"] : body["offset"] + body["limit"]]
            # Real Workday can leave out or zero the total after page one.
            total = len(jobs) if body["offset"] == 0 else 0
            return self._send(200, {"total": total, "jobPostings": page, "facets": []})
        return self._send(404, {})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path.startswith("/wday/cxs/acme/Careers/job/"):
            if "Accept" not in self.headers or "json" not in self.headers["Accept"]:
                return self._send(200, "<html>shell</html>", "text/html")
            return self._send(200, self.state["details"][path.rsplit("/", 1)[-1]])
        if path == "/hcmRestApi/resources/latest/recruitingCEJobRequisitions":
            return self._send(200, {"items": [{"TotalJobsCount": 1, "requisitionList": [
                {"Id": "555", "Title": "Cyber Security Analyst", "PrimaryLocation": "Atlanta, GA, United States",
                 "PostedDate": "2026-11-01", "secondaryLocations": [{"Name": "Birmingham, AL"}],
                 "ShortDescriptionStr": "Short"}]}]})
        if path == "/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails":
            return self._send(200, {"items": [{"ExternalQualificationsStr": "Requires 1 year of experience."}]})
        if path == "/en_US/careers/SearchJobs/feed/":
            if self.state.get("avature_mode") == "html":
                return self._send(403, "blocked", "text/plain")
            return self._send(200, (
                '<?xml version="1.0"?><rss><channel><item><title>Associate Security Analyst</title>'
                f'<link>http://127.0.0.1:{self.server.server_port}/en_US/careers/JobDetail/Associate-Security-Analyst/42</link>'
                '<pubDate>Mon, 02 Nov 2026</pubDate></item></channel></rss>'), "application/rss+xml")
        if path == "/en_US/careers/SearchJobs/":
            if "jobOffset=0" in parsed.query:
                return self._send(200, ('<article><a class="link" href="/en_US/careers/JobDetail/SOC-Analyst/77">'
                                        '<span>SOC Analyst</span></a></article>'
                                        '<article><a href="/en_US/careers/JobDetail?jobId=88">Fraud Analyst</a></article>'), "text/html")
            return self._send(200, "<html></html>", "text/html")
        if path.startswith("/en_US/careers/JobDetail/"):
            return self._send(200, "<p>Requires 2 years of experience.</p>", "text/html")
        if path == "/cyber.json":
            return self._send(200, self.state.get("cyber", []))
        if path == "/simplify.json":
            return self._send(200, self.state.get("simplify", []))
        return self._send(404, {})


def posting(req, title="Cybersecurity Analyst I", where="Atlanta, GA"):
    return {"title": title, "externalPath": f"/job/Atlanta/{title.replace(' ', '-')}_{req}", "locationsText": where, "postedOn": "Posted Today"}


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSites)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeSites.state = {"details": {}}
        self.http = Http(timeout=5, retries=1)
        self.now = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
        self.state = load_state(Path("/nonexistent"))
        self.alerts = Alerts(self.http, discord_url=f"{self.base}/discord")

    def config(self, *entries):
        sources = [parse_source(e) for e in entries]
        return Config(sources=sources, manual=[], profile=PROFILE_DATA, settings={"workers": 2, "warn_after_failures": 2})

    def workday(self):
        return {"name": "Acme", "type": "workday", "tenant": "acme", "site": "Careers", "url": f"{self.base}/Careers"}

    def run_once(self, config, minutes=0):
        return run_check(config, self.state, self.http, self.alerts, now=self.now + timedelta(minutes=minutes))

    def detail(self, req, title="Cybersecurity Analyst I", location="Atlanta, GA", description="<p>Requires 1 year of experience.</p>", extra=None):
        FakeSites.state["details"][f"{title.replace(' ', '-')}_{req}"] = {
            "jobPostingInfo": {"title": title, "location": location, "jobDescription": description,
                               "startDate": "2026-11-02", "additionalLocations": extra or []}}

    def test_first_run_sets_baseline_then_only_new_jobs_alert(self):
        FakeSites.state["workday_jobs"] = [posting("R1")]
        self.detail("R1")
        config = self.config(self.workday())
        first = self.run_once(config)
        self.assertEqual(first["new"], [])
        self.assertNotIn("discord", FakeSites.state)
        self.assertEqual(self.state["jobs"]["workday:acme:r1"]["alerted"], "baseline")

        FakeSites.state["workday_jobs"].append(posting("R2", "Security Engineer"))
        self.detail("R2", "Security Engineer")
        second = self.run_once(config, 30)
        self.assertEqual(len(second["new"]), 1)
        sent = FakeSites.state["discord"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["embeds"][0]["title"], "Security Engineer")

        self.run_once(config, 60)
        self.assertEqual(len(FakeSites.state["discord"]), 1, "must not alert twice")

    def test_paging_reads_past_page_one_even_when_total_disappears(self):
        FakeSites.state["workday_jobs"] = [posting(f"R{i}", f"Security Analyst {i}") for i in range(10, 55)]
        profile = Profile(PROFILE_DATA)
        source = build_source(parse_source(self.workday()), self.http, profile)
        source.pause = 0
        jobs = source.fetch()
        self.assertEqual(len(jobs), 45)

    def test_multiple_locations_are_resolved_from_the_job_page(self):
        config = self.config(self.workday())
        FakeSites.state["workday_jobs"] = []
        self.run_once(config)
        FakeSites.state["workday_jobs"] = [posting("R3", where="2 Locations"), posting("R4", "SOC Analyst", where="2 Locations")]
        self.detail("R3", location="Dallas, TX", extra=["Atlanta, GA"])
        self.detail("R4", "SOC Analyst", location="Dallas, TX", extra=["Denver, CO"])
        self.run_once(config, 30)
        self.assertIn("workday:acme:r3", self.state["jobs"])
        self.assertIn("Atlanta", self.state["jobs"]["workday:acme:r3"]["location"])
        self.assertNotIn("workday:acme:r4", self.state["jobs"])
        self.assertIn("workday:acme:r4", self.state["rejected"], "rejected once, so the page is not refetched")

    def test_experience_filter_uses_job_description(self):
        config = self.config(self.workday())
        FakeSites.state["workday_jobs"] = [posting("R5")]
        self.detail("R5", description="<li>8+ years of experience required</li>")
        self.run_once(config)
        self.assertNotIn("workday:acme:r5", self.state["jobs"])

    def test_failed_alert_retries_and_is_not_lost(self):
        config = self.config(self.workday())
        FakeSites.state["workday_jobs"] = []
        self.run_once(config)
        FakeSites.state["workday_jobs"] = [posting("R6")]
        self.detail("R6")
        FakeSites.state["discord_down"] = True
        summary = self.run_once(config, 30)
        self.assertIn("alert_error", summary)
        self.assertNotIn("/discord", summary["alert_error"], "the secret webhook link must never reach logs")
        self.assertIsNone(self.state["jobs"]["workday:acme:r6"]["alerted"])
        FakeSites.state["discord_down"] = False
        self.run_once(config, 60)
        self.assertEqual(FakeSites.state["discord"][0]["embeds"][0]["title"], "Cybersecurity Analyst I")
        self.assertIsNotNone(self.state["jobs"]["workday:acme:r6"]["alerted"])

    def test_job_closes_only_after_clean_misses_and_can_reopen(self):
        config = self.config(self.workday())
        FakeSites.state["workday_jobs"] = [posting("R7"), posting("R8", "SOC Analyst")]
        self.detail("R7")
        self.detail("R8", "SOC Analyst")
        self.run_once(config)
        FakeSites.state["workday_jobs"] = [posting("R8", "SOC Analyst")]
        self.run_once(config, 30)
        self.assertEqual(self.state["jobs"]["workday:acme:r7"]["status"], "open")
        self.run_once(config, 60)
        self.assertEqual(self.state["jobs"]["workday:acme:r7"]["status"], "closed")
        FakeSites.state["workday_jobs"] = [posting("R7"), posting("R8", "SOC Analyst")]
        self.run_once(config, 90)
        self.assertEqual(self.state["jobs"]["workday:acme:r7"]["status"], "open")
        self.assertNotIn("discord", FakeSites.state, "a reopened job is not new")

    def test_outage_never_closes_jobs_and_warns_once(self):
        config = self.config(self.workday())
        FakeSites.state["workday_jobs"] = [posting("R9")]
        self.detail("R9")
        self.run_once(config)
        FakeSites.state["workday_down"] = True
        for step in range(1, 5):
            summary = self.run_once(config, 30 * step)
            self.assertEqual(len(summary["failed"]), 1)
        self.assertEqual(self.state["jobs"]["workday:acme:r9"]["status"], "open")
        notices = [m for m in FakeSites.state["discord"] if "can't read" in (m.get("content") or "")]
        self.assertEqual(len(notices), 1)
        FakeSites.state["workday_down"] = False
        self.run_once(config, 300)
        self.assertTrue(any("again" in (m.get("content") or "") for m in FakeSites.state["discord"]))

    def test_empty_response_never_closes_jobs(self):
        config = self.config(self.workday())
        FakeSites.state["workday_jobs"] = [posting("R10")]
        self.detail("R10")
        self.run_once(config)
        FakeSites.state["workday_jobs"] = []
        for step in range(1, 4):
            self.run_once(config, 30 * step)
        self.assertEqual(self.state["jobs"]["workday:acme:r10"]["status"], "open")

    def test_oracle_source(self):
        entry = {"name": "Southern", "type": "oracle", "site": "CX_1001", "url": f"{self.base}/"}
        source = build_source(parse_source(entry), self.http, PROFILE)
        source.pause = 0
        jobs = source.fetch()
        self.assertEqual(len(jobs), 1)
        self.assertIn("Birmingham", jobs[0].location)
        source.enrich(jobs[0])
        self.assertIn("1 year", jobs[0].description)

    def test_avature_feed_and_html_fallback(self):
        entry = {"name": "Delta", "type": "avature", "url": f"{self.base}/en_US/careers", "assume_location": "Atlanta, GA"}
        source = build_source(parse_source(entry), self.http, PROFILE)
        source.pause = 0
        self.assertEqual(source.fetch()[0].title, "Associate Security Analyst")
        FakeSites.state["avature_mode"] = "html"
        jobs = source.fetch()
        self.assertEqual(jobs[0].title, "SOC Analyst")
        self.assertTrue(jobs[0].url.startswith(self.base))
        self.assertEqual(jobs[1].title, "Fraud Analyst")
        self.assertTrue(jobs[1].url.endswith("jobId=88"))

    def test_feeds_merge_into_one_job(self):
        wd_url = f"https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/Atlanta/Cybersecurity-Analyst-I_R11"
        FakeSites.state["simplify"] = [{"active": True, "is_visible": True, "url": wd_url, "title": "Cybersecurity Analyst I",
                                         "company_name": "Acme", "locations": ["Atlanta, GA"], "date_posted": 1793664000}]
        FakeSites.state["workday_jobs"] = [posting("R11")]
        self.detail("R11")
        config = self.config(self.workday(), {"name": "Simplify", "type": "simplify", "id": "s", "url": f"{self.base}/simplify.json", "strict_location": True})
        self.run_once(config)
        self.assertEqual(len(self.state["jobs"]), 1)
        self.assertEqual(sorted(self.state["jobs"]["workday:acme:r11"]["sources"]), ["acme-careers", "s"])

    def test_cyber_list_uses_categories_and_skips_closed_roles(self):
        FakeSites.state["cyber"] = [
            {"company": "CrowdStrike", "role": "Analyst I, Falcon Complete", "location": "Remote (US)", "type": "earlycareer",
             "category": "SOC & Detection", "url": "https://a.example/1", "date_added": "2026-10-01"},
            {"company": "Delinea", "role": "Support Engineer I", "location": "Remote (US)", "type": "earlycareer",
             "category": "Engineering @ Security Co", "url": "https://a.example/2", "date_added": "2026-10-01"},
            {"company": "Truist", "role": "Associate Security Engineer", "location": "Atlanta, GA", "type": "earlycareer",
             "category": "Security Engineering", "url": "https://a.example/3", "date_added": "2026-09-16", "closed": True},
            {"company": "Acme", "role": "Senior SOC Analyst", "location": "Atlanta, GA", "type": "earlycareer",
             "category": "SOC & Detection", "url": "https://a.example/4", "date_added": "2026-10-01"},
        ]
        config = self.config({"name": "Cyber", "type": "cyberjobs", "id": "c", "url": f"{self.base}/cyber.json", "strict_location": True})
        self.run_once(config)
        titles = [j["title"] for j in self.state["jobs"].values()]
        self.assertEqual(titles, ["Analyst I, Falcon Complete"])

    def test_partial_search_never_closes_jobs(self):
        data = dict(PROFILE_DATA)
        data["search"] = {"terms": ["cybersecurity"], "max_pages_per_term": 1}
        config = Config(sources=[parse_source(self.workday())], manual=[], profile=data, settings={"workers": 1})
        FakeSites.state["workday_jobs"] = [posting("R12")] + [posting(f"R{i}", f"Fraud Analyst {i}") for i in range(20, 45)]
        self.detail("R12")
        for i in range(20, 45):
            self.detail(f"R{i}", f"Fraud Analyst {i}")
        self.run_once(config)
        FakeSites.state["workday_jobs"] = [posting(f"R{i}", f"Fraud Analyst {i}") for i in range(20, 45)]
        self.run_once(config, 30)
        self.run_once(config, 60)
        self.assertEqual(self.state["jobs"]["workday:acme:r12"]["status"], "open")

    def test_inactive_and_unsafe_links_are_ignored(self):
        FakeSites.state["simplify"] = [
            {"active": False, "url": "https://a.example/1", "title": "SOC Analyst", "company_name": "A", "locations": ["Atlanta, GA"]},
            {"active": True, "url": "javascript:alert(1)", "title": "SOC Analyst", "company_name": "B", "locations": ["Atlanta, GA"]},
        ]
        config = self.config({"name": "Simplify", "type": "simplify", "id": "s", "url": f"{self.base}/simplify.json", "strict_location": True})
        self.run_once(config)
        self.assertEqual(self.state["jobs"], {})

    def test_dashboard_cannot_be_broken_by_job_text(self):
        hostile = {
            "key": "k", "company": "</script><script>alert(1)</script>", "title": "x", "location": "Atlanta",
            "url": "https://example.com/1", "posted": "", "first_seen": "2026-11-03T15:00+00:00",
            "tier": "fit", "tags": [], "years": None, "status": "open", "sources": [],
        }
        state = {"jobs": {"k": hostile}, "sources": {}, "rejected": {}}
        config = Config(sources=[], manual=[], profile=PROFILE_DATA)
        with tempfile.TemporaryDirectory() as folder:
            page = write_dashboard(Path(folder), config, state).read_text()
        self.assertNotIn("</script><script>alert(1)", page)


if __name__ == "__main__":
    unittest.main()

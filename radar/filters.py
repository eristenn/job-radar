from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

STATES = (
    "AL AK AZ AR CA CO CT DE FL HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
    "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
STATE_NAMES = (
    "alabama alaska arizona arkansas california colorado connecticut delaware florida hawaii idaho "
    "illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota "
    "mississippi missouri montana nebraska nevada ohio oklahoma oregon pennsylvania tennessee texas "
    "utah vermont virginia washington wisconsin wyoming"
).split() + ["new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota",
             "rhode island", "south carolina", "south dakota", "west virginia"]
US_CITIES = ["nyc", "new york city", "seattle", "chicago", "dallas", "houston", "austin", "boston", "denver", "san francisco"]
FOREIGN_PLACES = [
    "argentina", "australia", "austria", "belgium", "brazil", "bulgaria", "canada", "chile", "china", "colombia",
    "costa rica", "czech republic", "denmark", "egypt", "england", "finland", "france", "germany", "greece",
    "hong kong", "hungary", "india", "indonesia", "ireland", "israel", "italy", "japan", "jordan", "kuwait",
    "luxembourg", "malaysia", "mexico", "netherlands", "new zealand", "nigeria", "norway", "oman", "pakistan",
    "peru", "philippines", "poland", "portugal", "qatar", "romania", "saudi", "scotland", "singapore",
    "south africa", "south korea", "spain", "sweden", "switzerland", "taiwan", "thailand", "turkey", "uae",
    "ukraine", "united arab emirates", "united kingdom", "uk", "vietnam",
    "bengaluru", "bangalore", "hyderabad", "pune", "chennai", "gurgaon", "gurugram", "mumbai", "london", "toronto",
    "dublin", "cork", "krakow", "sydney", "melbourne", "riyadh", "jeddah", "dubai", "cairo", "tel aviv", "stuttgart",
    "napoli", "bogota", "manila", "buenos aires", "cordoba", "merida", "san jose, costa rica", "kuala lumpur", "paris", "berlin", "munich", "madrid", "zurich", "brussels", "bucharest", "tokyo",
]
FOREIGN_CODES = re.compile(r"\b(ROU|GBR|CAN|IRE|UK|AUS|DEU|FRA|IND|JPN|NZ|EMEA|APAC|LATAM)\b")
US_HINT = re.compile(r"\b(USA?|U\.S\.A?\.?|United States)\b")
WORD_YEARS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
PREFERRED = re.compile(r"\b(prefer|preferred|nice to have|bonus|plus\b|desired|ideally)", re.I)
YEARS = re.compile(
    r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b(?:\s*\(\w+\))?\s*"
    r"(?:(?:-|–|—|to)\s*\d{1,2}\s*)?\+?\s*(?:or more\s+)?(?:years?|yrs?)\b",
    re.I,
)
ACTIVE_CLEARANCE = re.compile(
    r"\b(active|current)\b[^.]{0,50}\b(clearance|secret|ts/sci)\b|\bts/sci\b|\btop secret\b", re.I
)
ANY_CLEARANCE = re.compile(r"\b(security clearance|clearance eligible|obtain[^.]{0,30}clearance)\b", re.I)


def _is_foreign(text: str) -> bool:
    low = text.lower()
    return any(re.search(rf"\b{re.escape(place)}\b", low) for place in FOREIGN_PLACES)


def strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text or "", flags=re.I | re.S)
    text = re.sub(r"</(p|li|div|h\d|tr)>|<br\s*/?>", ". ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def years_required(description: str) -> int | None:
    """Return the lowest years of experience a posting asks for.

    Using the lowest figure is deliberate: a posting that says "2 years with a
    degree, or 6 without" should stay visible, because wrongly hiding a job is
    worse than showing one extra.
    """
    text = strip_html(description)
    required, preferred = [], []
    for sentence in re.split(r"(?<=[.;:!?])\s+", text):
        if not re.search(r"experience|background|work history", sentence, re.I):
            continue
        for match in YEARS.finditer(sentence):
            raw = match.group(1).lower()
            years = WORD_YEARS.get(raw, int(raw) if raw.isdigit() else None)
            if years is None or years > 25:
                continue
            (preferred if PREFERRED.search(sentence) else required).append(years)
    if required:
        return min(required)
    return min(preferred) if preferred else None


@dataclass
class Decision:
    keep: bool
    tier: str = ""
    reason: str = ""
    tags: list[str] = field(default_factory=list)
    years: int | None = None
    location_class: str = ""


class Profile:
    def __init__(self, data: dict):
        roles = data["roles"]
        location = data["location"]
        self.include = self._any(roles["include"])
        self.exclude = self._any(roles["exclude"])
        self.levels = self._any(roles["too_senior"])
        self.stretch_titles = self._any(roles["stretch"])
        self.entry = self._any(roles["entry_level"])
        self.interns = self._any(roles["internship"])
        self.include_internships = roles.get("include_internships", False)
        self.trusted_categories = set(roles.get("trusted_categories", []))
        self.max_years = roles.get("hide_at_years", 5)
        self.stretch_years = roles.get("stretch_at_years", 3)
        self.metro = self._any([rf"\b{re.escape(c)}\b" for c in location["metro"]])
        self.remote = self._any(location["remote"])
        self.include_remote = location.get("include_remote", True)
        self.search_terms = data["search"]["terms"]
        self.max_pages = data["search"].get("max_pages_per_term", 5)
        self.alert_tiers = set(data.get("alerts", {}).get("tiers", ["fit", "stretch"]))

    @staticmethod
    def _any(patterns):
        return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)

    def title_decision(self, title: str, trusted_role: bool = False) -> Decision:
        if not trusted_role and not self.include.search(title):
            return Decision(False, reason="not a target role")
        if self.exclude.search(title):
            return Decision(False, reason="excluded role type")
        if self.levels.search(title):
            return Decision(False, reason="too senior")
        if self.interns.search(title) and not self.include_internships:
            return Decision(False, reason="internship")
        tags = ["entry level"] if self.entry.search(title) else []
        tier = "stretch" if self.stretch_titles.search(title) else "fit"
        return Decision(True, tier=tier, tags=tags)

    def location_class(self, title: str, location: str, description: str = "") -> str:
        text = location or ""
        if self.metro.search(text):
            return "atlanta"
        if re.search(r"\bhybrid\b", title, re.I):
            return "elsewhere"  # Hybrid somewhere outside the Atlanta area isn't really remote.
        if self.remote.search(text) or self.remote.search(title):
            return "remote" if self._us_remote(title, text) else "elsewhere"
        if re.fullmatch(r"\s*\d+\s+locations?\s*|\s*multiple locations?\s*", text, re.I):
            return "multiple"
        if self._names_a_place_elsewhere(text):
            return "elsewhere"
        if description and self.metro.search(strip_html(description)):
            return "atlanta"
        # Only a blank or US-labelled location is unclear. Anything else, like "Riyadh", isn't Atlanta.
        return "unknown" if not text.strip() or US_HINT.search(text) else "elsewhere"

    @staticmethod
    def _us_remote(title: str, text: str) -> bool:
        if US_HINT.search(text) or re.search(r"\b(" + "|".join(STATES) + r")\b", text):
            return True
        return not (_is_foreign(text) or FOREIGN_CODES.search(title) or _is_foreign(title))

    @staticmethod
    def _names_a_place_elsewhere(text: str) -> bool:
        # Georgia counts here because metro Atlanta towns already matched above.
        if re.search(r"\b(" + "|".join(STATES) + r"|GA)\b", text):
            return True
        low = text.lower()
        if "georgia" in low or _is_foreign(text):
            return True
        return any(re.search(rf"\b{re.escape(place)}\b", low) for place in STATE_NAMES + US_CITIES)

    def classify(self, title, location, description="", strict_location=False, trusted_role=False) -> Decision:
        decision = self.title_decision(title, trusted_role)
        if not decision.keep:
            return decision

        place = self.location_class(title, location, description)
        decision.location_class = place
        if place == "elsewhere":
            return Decision(False, reason="outside Atlanta", location_class=place)
        if place == "remote":
            if not self.include_remote:
                return Decision(False, reason="remote role", location_class=place)
            decision.tags.append("remote")
        if place in {"unknown", "multiple"}:
            if strict_location:
                return Decision(False, reason="location not confirmed", location_class=place)
            decision.tags.append("check location")

        if description:
            text = strip_html(description)
            years = years_required(text)
            decision.years = years
            if years is not None and years >= self.max_years:
                return Decision(False, reason=f"asks for {years}+ years", years=years, location_class=place)
            if years is not None and years >= self.stretch_years:
                decision.tier = "stretch"
            if ACTIVE_CLEARANCE.search(text):
                decision.tier = "stretch"
                decision.tags.append("active clearance")
            elif ANY_CLEARANCE.search(text):
                decision.tags.append("clearance eligible")
        return decision

#!/usr/bin/env python3
"""Monitor official pages every 12 hours; only explicit current-season dates are confirmed."""
from __future__ import annotations
import hashlib, json, os, re, urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "contests.json"
STATUS = ROOT / "data" / "monitor-status.json"
HISTORY = ROOT / "data" / "history.json"
UA = "CSContestWatch/8.0 (+https://github.com; official schedule monitor)"
SOURCES = {
    "USACO": ["https://usaco.org/"],
    "ACSL": ["https://www.acsl.org/Registration", "https://www.acsl.org/get-started/schedule"],
    "BCC": ["https://cemc.uwaterloo.ca/contests/bcc"],
    "CCC": ["https://cemc.uwaterloo.ca/contests/ccc"],
    "AIO": ["https://amt.edu.au/aio"],
    "NZIC": ["https://www.nzoi.org.nz/nzic/", "https://www.nzoi.org.nz/nzic/rules/"],
}

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts=[]
        self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"): self.hidden += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"): self.hidden = max(0, self.hidden-1)
    def handle_data(self, data):
        t=" ".join(data.split())
        if t and not self.hidden:
            self.parts.append(t)
    def text(self):
        return "\n".join(self.parts)

def read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback

def write_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)

def fetch(url):
    req=urllib.request.Request(url, headers={"User-Agent":UA, "Accept":"text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=35) as response:
        raw=response.read()
        charset=response.headers.get_content_charset() or "utf-8"
        try:
            source=raw.decode(charset, errors="replace")
        except LookupError:
            source=raw.decode("utf-8", errors="replace")
        parser=TextExtractor()
        parser.feed(source)
        text=parser.text()
        return {
            "url":url,
            "text":text,
            "sha256":hashlib.sha256(re.sub(r"\s+"," ",text).strip().encode("utf-8")).hexdigest(),
            "etag":response.headers.get("ETag"),
            "lastModified":response.headers.get("Last-Modified"),
            "httpStatus":response.status,
        }

def parse_month(token):
    months={"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
    return months[token[:3].lower()]

def current_season_year(now=None):
    return (now or datetime.now(timezone.utc)).year

MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"

def parse_usaco_schedule(text, year=None):
    year = year or current_season_year()
    heading = re.search(rf"{year}\s*[-–—/]\s*{year+1}\s+(?:Competition\s+)?Schedule(?!\s+Recap)", text, re.I)
    if not heading: return None
    block = text[heading.end():]
    stop = re.search(r"(?:20\d{2}\s*[-–—/]\s*20\d{2}\s+(?:Competition\s+)?Schedule|Our Mission|Our Sponsors|Finalists Announced|Excellent Results)", block, re.I)
    block = block[:stop.start()] if stop else block[:2500]
    pattern = rf"\b({MONTH})\s+(\d{{1,2}})(?:\s*[-–—]\s*(?:({MONTH})\s*)?(\d{{1,2}}))?\s*:\s*([^\n]+)"
    rows=[]
    for m in re.finditer(pattern, block, re.I):
        sm=parse_month(m[1]); em=parse_month(m[3]) if m[3] else sm
        try:
            start=datetime(year if sm>=9 else year+1,sm,int(m[2])).date().isoformat()
            end=datetime(year if em>=9 else year+1,em,int(m[4] or m[2])).date().isoformat()
        except ValueError as exc: raise ScheduleConflict(str(exc)) from exc
        if end < start: raise ScheduleConflict("Date range ends before it begins")
        label=m[5].strip(" .;")
        rows.append({"id":"usaco-"+re.sub(r"[^a-z0-9]+","-",label.lower()).strip("-"), "label":label,
                     "kind":"contest","start":start,"end":end,"source":SOURCES["USACO"][0],
                     "confirmed":True,"seasonYear":year})
    return rows or None

def parse_nzic_schedule(text, year=None):
    target=(year or current_season_year())+1
    # Never cross the historical/reference section into last year's date list.
    intro=re.search(rf"NZIC\s+{target}\b",text,re.I)
    if not intro: return None
    block=text[intro.start():]
    stop=re.search(r"(?:For reference|previous (?:year|season)|last (?:year|season)|NZIC\s+"+str(target-1)+r"\b)",block,re.I)
    if stop: block=block[:stop.start()]
    if re.search(r"schedule will be announced|dates? (?:are |is )?(?:TBD|to be announced)",block,re.I): return None
    # Official NZIC format: Monday 23 March - Sunday 29 March.
    day=r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+)?"
    pattern=rf"{day}(\d{{1,2}})\s+({MONTH})(?:\s+{target})?\s*[-–—]\s*{day}(\d{{1,2}})\s+({MONTH})(?:\s+{target})?"
    rows=[]
    for i,m in enumerate(re.finditer(pattern,block,re.I),1):
        try:
            start=datetime(target,parse_month(m[2]),int(m[1])).date().isoformat()
            end=datetime(target,parse_month(m[4]),int(m[3])).date().isoformat()
        except ValueError as exc: raise ScheduleConflict(str(exc)) from exc
        if end < start: raise ScheduleConflict("Date range ends before it begins")
        rows.append({"id":f"nzic-r{i}","label":f"Round {i}","kind":"contest","start":start,"end":end,
                     "source":SOURCES["NZIC"][0],"confirmed":True,"seasonYear":target-1})
    if len(rows) == 3:
        if any(a["end"] >= b["start"] for a, b in zip(rows, rows[1:])):
            raise ScheduleConflict("NZIC rounds overlap or are out of order")
        return rows
    return None

class ScheduleConflict(ValueError):
    """An official date could not be applied without contradicting another date."""


def season_label(key, year):
    return str(year + 1) if key == "NZIC" else f"{year}–{year + 1}"


def nzic_estimate(text, year):
    target = year + 1
    match = re.search(
        rf"\bRegistration for NZIC {target} is expected to open in ({MONTH}) {target}\b",
        text, re.I,
    )
    if not match:
        return None
    return {
        "id": "nzic-open", "label": "Registration expected to open", "kind": "expected",
        "month": f"{target}-{parse_month(match[1]):02d}", "seasonYear": year,
        "source": SOURCES["NZIC"][0],
    }


def event_value(event):
    if not event or event.get("reference") or event.get("kind") in ("reference", "unknown"):
        return None
    if event.get("kind") == "expected":
        if not event.get("month") and not event.get("start"):
            return None
        state = "expected"
    elif event.get("start"):
        state = "confirmed"
    else:
        return None
    return {
        "status": state, "start": event.get("start"), "end": event.get("end") or event.get("start"),
        "month": event.get("month"),
    }


def event_key(event):
    key = event["id"]
    # Normalize the old label-derived US Open ID without changing stored dates.
    if re.match(r"^usaco-(?:us-)?open(?:-|$)", key):
        return "usaco-open"
    ordinal = re.fullmatch(r"usaco-(first|second|third|fourth)-contest", key)
    if ordinal:
        number = ("first", "second", "third", "fourth").index(ordinal[1]) + 1
        return f"usaco-round-{number}"
    numbered = re.fullmatch(r"usaco-(?:contest|round)-(\d+)", key)
    if numbered:
        return f"usaco-round-{int(numbered[1])}"
    return key


def event_season_year(key, event, default=None):
    if event.get("seasonYear"):
        return event["seasonYear"]
    if key == "NZIC" and event.get("month"):
        return int(event["month"][:4]) - 1
    if event.get("start"):
        year, month, _ = event["start"].split("-")
        return int(year) - (key == "NZIC" or int(month) < 9)
    return default


def event_update(key, year, event, before, url, checked_at):
    after = event_value(event)
    if not after or after == before:
        return None
    change_type = "date_adjusted" if before and before["status"] == "confirmed" else (
        "estimate_updated" if after["status"] == "expected" else "date_published"
    )
    season = season_label(key, year)
    # Ignore page hashes, run time and presentation wording. Retain distinct date transitions.
    identity = [key, season, event_key(event), change_type, before, after]
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {
        "id": fingerprint, "contest": key, "season": season, "type": "verified_event_update",
        "changeType": change_type, "event": event_key(event), "label": event["label"],
        "before": before, "after": after, "url": url, "detectedAt": checked_at,
    }


def clean_history(history):
    cleaned = {}
    for item in history:
        if item.get("type") == "verified_event_update":
            entries = [item]
        elif item.get("type") == "schedule_updated":
            # Migrate genuine legacy date changes, including updates from older seasons.
            key = item["contest"]
            prior = {event_key(e): e for e in item.get("before", [])}
            entries = []
            for event in item.get("after", []):
                year = event_season_year(key, event)
                if not year:
                    continue
                old = prior.get(event_key(event))
                before = event_value(old) if old and event_season_year(key, old, year) == year else None
                record = event_update(key, year, event, before, item["url"], item["detectedAt"])
                if record:
                    entries.append(record)
        else:
            # Generic page changes and parsing diagnostics belong in monitor-status, not public history.
            continue
        for record in entries:
            existing = cleaned.get(record["id"])
            if not existing or record["detectedAt"] < existing["detectedAt"]:
                cleaned[record["id"]] = record
    return sorted(cleaned.values(), key=lambda item: item["detectedAt"], reverse=True)


def schedule_context(key, text, year):
    """Return evidence that a page contains a current schedule or an explicit pending notice."""
    if key == "USACO":
        current = re.search(rf"{year}\s*[-–—/]\s*{year + 1}\s+(?:Competition\s+)?Schedule(?!\s+Recap)", text, re.I)
        return bool(current), bool(current and re.search(r"to be announced|coming soon|\bTBD\b", text[current.end():current.end()+150], re.I))
    current = re.search(rf"\bNZIC\s+{year + 1}\b", text, re.I)
    pending = re.search(r"schedule will be announced|dates? (?:are |is )?(?:TBD|to be announced)", text, re.I)
    return bool(current), bool(pending)


def apply_verified(contest, year, schedule, estimate, history, checked_at):
    old_events = contest.get("events", [])
    key = contest["key"]
    previous_year = contest.get("scheduleSeasonYear", year)
    previous = {event_key(e): e for e in old_events}
    old_estimate = previous.get("nzic-open")
    if (estimate and old_estimate and old_estimate.get("kind") != "expected"
            and event_season_year(key, old_estimate, previous_year) == year):
        estimate = None
    incoming = [*(schedule or []), *([estimate] if estimate else [])]
    new_entries = []
    for event in incoming:
        old = previous.get(event_key(event))
        before = None
        if old and event_season_year(key, old, previous_year) == year:
            before = event_value(old)
        entry = event_update(key, year, event, before, event["source"], checked_at)
        if entry:
            new_entries.append(entry)
    if schedule:
        # Replace dates only after a valid parse. Preserve independently maintained NZIC registration.
        events = list(schedule)
        if previous_year == year:
            published = {event_key(e) for e in schedule}
            events.extend(e for e in old_events if event_value(e) and event_key(e) not in published and e["id"] != "nzic-open")
        if key == "NZIC" and (estimate or "nzic-open" in previous):
            events.insert(0, estimate or previous["nzic-open"])
        if events != old_events:
            contest.setdefault("scheduleArchive", []).append({"replacedAt": checked_at, "events": old_events})
            contest["events"] = events
            contest["scheduleSeasonYear"] = year
            contest["season"] = f"{season_label(key, year)} 官方赛程已发布"
            contest["shortSeason"] = contest["season"]
    elif estimate:
        old = previous.get("nzic-open")
        if not old or event_value(old) != event_value(estimate) or event_season_year(key, old, previous_year) != year:
            if old and event_season_year(key, old, previous_year) != year:
                contest.setdefault("scheduleArchive", []).append({"replacedAt": checked_at, "events": [old]})
            contest["events"] = [e for e in old_events if e["id"] != "nzic-open"] + [estimate]
    seen = {item["id"] for item in history}
    added = []
    for entry in new_entries:
        if entry["id"] not in seen:
            history.insert(0, entry)
            seen.add(entry["id"])
            added.append(entry["id"])
    return added


def check_contest(contest, urls, previous, history, now, checked_at):
    key = contest["key"]
    pages = dict(previous.get("pages", {}))
    errors = []
    new_ids = []

    def warn(kind, url, error):
        errors.append({"type": kind, "url": url, "checkedAt": checked_at, "error": str(error)[:400]})
        print(f"{key} {kind}: {url}: {error}")

    for url in urls:
        try:
            result = fetch(url)
        except (OSError, ValueError) as exc:
            warn("fetch_error", url, exc)
            continue
        old = pages.get(url, {})
        changed = bool(old.get("sha256") and old["sha256"] != result["sha256"])
        result.update(changed=changed, checkedAt=checked_at, changeState="detected_change" if changed else "unchanged")
        pages[url] = result
        if key not in ("USACO", "NZIC") or url != urls[0]:
            continue
        parser = parse_usaco_schedule if key == "USACO" else parse_nzic_schedule
        relevant, pending = schedule_context(key, result["text"], now.year)
        estimate = nzic_estimate(result["text"], now.year) if key == "NZIC" else None
        schedule = None
        try:
            schedule = parser(result["text"], now.year)
            if schedule:
                keys = [event_key(e) for e in schedule]
                if len(set(keys)) != len(keys):
                    raise ScheduleConflict("The source lists the same event more than once")
                rounds = sorted((e for e in schedule if event_key(e).startswith("usaco-round-")), key=lambda e: e["start"])
                if any(a["end"] >= b["start"] for a, b in zip(rounds, rounds[1:])):
                    raise ScheduleConflict("USACO rounds overlap")
            elif relevant and not pending:
                warn("parse_error", url, "Current-season schedule dates could not be parsed")
            elif not relevant and not pending:
                # A recognizable schedule can disappear during a site redesign.
                old_relevant, _ = schedule_context(key, old.get("text", ""), now.year)
                if old_relevant or any(e.get("confirmed") and e.get("seasonYear") == now.year for e in contest["events"]):
                    warn("parse_error", url, "Current-season schedule section could not be found")
        except ScheduleConflict as exc:
            warn("date_conflict", url, exc)
            schedule = None
        evidence = {
            "season": season_label(key, now.year), "scheduleSection": relevant, "pending": pending,
            "events": {event_key(event): event_value(event) for event in (schedule or [])},
            "estimate": event_value(estimate),
        }
        result["scheduleEvidence"] = evidence
        if evidence != old.get("scheduleEvidence") and (relevant or estimate or old.get("scheduleEvidence", {}).get("scheduleSection")):
            result["changeState"] = "relevant_change"
        added = apply_verified(contest, now.year, schedule, estimate, history, checked_at)
        if added:
            result["changeState"] = "verified_event_update"
            new_ids.extend(added)
    return {
        "checkedAt": checked_at, "pages": pages, "errors": errors,
        "errorHistory": previous.get("errorHistory", []) + errors,
    }, new_ids


def main(now=None):
    now = now or datetime.now(timezone.utc)
    checked_at = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    data = read_json(DATA, None)
    if not data or not data.get("contests"):
        raise ValueError("Cannot read contest data")
    status = read_json(STATUS, {"sources": {}})
    history = clean_history(read_json(HISTORY, data.get("history", [])))
    sources = status.setdefault("sources", {})
    latest = []
    checked = False
    for contest in data["contests"]:
        key = contest["key"]
        if key not in SOURCES:
            continue
        previous = sources.get(key, {})
        if status.get("historySchema") == 2 and previous.get("checkedAt") and os.getenv("GITHUB_EVENT_NAME") != "schedule":
            last = datetime.fromisoformat(previous["checkedAt"].replace("Z", "+00:00"))
            if now - last < timedelta(hours=12):
                continue
        sources[key], added = check_contest(contest, SOURCES[key], previous, history, now, checked_at)
        latest.extend(added)
        checked = True

    if checked:
        data["lastChecked"] = checked_at
        data["latestUpdateIds"] = latest
        status["lastChecked"] = checked_at
    data["history"] = history[:80]
    data["monitorIssues"] = [
        {"contest": key, "type": err.get("type", "fetch_error"), "url": err["url"], "checkedAt": err["checkedAt"]}
        for key, source in sources.items() for err in source.get("errors", [])
    ]
    status["historySchema"] = 2
    status["scheduleParser"] = "Verified current-season USACO / NZIC schedule changes"
    status["frequencyMinutes"] = {key: 720 for key in SOURCES}
    write_json(HISTORY, history)
    write_json(DATA, data)
    write_json(STATUS, status)
    print(f"Monitor finished at {checked_at}; {len(latest)} new verified updates, {len(history)} archived")


if __name__ == "__main__":
    main()

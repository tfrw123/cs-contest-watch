#!/usr/bin/env python3
"""Monitor official pages every 12 hours; only explicit current-season dates are confirmed."""
from __future__ import annotations
import hashlib, json, re, urllib.request
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
    except Exception:
        return fallback

def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
        except ValueError: return None
        if end < start: return None
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
        except ValueError: return None
        if end < start: return None
        rows.append({"id":f"nzic-r{i}","label":f"Round {i}","kind":"contest","start":start,"end":end,
                     "source":SOURCES["NZIC"][0],"confirmed":True,"seasonYear":target-1})
    return rows if len(rows)==3 else None

def main():
    now=datetime.now(timezone.utc)
    now_iso=now.isoformat(timespec="seconds").replace("+00:00","Z")
    data=read_json(DATA, {"season":"2026–2027","contests":[],"history":[]})
    status=read_json(STATUS, {"lastChecked":None,"sources":{}})
    history=read_json(HISTORY, [])
    source_state=status.setdefault("sources",{})
    by_key={c["key"]:c for c in data.get("contests",[])}

    for key, urls in SOURCES.items():
        previous=source_state.get(key,{})
        if previous.get("checkedAt"):
            try:
                last=datetime.fromisoformat(previous["checkedAt"].replace("Z","+00:00"))
                if now-last < timedelta(hours=12):
                    continue
            except Exception:
                pass
        page_results=[]
        for url in urls:
            try:
                result=fetch(url)
                old=previous.get("pages",{}).get(url,{})
                changed=bool(old.get("sha256") and old.get("sha256")!=result["sha256"])
                if changed:
                    history.insert(0,{
                        "id":f"{key}-{now.strftime('%Y%m%dT%H%M%SZ')}",
                        "contest":key,"type":"official_page_changed",
                        "detectedAt":now_iso,"url":url,
                        "message":f"{key} 官方页面内容发生变化；请核对是否涉及报名或赛程日期。",
                        "previousHash":old.get("sha256"),"currentHash":result["sha256"],
                        "lastModified":result.get("lastModified")
                    })
                result["changed"]=changed
                result["checkedAt"]=now_iso
                page_results.append(result)
            except Exception as exc:
                page_results.append({"url":url,"checkedAt":now_iso,"error":str(exc)[:400]})

        pages=dict(previous.get("pages",{}))
        pages.update({p["url"]:p for p in page_results if "sha256" in p})
        errors=[p for p in page_results if "error" in p]
        source_state[key]={"checkedAt":now_iso,"pages":pages,"errors":errors,"errorHistory":(previous.get("errorHistory",[])+errors)}
        for error in errors: print(f"{key}: {error['url']}: {error['error']}")

        if key in ("USACO","NZIC"):
            page=next((p for p in page_results if p.get("url")==urls[0] and "text" in p),None)
            parser=parse_usaco_schedule if key=="USACO" else parse_nzic_schedule
            parsed=parser(page["text"],now.year) if page else None
            contest=by_key.get(key)
            if parsed and contest is not None:
                old_events=contest.get("events",[])
                if key=="NZIC":
                    parsed=[e for e in old_events if e.get("id")=="nzic-open"]+parsed
                if old_events!=parsed:
                    history.insert(0,{"id":f"{key}-schedule-{now.strftime('%Y%m%dT%H%M%SZ')}",
                        "contest":key,"type":"schedule_updated","detectedAt":now_iso,"url":urls[0],
                        "message":f"{key} {now.year}–{now.year+1} 官方赛程已同步。",
                        "before":old_events,"after":parsed})
                    # Preserve superseded events for reference/audit without presenting them as confirmed.
                    contest.setdefault("scheduleArchive",[]).append({"replacedAt":now_iso,"events":old_events})
                    contest["events"]=parsed
                    contest["scheduleSeasonYear"]=now.year
                    contest["season"]=f"{now.year}–{now.year+1} 官方赛程已发布"
                    contest["shortSeason"]=contest["season"]

    # Keep a bounded, newest-first audit trail.
    # Retain all historical records.
    data["lastChecked"]=now_iso
    data["history"]=history[:80]
    status["lastChecked"]=now_iso
    status["scheduleParser"]="Dynamic current-season USACO / NZIC explicit-date parsers"
    status["frequencyMinutes"]={key:720 for key in SOURCES}
    write_json(DATA,data)
    write_json(STATUS,status)
    write_json(HISTORY,history)
    print(f"Monitor finished at {now_iso}; history entries={len(history)}")

if __name__=="__main__":
    main()


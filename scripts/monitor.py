#!/usr/bin/env python3
"""Official contest-site monitor for CS Contest Watch v8.

USACO is checked on every workflow run (scheduled every 30 minutes).
The other five official sites are checked at most once every six hours.
USACO season dates are parsed only from a matching 2026-2027 schedule heading.
Unknown dates are never inferred from previous seasons.
"""
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
    def handle_data(self, data):
        t=" ".join(data.split())
        if t:
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

def season_year(month):
    # The 2026-2027 academic season's winter/spring events occur in 2027.
    return 2026 if month >= 9 else 2027

def parse_usaco_schedule(text):
    # Require an explicit season heading. Never parse the previous season as a fallback.
    heading=re.search(r"2026\s*[-–/]\s*2027\s+(?:Competition\s+)?Schedule", text, re.I)
    if not heading:
        return None
    tail=text[heading.end():]
    # Restrict parsing to the current schedule block.
    stop=re.search(r"(?:2027\s*[-–/]\s*2028\s+Schedule|Our Mission|Excellent Results|2026\s+Finalists)", tail, re.I)
    block=tail[:stop.start()] if stop else tail[:2500]
    rows=[]
    line_pattern=re.compile(
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})"
        r"(?:\s*[-–]\s*(?:(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*)?(\d{1,2}))?"
        r"\s*:\s*([^\n]+)", re.I)
    for match in line_pattern.finditer(block):
        sm=parse_month(match.group(1)); sd=int(match.group(2))
        em=parse_month(match.group(3)) if match.group(3) else sm
        ed=int(match.group(4)) if match.group(4) else sd
        label=match.group(5).strip(" .;")
        sy=season_year(sm); ey=season_year(em)
        start=f"{sy:04d}-{sm:02d}-{sd:02d}"
        end=f"{ey:04d}-{em:02d}-{ed:02d}"
        kind="contest"
        low=label.lower()
        if "open" in low: kind="contest"
        rows.append({
            "id":"usaco-"+re.sub(r"[^a-z0-9]+","-",label.lower()).strip("-"),
            "label":f"{label} · {start[5:]}–{end[5:]}" if start!=end else f"{label} · {start[5:]}",
            "kind":kind,"start":start,"end":end,
            "source":"https://usaco.org/"
        })
    return rows or None

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
        if key!="USACO" and previous.get("checkedAt"):
            try:
                last=datetime.fromisoformat(previous["checkedAt"].replace("Z","+00:00"))
                if now-last < timedelta(hours=6):
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

        pages={p["url"]:p for p in page_results if "sha256" in p}
        source_state[key]={"checkedAt":now_iso,"pages":pages,"errors":[p for p in page_results if "error" in p]}

        if key=="USACO":
            page=next((p for p in page_results if p.get("url")=="https://usaco.org/" and "text" in p),None)
            parsed=parse_usaco_schedule(page["text"]) if page else None
            if parsed:
                contest=by_key.get("USACO")
                if contest is not None:
                    old_events=contest.get("events",[])
                    if json.dumps(old_events,sort_keys=True)!=json.dumps(parsed,sort_keys=True):
                        history.insert(0,{
                            "id":f"USACO-schedule-{now.strftime('%Y%m%dT%H%M%SZ')}",
                            "contest":"USACO","type":"schedule_updated",
                            "detectedAt":now_iso,"url":"https://usaco.org/",
                            "message":f"检测到 USACO 2026–2027 官方赛程，共 {len(parsed)} 项日期安排。",
                            "before":old_events,"after":parsed
                        })
                        contest["events"]=parsed
                        contest["season"]="2026–2027 官方赛程已发布"
                        contest["regText"]="无需赛季预注册；比赛日期已从 USACO 官方赛程页面同步。"
            elif page:
                # Keep any previously verified dates; otherwise show a clear pending state.
                contest=by_key.get("USACO")
                if contest is not None and not any(e.get("start") for e in contest.get("events",[])):
                    contest["season"]="2026–2027 赛程待官方公布"
                    contest["regText"]="常年可创建个人账号；无需赛季预注册。2026–2027 官方赛程尚未检测到明确赛程标题。"

    # Keep a bounded, newest-first audit trail.
    history=history[:300]
    data["lastChecked"]=now_iso
    data["history"]=history[:80]
    status["lastChecked"]=now_iso
    status["scheduleParser"]="USACO 2026–2027 explicit-heading parser"
    status["frequencyMinutes"]={"USACO":30,"others":360}
    write_json(DATA,data)
    write_json(STATUS,status)
    write_json(HISTORY,history)
    print(f"Monitor finished at {now_iso}; history entries={len(history)}")

if __name__=="__main__":
    main()

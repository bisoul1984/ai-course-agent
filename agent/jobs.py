"""Job agents: find ML / AI engineering jobs, score them, and write tailored cover letters.

Sources (all free, no login):
  - Remotive, RemoteOK, Himalayas   -> remote jobs (checked for "worldwide/Africa/EMEA" eligibility)
  - Arbeitnow (visa_sponsorship=true) -> jobs in Europe that sponsor visas
  - Web search + page reading        -> jobs in Ethiopia / Addis Ababa and extra visa-sponsored roles
"""
import datetime
import html
import json
import re
import time

import requests

from agent.llm import DailyQuotaExceeded
from agent.websearch import HEADERS, page_text, search

TITLE_RE = re.compile(
    r"(machine[- ]learning|\bml\b|\bai\b|artificial intelligence|\bllm|\bnlp\b|deep learning|"
    r"data scien|mlops|computer vision|gen ?ai|generative|applied scientist|prompt engineer|"
    r"ai engineer|ai developer|ai/ml|ml/ai)", re.I)
OPEN_LOCATIONS = re.compile(
    r"(worldwide|anywhere|global|africa|emea|ethiopia|remote$|^remote\b|international|"
    r"any location|all countries)", re.I)

JOB_SYSTEM = ("You assess job postings for one specific applicant. Be honest and strict about "
              "location/work-authorization rules and seniority. Never invent facts.")
EXTRACT_SYSTEM = ("You extract real job postings from web pages. Only report jobs actually "
                  "described in the pages. Never invent URLs or details.")
LETTER_SYSTEM = """You write concise, specific cover letters for one applicant.
Rules: use ONLY facts in the applicant profile; never invent employers, degrees, years, tools
or results. First person, plain professional English, 180-250 words, no clichés such as
"passionate" or "I am writing to express my interest". Connect 2-3 concrete things from the
profile to concrete requirements in the job description. End with a short, confident close.
Return only the letter text (no subject line, no placeholders)."""


def _strip_html(text, limit=3500):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(html.unescape(text).split())[:limit]


def _get_json(url, params=None):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"    job source error ({url.split('/')[2]}): {exc}")
        return None


def _recent(ts, days):
    return ts is None or ts > time.time() - days * 86400


def _iso_ts(text):
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------- sources
def from_remotive(term):
    data = _get_json("https://remotive.com/api/remote-jobs", {"search": term, "limit": 100})
    for j in (data or {}).get("jobs", []):
        yield {"title": j.get("title"), "company": j.get("company_name"), "url": j.get("url"),
               "apply_url": j.get("url"), "location": j.get("candidate_required_location"),
               "kind": "remote", "salary": j.get("salary"), "source": "Remotive",
               "posted": _iso_ts(j.get("publication_date")),
               "description": _strip_html(j.get("description"))}


def from_remoteok():
    data = _get_json("https://remoteok.com/api")
    for j in (data or [])[1:]:  # first item is the API notice
        yield {"title": j.get("position"), "company": j.get("company"), "url": j.get("url"),
               "apply_url": j.get("apply_url") or j.get("url"), "location": j.get("location"),
               "kind": "remote", "source": "RemoteOK",
               "salary": f"{j.get('salary_min')}-{j.get('salary_max')}" if j.get("salary_min") else None,
               "posted": float(j["epoch"]) if j.get("epoch") else None,
               "description": _strip_html(j.get("description"))}


def from_himalayas(terms=("machine learning", "AI engineer", "LLM")):
    for term in terms:
        for page in (1, 2):
            data = _get_json("https://himalayas.app/jobs/api/search",
                             {"q": term, "page": page, "sort": "recent"})
            for j in (data or {}).get("jobs", []):
                locs = j.get("locationRestrictions") or []
                locs = [l if isinstance(l, str) else (l.get("name") or "") for l in locs]
                pub = j.get("pubDate")
                posted = float(pub) if isinstance(pub, (int, float)) or str(pub).isdigit() \
                    else _iso_ts(pub)
                if posted and posted > 1e12:  # milliseconds
                    posted /= 1000
                yield {"title": j.get("title"), "company": j.get("companyName"),
                       "url": j.get("applicationLink") or j.get("guid"),
                       "apply_url": j.get("applicationLink"),
                       "location": ", ".join(locs) or "Worldwide", "kind": "remote",
                       "source": "Himalayas", "posted": posted,
                       "salary": f"{j.get('minSalary')}-{j.get('maxSalary')} {j.get('currency') or ''}"
                       if j.get("minSalary") else None,
                       "description": _strip_html(j.get("description") or j.get("excerpt"))}


def from_arbeitnow_visa():
    for page in (1, 2, 3):
        data = _get_json("https://www.arbeitnow.com/api/job-board-api",
                         {"page": page, "visa_sponsorship": "true"})
        for j in (data or {}).get("data", []):
            yield {"title": j.get("title"), "company": j.get("company_name"), "url": j.get("url"),
                   "apply_url": j.get("url"),
                   "location": ("Remote, " if j.get("remote") else "") + (j.get("location") or ""),
                   "kind": "visa", "source": "Arbeitnow (visa sponsorship)",
                   "posted": j.get("created_at"), "description": _strip_html(j.get("description"))}


def from_web(llm, queries, kind, pages_per_query=4, log=print):
    today = datetime.date.today().isoformat()
    for q in queries:
        log(f"  searching jobs: {q}")
        pages = []
        for r in search(q)[:pages_per_query]:
            text = page_text(r["url"], max_chars=5000)
            if text:
                pages.append(f"URL: {r['url']}\nTITLE: {r['title']}\nCONTENT: {text}")
        if not pages:
            continue
        prompt = f"""Today is {today}. Extract currently open job postings for machine learning,
AI, data science or AI-focused software engineering roles from THESE PAGES ONLY.
Return ONLY a JSON array (empty [] if none):
[{{"title": "...", "company": "...", "url": "posting URL from the page",
   "apply_url": "application URL if shown, else the posting URL", "location": "...",
   "deadline": "YYYY-MM-DD or null", "description": "2-4 sentence summary of requirements"}}]

PAGES:
""" + "\n\n---\n\n".join(pages)
        try:
            items = llm.ask_json(EXTRACT_SYSTEM, prompt)
        except DailyQuotaExceeded:
            raise
        except Exception as exc:
            log(f"    could not read job pages: {exc}")
            continue
        for j in items if isinstance(items, list) else []:
            if isinstance(j, dict):
                j.update(kind=kind, source="Web search")
                yield j


# ---------------------------------------------------------------- scout
def scout_jobs(llm, store, config, log=print):
    cfg = config.get("jobs", {})
    scopes = set(cfg.get("scopes", ["remote", "visa", "ethiopia"]))
    max_age = cfg.get("max_age_days", 30)
    found = 0

    def keep(j):
        if not j.get("title") or not j.get("url"):
            return False
        if not TITLE_RE.search(j["title"]):
            return False
        if j.get("kind") == "remote" and j.get("location") and \
                not OPEN_LOCATIONS.search(j["location"].strip()):
            return False  # e.g. "USA only"
        return _recent(j.get("posted"), max_age)

    streams = []
    if "remote" in scopes:
        for term in cfg.get("remote_terms", ["machine learning", "AI engineer", "LLM"]):
            streams.append(from_remotive(term))
        streams += [from_remoteok(), from_himalayas(cfg.get("remote_terms", ["machine learning"])[:3])]
    if "visa" in scopes:
        streams.append(from_arbeitnow_visa())
        streams.append(from_web(llm, cfg.get("visa_queries", []), "visa", log=log))
    if "ethiopia" in scopes:
        streams.append(from_web(llm, cfg.get("ethiopia_queries", []), "ethiopia", log=log))

    for stream in streams:
        for j in stream:
            if keep(j) and store.add_job(j):
                found += 1
                log(f"  + job: {j['title']} — {j.get('company')} ({j.get('location')})")
    store.kv_set("last_job_scout", time.time())
    return found


# ---------------------------------------------------------------- evaluate
def evaluate_jobs(llm, store, config, profile, log=print, batch_size=10):
    min_score = config.get("jobs", {}).get("min_fit_score", 70)
    todo = [dict(r) for r in store.jobs_by_status("new")]
    keep = ("title", "company", "location", "kind", "salary", "description")
    queued = 0
    for i in range(0, len(todo), batch_size):
        batch = todo[i:i + batch_size]
        items = [{"n": n, **{k: (j.get(k) or "")[:1500] for k in keep}}
                 for n, j in enumerate(batch)]
        prompt = f"""Applicant profile: {json.dumps(profile, ensure_ascii=False)}

The applicant lives in Addis Ababa, Ethiopia (Ethiopian citizen). Job kinds: "remote" =
remote role, "visa" = role abroad that may sponsor a visa, "ethiopia" = local role.

Jobs: {json.dumps(items, ensure_ascii=False)}

For EACH job give fit_score 0-100 for this applicant. Consider: is the role really ML / AI
engineering (or AI-heavy software engineering); does the seniority match ~4 years of
software experience with growing AI experience; do the required skills match the profile;
can someone in Ethiopia legally do it (remote roles limited to specific countries or
requiring local work authorization => eligible=false; visa roles must offer sponsorship).

Return ONLY a JSON array: [{{"n": 0, "eligible": true/false, "fit_score": 0-100,
"reason": "one sentence on why it fits or not"}}]"""
        try:
            results = llm.ask_json(JOB_SYSTEM, prompt)
        except DailyQuotaExceeded:
            raise
        except Exception as exc:
            log(f"  could not score this job batch: {exc}")
            continue
        for r in results if isinstance(results, list) else []:
            try:
                job = batch[int(r.get("n"))]
            except (TypeError, ValueError, IndexError):
                continue
            score = int(r.get("fit_score") or 0)
            ok = bool(r.get("eligible")) and score >= min_score
            store.update_job(job["id"], fit_score=score, fit_reason=r.get("reason"),
                             status="queued" if ok else "skipped")
            queued += ok
            log(f"  {'QUEUED ' if ok else 'skipped'} {score:>3}  {job['title']} — {job['company']}")
    return queued


# ---------------------------------------------------------------- cover letter
def write_cover_letter(llm, job, profile):
    if not job.get("description") or len(job["description"]) < 200:
        text = page_text(job["url"], max_chars=5000)
        if text:
            job["description"] = text
    prompt = f"""Applicant profile: {json.dumps(profile, ensure_ascii=False)}

Job: {json.dumps({k: job.get(k) for k in ('title', 'company', 'location', 'description')}, ensure_ascii=False)}

Write the cover letter. Before finalising, silently check every sentence against the profile
and delete anything not supported by it."""
    letter = llm.ask(LETTER_SYSTEM, prompt).strip()
    return re.sub(r"\n{3,}", "\n\n", letter)

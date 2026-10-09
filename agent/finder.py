"""Find opportunities on the web and score them against the applicant profile."""
import datetime
import json

from agent.llm import DailyQuotaExceeded
from agent.websearch import page_text, search

DISCOVER_SYSTEM = (
    "You are a research assistant who finds real training opportunities. Only report "
    "opportunities you actually found on web pages during this search. Never invent "
    "URLs, dates or funding details. If a detail is unknown, use null."
)

EVALUATE_SYSTEM = (
    "You assess whether a training opportunity suits a specific applicant. Be strict "
    "and honest about eligibility rules such as age limits, required student status "
    "or nationality restrictions."
)


EXTRACT_PROMPT = """Today is {today}. Below are web pages ({label}).

From THESE PAGES ONLY, extract AI / machine learning short courses, summer or winter
schools, bootcamps and workshops that are held in Europe or run online by a European
university/institute, whose application deadline has NOT passed yet (or is not yet
announced) and which accept international applicants. Ignore anything not described
in the pages, and ignore full degree programmes (bachelor/master/PhD).
Do not guess URLs: use the page URL or an application link shown in the page.

Return ONLY a JSON array (empty [] if none). Each item:
{{"title": "...", "organizer": "...", "url": "official page URL",
  "application_url": "direct application form URL if shown, otherwise the page URL",
  "location": "city, country (or Online)", "dates": "...", "deadline": "YYYY-MM-DD or null",
  "fee": "...", "funding": "scholarship / grant / fee-waiver info or 'none found'",
  "eligibility": "who can apply", "topics": "main topics"}}

PAGES:
"""


def _extract(llm, store, label, pages, log):
    """Ask the model to pull opportunities out of a batch of page texts."""
    if not pages:
        return 0
    prompt = EXTRACT_PROMPT.format(today=datetime.date.today().isoformat(), label=label)
    prompt += "\n\n---\n\n".join(pages)
    try:
        items = llm.ask_json(DISCOVER_SYSTEM, prompt)
    except DailyQuotaExceeded:
        raise
    except Exception as exc:  # keep going with the next batch
        log(f"  could not read pages for {label}: {exc}")
        return 0
    added = 0
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and store.add(item):
            added += 1
            log(f"  + {item.get('title')} (deadline {item.get('deadline')})")
    return added


def discover(llm, store, config, log=print):
    search_cfg = config.get("search", {})
    pages_per_query = search_cfg.get("pages_per_query", 5)
    new_count = 0

    # 1) Known course pages you listed in config.yaml (checked every run)
    seeds = search_cfg.get("seed_urls") or []
    for i in range(0, len(seeds), 4):  # 4 pages per model call
        batch = []
        for url in seeds[i:i + 4]:
            log(f"  checking: {url}")
            text = page_text(url)
            if text:
                batch.append(f"URL: {url}\nCONTENT: {text}")
        new_count += _extract(llm, store, "known course pages", batch, log)

    # 2) Free web search for each query
    for query in search_cfg.get("queries") or []:
        log(f"  searching: {query}")
        batch = []
        for r in search(query)[:pages_per_query]:
            text = page_text(r["url"])
            if text:
                batch.append(f"URL: {r['url']}\nTITLE: {r['title']}\nCONTENT: {text}")
        new_count += _extract(llm, store, f'search results for "{query}"', batch, log)
    return new_count


def _deadline_passed(deadline):
    try:
        return datetime.date.fromisoformat(str(deadline)[:10]) < datetime.date.today()
    except ValueError:
        return False


def evaluate(llm, store, config, log=print, batch_size=8):
    """Score new opportunities, several per model call to save free-tier quota."""
    profile = config["profile"]
    min_score = config.get("min_fit_score", 65)
    today = datetime.date.today().isoformat()
    todo = []
    for course in store.by_status("new"):
        course = dict(course)
        if _deadline_passed(course.get("deadline")):
            store.update(course["id"], status="skipped", fit_reason="Deadline passed")
        else:
            todo.append(course)
    keep = ("title", "organizer", "url", "location", "dates", "deadline", "fee",
            "funding", "eligibility", "topics")
    queued = 0
    for i in range(0, len(todo), batch_size):
        batch = todo[i:i + batch_size]
        items = [{"n": n, **{k: c.get(k) for k in keep}} for n, c in enumerate(batch)]
        prompt = f"""Today is {today}.
Applicant profile: {json.dumps(profile, ensure_ascii=False)}

Opportunities: {json.dumps(items, ensure_ascii=False)}

For EACH opportunity, score how well it fits the applicant (0-100). Reward: practical AI
engineering content (LLMs, agents, NLP, deployment), funding available to someone from
Ethiopia, openness to working professionals, low cost. A conference with no training
programme or a non-AI topic scores low. Mark eligible=false if any stated rule excludes
the applicant.

Return ONLY a JSON array with one object per opportunity:
[{{"n": 0, "eligible": true/false, "deadline_passed": true/false/null,
   "fit_score": 0-100, "reason": "one or two sentences"}}]"""
        try:
            results = llm.ask_json(EVALUATE_SYSTEM, prompt)
        except DailyQuotaExceeded:
            raise
        except Exception as exc:
            log(f"  could not score this batch: {exc}")
            continue
        for r in results if isinstance(results, list) else []:
            try:
                course = batch[int(r.get("n"))]
            except (TypeError, ValueError, IndexError):
                continue
            score = int(r.get("fit_score") or 0)
            ok = bool(r.get("eligible")) and not r.get("deadline_passed") and score >= min_score
            store.update(course["id"], fit_score=score, fit_reason=r.get("reason"),
                         status="queued" if ok else "skipped")
            queued += ok
            log(f"  {'QUEUED ' if ok else 'skipped'} {score:>3}  {course['title']}")
    return queued

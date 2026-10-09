"""Verifier agent: re-reads the official page to confirm an opportunity before applying."""
import datetime
import json

from agent.llm import DailyQuotaExceeded
from agent.websearch import page_text

VERIFY_SYSTEM = ("You check claims about a training opportunity against its official web "
                 "page. Trust only the page text. Be conservative.")


def verify(llm, store, log=print, include_waiting=True):
    """Checks unverified 'queued' items and re-checks 'waiting' ones."""
    today = datetime.date.today().isoformat()
    todo = [dict(r) for r in store.by_status("queued") if not r["verified"]]
    if include_waiting:
        todo += [dict(r) for r in store.by_status("waiting")]
    ready = 0
    for c in todo:
        main = page_text(c["url"], max_chars=9000)
        app = ""
        if c.get("application_url") and c["application_url"] != c["url"]:
            app = page_text(c["application_url"], max_chars=4000)
        if not main and not app:
            store.update(c["id"], status="skipped", verified=1,
                         fit_reason="Official page could not be loaded")
            log(f"  unreachable: {c['title']}")
            continue
        prompt = f"""Today is {today}.
Claimed opportunity: {json.dumps(c, ensure_ascii=False)}

OFFICIAL PAGE TEXT:
{main}

APPLICATION PAGE TEXT (may be empty):
{app}

Return ONLY JSON:
{{"page_matches": true/false (the page really describes this opportunity),
  "edition_year": "year of the edition described, or null",
  "applications_status": "open" | "not_yet_open" | "closed" | "unknown",
  "deadline": "YYYY-MM-DD or null",
  "application_url": "URL of the application form if shown, else null",
  "fee": "fee info or null", "funding": "funding info or null",
  "note": "one sentence"}}"""
        try:
            r = llm.ask_json(VERIFY_SYSTEM, prompt)
        except DailyQuotaExceeded:
            raise
        except Exception as exc:
            log(f"  could not verify {c['title']}: {exc}")
            continue
        fields = {"verified": 1}
        for key in ("deadline", "application_url", "fee", "funding"):
            if r.get(key):
                fields[key] = r[key]
        state = r.get("applications_status")
        if not r.get("page_matches"):
            fields.update(status="skipped", fit_reason="Verifier: page does not match")
        elif state == "closed":
            fields.update(status="skipped", fit_reason="Verifier: applications closed")
        elif state == "not_yet_open":
            fields.update(status="waiting", notes=f"Not open yet. {r.get('note') or ''}")
        else:
            fields.update(status="queued")
            ready += 1
        store.update(c["id"], **fields)
        log(f"  {fields.get('status', 'queued'):<8} {c['title']}  ({r.get('note') or ''})")
    return ready

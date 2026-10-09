"""Open an application page, fill the form with Gemini's help, and submit it."""
import json
import os
import re
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

BROWSER_PROFILE = os.path.abspath("data/browser_profile")
LOG_DIR = "logs"

NEXT_RE = re.compile(r"^\s*(next|continue|weiter|suivant|siguiente|avanti)\s*$", re.I)
SUBMIT_RE = re.compile(r"^\s*(submit|send|apply|apply now|submit application|register|"
                       r"finish|complete|senden|envoyer|enviar|invia)\s*$", re.I)
CONFIRM_RE = re.compile(r"(response has been recorded|thank you|thanks for|successfully|"
                        r"submitted|received your|application has been)", re.I)
NO_AI_RE = re.compile(
    r"(write|written)[^.]{0,60}(yourself|your own)|"
    r"(rather than|without|not|no|instead of)\s+(using\s+|the use of\s+)?(an?\s+)?"
    r"(AI\b|A\.I\.|ChatGPT|generative AI|AI tools|AI-generated|language models?)", re.I)
NO_AUTO_HOSTS = re.compile(r"https?://([a-z0-9-]+\.)*(linkedin|indeed|glassdoor|monster|"
                           r"ziprecruiter)\.", re.I)
PAY_RE = re.compile(r".*(secure payment|proceed to payment|continue to payment|pay now|"
                    r"checkout|go to payment|make payment).*", re.I)
REQUIRED_ERR_RE = re.compile(r"(this (question|field) is required|required field|"
                             r"please (fill|complete|answer) (in |out )?(this|all)|"
                             r"must be filled|cannot be (left )?(blank|empty))", re.I)
APPLY_LINK_RE = re.compile(r"\b(apply|application|register|registration)\b", re.I)

FILL_SYSTEM = """You fill in application forms on behalf of the applicant, who has authorised
you to apply for them. Rules:
- Use ONLY facts from the applicant profile. Never invent degrees, jobs, dates,
  publications, scores or experiences.
- For motivation / essay / free-text questions write a specific, sincere, first-person
  answer tailored to this opportunity, drawing on the real projects and goals in the
  profile. Respect any word or character limit in the question or in max_length.
- For select, radio and checkbox fields the value must match one of the listed options
  exactly (a list of options for checkbox groups).
- Dates must be YYYY-MM-DD for date inputs.
- For file fields answer "CV" if a CV/resume is requested, "COVER_LETTER" if a cover letter is
  requested and the profile has "cover_letter_for_this_application", otherwise "UNKNOWN".
- If the profile has "cover_letter_for_this_application", use that text VERBATIM for any
  cover-letter / motivation / "why do you want to join" text field.
- Never state work authorisation, visa status, salary expectations, notice period, start date
  or willingness to relocate unless the profile or the applicant's answers state it; if such
  a field is required and unknown, put a clear question in "missing".
- The profile may contain "answers_given_by_applicant": treat those as true facts.
- If a REQUIRED field cannot be answered truthfully from the profile, add to "missing" a
  short, clear question to the applicant (include the allowed options for choice fields).
  Skip optional fields you cannot answer. NEVER list an optional field (one marked
  "optional" or with required=false) in "missing".
- For "dropdown" fields (options not listed), give the exact visible option text you
  expect, e.g. the country name "Ethiopia".
- If these fields are not an application/registration form for this opportunity
  (e.g. a newsletter signup or site search), set "not_a_form": true."""

EXTRACT_JS = r"""
() => {
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect();
    const s = getComputedStyle(el); return r.width > 0 && r.height > 0 &&
    s.visibility !== 'hidden' && s.display !== 'none'; };
  const clean = t => (t || '').replace(/\s+/g, ' ').trim().slice(0, 500);
  const questionText = el => {
    const item = el.closest('[role="listitem"], fieldset, .form-group, .field, .question, li');
    const h = item && item.querySelector('[role="heading"], legend, h3, h4, label');
    return h ? h.innerText : '';
  };
  const labelFor = el => {
    let t = '';
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) t = l.innerText; }
    if (!t && el.closest('label')) t = el.closest('label').innerText;
    if (!t && el.getAttribute('aria-labelledby')) t = el.getAttribute('aria-labelledby')
      .split(' ').map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
    if (!t) t = el.getAttribute('aria-label') || '';
    const q = questionText(el);
    if (q && !t.includes(q)) t = q + ' ' + t;
    return clean(t || el.placeholder || el.name);
  };
  const fields = []; let n = 0;
  document.querySelectorAll('input, textarea, select').forEach(el => {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'radio', 'checkbox', 'search'].includes(type)) return;
    if (type !== 'file' && !visible(el)) return;
    const id = 'f' + (n++); el.setAttribute('data-agent-id', id);
    const f = { id, kind: el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : type,
      question: labelFor(el), required: el.required || el.getAttribute('aria-required') === 'true' };
    if (el.tagName === 'SELECT') f.options = [...el.options].map(o => clean(o.text)).filter(Boolean);
    if (el.maxLength > 0) f.max_length = el.maxLength;
    fields.push(f);
  });
  const groups = new Map();
  document.querySelectorAll('input[type=radio], input[type=checkbox]').forEach(el => {
    if (!visible(el) && !visible(el.closest('label'))) return;
    const key = el.type + ':' + (el.name || el.id);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(el);
  });
  document.querySelectorAll('[role="radio"], [role="checkbox"]').forEach(el => {
    if (el.matches('input') || !visible(el)) return;
    const box = el.closest('[role="listitem"]') || el.closest('[role="radiogroup"], [role="group"]') || el.parentElement;
    if (!groups.has(box)) groups.set(box, []);
    groups.get(box).push(el);
  });
  groups.forEach(els => {
    const id = 'g' + (n++);
    els.forEach(e => e.setAttribute('data-agent-group', id));
    const first = els[0];
    const isRadio = first.type === 'radio' || first.getAttribute('role') === 'radio';
    const opt = e => clean(e.matches('input') ? (labelFor(e).replace(questionText(e), '') || e.value)
      : (e.getAttribute('aria-label') || e.getAttribute('data-value') || e.innerText));
    const q = questionText(first);
    fields.push({ id, kind: isRadio ? 'radio_group' : 'checkbox_group', question: clean(q || first.name),
      required: els.some(e => e.required || e.getAttribute('aria-required') === 'true') || /\*/.test(q),
      options: els.map(opt) });
  });
  document.querySelectorAll('[role="combobox"], [aria-haspopup="listbox"]').forEach(el => {
    if (el.matches('input, select, textarea') || !visible(el)) return;
    const id = 'f' + (n++); el.setAttribute('data-agent-id', id);
    const q = questionText(el) || el.getAttribute('aria-label') || '';
    fields.push({ id, kind: 'dropdown', question: clean(q),
      required: el.getAttribute('aria-required') === 'true' || /\*/.test(q) });
  });
  return fields;
}
"""


def _button(page, regex):
    cands = page.locator('button, [role="button"], input[type="submit"], input[type="button"]')
    for i in range(cands.count()):
        b = cands.nth(i)
        try:
            if not b.is_visible():
                continue
            text = (b.inner_text() or b.get_attribute("value")
                    or b.get_attribute("aria-label") or "").strip()
        except Exception:
            continue
        if regex.match(text):
            return b
    return None


def _apply_link(page):
    links = page.locator("a")
    found_text_link = None
    for i in range(min(links.count(), 400)):
        a = links.nth(i)
        try:
            href = a.get_attribute("href") or ""
            text = (a.inner_text() or "").strip()
        except Exception:
            continue
        if href.startswith(("mailto:", "#", "javascript")) or not href:
            continue
        if "docs.google.com/forms" in href or "forms.gle" in href:
            return urljoin(page.url, href)
        if not found_text_link and APPLY_LINK_RE.search(text):
            found_text_link = urljoin(page.url, href)
    return found_text_link


def _blocker(page):
    url = page.url.lower()
    if "accounts.google.com" in url or re.search(r"(login|signin|sign-in)", url):
        return "login required"
    if page.locator('iframe[src*="recaptcha/api2/bframe"], iframe[src*="hcaptcha"], '
                    'iframe[src*="turnstile"], .g-recaptcha, .h-captcha').count():
        return "CAPTCHA"
    if page.locator('input[type="password"]').count():
        return "login required"
    if page.locator('iframe[src*="stripe"], iframe[src*="paypal"], '
                    'input[autocomplete="cc-number"]').count():
        return "payment page"
    if _button(page, PAY_RE):
        return "paid registration (asks for payment)"
    return None


def _fill(page, field, value, cv_path, files=None):
    if field["id"].startswith("f"):
        loc = page.locator(f'[data-agent-id="{field["id"]}"]')
        kind = field["kind"]
        if kind == "file":
            wanted = {"CV": cv_path, **(files or {})}.get(str(value).upper())
            if not wanted or not os.path.exists(wanted):
                raise ValueError("file upload needed that the agent does not have")
            loc.set_input_files(wanted)
        elif kind == "dropdown":
            loc.click()
            page.wait_for_timeout(700)
            opts = page.get_by_role("option")
            texts = [t.strip() for t in opts.all_inner_texts()]
            target = next((i for i, t in enumerate(texts) if t.lower() == str(value).lower()), None)
            if target is None:
                target = next((i for i, t in enumerate(texts)
                               if str(value).lower() in t.lower()), None)
            if target is None:
                target = next((i for i, t in enumerate(texts) if t.lower() == "other"), None)
            if target is None:
                page.keyboard.press("Escape")
                raise ValueError(f"'{value}' is not one of the options")
            opts.nth(target).click()
            page.wait_for_timeout(500)
        elif kind == "select":
            try:
                loc.select_option(label=str(value))
            except Exception:
                loc.select_option(value=str(value))
        else:
            loc.fill(str(value))
        return
    wanted = value if isinstance(value, list) else [value]
    options = page.locator(f'[data-agent-group="{field["id"]}"]')
    for v in wanted:
        if v not in field["options"]:
            raise ValueError(f"'{v}' is not one of the options")
        el = options.nth(field["options"].index(v))
        if el.evaluate("e => e.matches('input')"):
            el.check(force=True)
        else:
            el.click()


def apply_to_course(course, llm, config, log=print):
    """Applicant agent. Returns (status, note, missing_questions).
    Status: applied, submitted_unconfirmed, blocked, needs_input, failed, skipped."""
    from agent.writer import review_and_revise
    status, note, missing = _apply(course, llm, config, review_and_revise, log)
    return status, note, missing


def _apply(course, llm, config, review_and_revise, log):
    os.makedirs(LOG_DIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", course["title"].lower())[:50]
    profile = config["profile"]
    cv_path = os.path.abspath(profile.get("cv_path", ""))
    url = course.get("application_url") or course["url"]
    answers_log = []
    files = {}
    letter = profile.get("cover_letter_for_this_application")
    if letter:
        os.makedirs(LOG_DIR, exist_ok=True)
        files["COVER_LETTER"] = os.path.abspath(f"{LOG_DIR}/{slug}-cover-letter.txt")
        with open(files["COVER_LETTER"], "w", encoding="utf-8") as fh:
            fh.write(letter)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(BROWSER_PROFILE,
                                                   headless=config.get("headless", False))
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            fields = page.evaluate(EXTRACT_JS)
            if len(fields) < 3:
                link = _apply_link(page)
                if link:
                    log(f"    following application link: {link}")
                    page.goto(link, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)

            for step in range(12):  # multi-page forms
                if NO_AUTO_HOSTS.search(page.url):
                    return ("blocked", "this site does not allow automated applications - "
                            f"please apply yourself: {page.url}", [])
                blocker = _blocker(page)
                if blocker:
                    page.screenshot(path=f"{LOG_DIR}/{slug}-blocked.png", full_page=True)
                    return "blocked", f"{blocker} at {page.url}", []
                fields = page.evaluate(EXTRACT_JS)
                if not fields:
                    if step == 0:
                        return "needs_input", f"No application form found at {page.url}", []
                    break
                # Forms that forbid AI-written text: use ONLY the applicant's own words.
                own = {a["question"]: a["answer"]
                       for a in profile.get("answers_given_by_applicant", [])}
                own_fill = {}
                for f in [f for f in fields if f["kind"] == "textarea"
                          and NO_AI_RE.search(f["question"])]:
                    ask = (f"✍️ {course['title']} asks applicants to write this themselves "
                           f"(no AI). Please send your own text for: {f['question'][:250]}")
                    if ask in own:
                        own_fill[f["id"]] = own[ask]
                    else:
                        return ("needs_input", "This form asks for a text written without AI",
                                [ask])
                fields_for_llm = [f for f in fields if f["id"] not in own_fill]
                prompt = (f"Opportunity: {json.dumps(course, ensure_ascii=False)}\n\n"
                          f"Applicant profile: {json.dumps(profile, ensure_ascii=False)}\n\n"
                          f"Form fields on this page: {json.dumps(fields_for_llm, ensure_ascii=False)}\n\n"
                          'Return ONLY JSON: {"not_a_form": false, "answers": '
                          '[{"id": "...", "value": ...}], "missing": ["question", ...]}')
                plan = llm.ask_json(FILL_SYSTEM, prompt)
                if plan.get("not_a_form"):
                    return "needs_input", f"Page is not an application form: {page.url}", []
                plan["missing"] = [m for m in plan.get("missing") or []
                                   if "optional" not in str(m).lower()]
                plan.setdefault("answers", [])
                plan["answers"] += [{"id": fid, "value": txt, "own": True}
                                    for fid, txt in own_fill.items()]
                if plan.get("missing"):
                    page.screenshot(path=f"{LOG_DIR}/{slug}-missing.png", full_page=True)
                    return ("needs_input", "Missing info: " + "; ".join(plan["missing"]),
                            [str(m) for m in plan["missing"]])
                by_id = {f["id"]: f for f in fields}
                for ans in plan.get("answers", []):
                    field = by_id.get(ans.get("id"))
                    if not field or ans.get("value") in (None, "", "UNKNOWN"):
                        continue
                    value = ans["value"]
                    if letter and str(value).strip() == letter.strip():
                        ans["own"] = True
                    if field["kind"] == "textarea" and len(str(value)) > 120 \
                            and not ans.get("own"):
                        log(f"    Writer agent reviewing: {field['question'][:60]}")
                        value = review_and_revise(llm, field["question"], str(value), course,
                                                  profile, field.get("max_length"))
                        ans["value"] = value
                    try:
                        _fill(page, field, value, cv_path, files)
                        answers_log.append({"question": field["question"], "answer": ans["value"]})
                    except Exception as exc:
                        if field.get("required"):
                            return ("needs_input", f"Could not fill '{field['question']}': {exc}",
                                    [] if "upload" not in str(exc) else
                                    [f"Please send me a file for: {field['question']}"])
                page.wait_for_timeout(1000)
                nxt = _button(page, NEXT_RE)
                if not nxt:
                    break
                nxt.click()
                page.wait_for_timeout(3000)

            with open(f"{LOG_DIR}/{slug}-answers.json", "w", encoding="utf-8") as fh:
                json.dump({"course": course["title"], "url": page.url,
                           "answers": answers_log}, fh, ensure_ascii=False, indent=2)
            page.screenshot(path=f"{LOG_DIR}/{slug}-before-submit.png", full_page=True)

            submit = _button(page, SUBMIT_RE)
            if not submit:
                return "needs_input", f"Form filled but no Submit button found at {page.url}", []
            if not config.get("auto_submit", False):
                choice = input(f"\nReview '{course['title']}' in the browser. "
                               "Press Enter to submit, or type 'skip': ").strip().lower()
                if choice == "skip":
                    return "skipped", "Skipped by you before submitting", []
            submit.click()
            page.wait_for_timeout(5000)
            page.screenshot(path=f"{LOG_DIR}/{slug}-after-submit.png", full_page=True)
            body = page.inner_text("body")
            if REQUIRED_ERR_RE.search(body) and not CONFIRM_RE.search(body):
                return ("failed", "Not submitted: the form says some required questions are "
                        f"still empty. Finish it here: {page.url}", [])
            if CONFIRM_RE.search(body):
                return "applied", f"Confirmation shown at {page.url}", []
            return "submitted_unconfirmed", "Clicked Submit but no confirmation text found", []
        except Exception as exc:
            from agent.llm import DailyQuotaExceeded
            if isinstance(exc, DailyQuotaExceeded):
                raise
            try:
                page.screenshot(path=f"{LOG_DIR}/{slug}-error.png", full_page=True)
            except Exception:
                pass
            return "failed", str(exc)[:300], []
        finally:
            ctx.close()

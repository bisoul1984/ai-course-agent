"""Profile agent: builds your applicant profile from config.yaml + your CV + your answers."""
import copy
import json
import os

CV_SYSTEM = ("You extract facts from a CV. Only report what the CV states. Never guess. "
             "Use null for anything not in the CV.")

# Basic facts almost every application form asks for, with the question we ask you
BASIC_FIELDS = {
    "full_name": "What is your full name exactly as in your passport?",
    "email": "Which email address should applications use?",
    "phone": "What is your phone number with country code (e.g. +251...)?",
    "date_of_birth": "What is your date of birth? (format YYYY-MM-DD)",
    "years_of_experience": "How many years of professional work experience do you have?",
    "city": "Which city do you live in?",
    "highest_degree": "What is your highest degree, institution and year? "
                      "(e.g. BSc Computer Science, Addis Ababa University, 2020)",
}


def _empty(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().startswith("#")
    if isinstance(value, list):
        return not value or all(_empty(v) if not isinstance(v, dict)
                                else all(_empty(x) for x in v.values()) for v in value)
    return False


def _read_pdf(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)[:15000]


def cv_facts(llm, cv_path, cache="data/cv_profile.json", log=print):
    """Extract facts from the CV once; re-extract only when the CV file changes."""
    if not cv_path or not os.path.exists(cv_path):
        return {}
    mtime = os.path.getmtime(cv_path)
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached.get("_mtime") == mtime:
            return cached
    try:
        text = _read_pdf(cv_path)
    except Exception as exc:
        log(f"  could not read CV: {exc}")
        return {}
    if len(text.strip()) < 100:
        log("  CV has no readable text (is it a scanned image?). Add a text-based PDF.")
        return {}
    log("  Profile agent: reading your CV...")
    prompt = f"""CV text:
{text}

Return ONLY JSON:
{{"full_name": str|null, "email": str|null, "phone": str|null, "city": str|null,
  "years_of_experience": str|null, "highest_degree": str|null,
  "education": [{{"degree": str, "institution": str, "year_completed": str}}],
  "work_history": [{{"role": str, "employer": str, "period": str, "highlights": str}}],
  "skills": [str], "projects": [{{"name": str, "description": str}}],
  "languages": [str], "links": {{"github": str|null, "linkedin": str|null, "portfolio": str|null}},
  "summary": "2-3 sentence factual summary of the person's background"}}"""
    try:
        facts = llm.ask_json(CV_SYSTEM, prompt)
    except Exception as exc:
        log(f"  could not extract CV facts: {exc}")
        return {}
    facts["_mtime"] = mtime
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(facts, fh, ensure_ascii=False, indent=2)
    return facts


def effective_profile(llm, config, store, log=print):
    """config.yaml wins; CV fills gaps; your Telegram answers fill the rest."""
    profile = copy.deepcopy(config.get("profile") or {})
    facts = cv_facts(llm, profile.get("cv_path"), log=log) if llm else {}
    for key, value in facts.items():
        if key.startswith("_") or _empty(value):
            continue
        if key in ("skills", "projects", "languages") and not _empty(profile.get(key)):
            existing = json.dumps(profile[key], ensure_ascii=False).lower()
            extra = [v for v in value if json.dumps(v, ensure_ascii=False).lower()[:40]
                     not in existing]
            profile[key] = list(profile[key]) + extra
        elif key == "links" and isinstance(value, dict):
            links = profile.setdefault("links", {}) or {}
            for k, v in value.items():
                if _empty(links.get(k)) and v:
                    links[k] = v
            profile["links"] = links
        elif _empty(profile.get(key)):
            profile[key] = value

    extra_answers = []
    for q in store.answered_questions():
        if q["field"] and q["field"].startswith("job:"):
            continue  # YES/NO approvals for jobs, not profile facts
        if q["field"]:
            profile[q["field"]] = q["answer"]
        else:
            extra_answers.append({"question": q["question"], "answer": q["answer"]})
    if extra_answers:
        profile["answers_given_by_applicant"] = extra_answers
    if _empty(profile.get("highest_degree")) and not _empty(profile.get("education")):
        ed = profile["education"][0]
        profile["highest_degree"] = ", ".join(str(v) for v in ed.values() if v)
    return profile


def missing_basics(profile):
    return [f for f in BASIC_FIELDS if _empty(profile.get(f))]

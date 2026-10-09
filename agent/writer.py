"""Writer + Reviewer agents: improve essay answers before they are submitted."""
import json
import re

REVIEW_SYSTEM = """You are a strict reviewer of application essays. You improve a draft so it
is specific, sincere and persuasive, and you remove ANY claim that is not supported by the
applicant profile. Never add new facts. Write in first person, plain clear English, no
clichés ("passionate", "ever since I was young"), no headings."""


def word_limit(question, max_length=None):
    q = question or ""
    m = re.search(r"(\d{2,4})\s*(?:words|word)", q, re.I)
    words = int(m.group(1)) if m else None
    m = re.search(r"(\d{2,5})\s*(?:characters|chars|character)", q, re.I)
    chars = int(m.group(1)) if m else None
    if max_length and max_length > 0:
        chars = min(chars, max_length) if chars else max_length
    return words, chars


def enforce_limit(text, words=None, chars=None):
    text = text.strip()
    if words and len(text.split()) > words:
        text = " ".join(text.split()[:words])
        cut = max(text.rfind(". "), text.rfind("! "), text.rfind("? "))
        text = text[:cut + 1] if cut > len(text) * 0.6 else text
    if chars and len(text) > chars:
        text = text[:chars]
        cut = text.rfind(". ")
        text = text[:cut + 1] if cut > chars * 0.6 else text
    return text.strip()


def review_and_revise(llm, question, draft, course, profile, max_length=None):
    words, chars = word_limit(question, max_length)
    limit = (f"{words} words" if words else "") + (f" / {chars} characters" if chars else "")
    prompt = f"""Question on the form: {question}
Length limit: {limit or 'none stated (aim for 150-250 words)'}
Opportunity: {json.dumps({k: course.get(k) for k in ('title', 'organizer', 'topics', 'location')}, ensure_ascii=False)}
Applicant profile: {json.dumps(profile, ensure_ascii=False)}

DRAFT:
{draft}

Review the draft: does it answer the question directly, mention this specific opportunity,
use concrete examples from the profile, stay within the limit, and contain only true facts?
Then write the improved final version.
Return ONLY JSON: {{"issues": ["..."], "final": "the improved answer"}}"""
    try:
        result = llm.ask_json(REVIEW_SYSTEM, prompt)
        final = result.get("final") or draft
    except Exception:
        final = draft
    return enforce_limit(final, words, chars)

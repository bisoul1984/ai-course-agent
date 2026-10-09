"""Free web search (DuckDuckGo, no API key) and page text extraction."""
import requests
from bs4 import BeautifulSoup

try:
    from ddgs import DDGS
except ImportError:  # older package name
    from duckduckgo_search import DDGS

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}


def search(query, max_results=8):
    try:
        return [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")}
                for r in DDGS().text(query, max_results=max_results)]
    except Exception as exc:
        print(f"    search error: {exc}")
        return []


def page_text(url, max_chars=6000):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException:
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    links = [f"{a.get_text(strip=True)} -> {a['href']}" for a in soup.find_all("a", href=True)
             if any(w in (a.get_text() + a["href"]).lower()
                    for w in ("apply", "application", "register", "forms.gle", "google.com/forms"))]
    text = " ".join(soup.get_text(" ").split())
    return (text[:max_chars] + "\nApplication-related links: " + " | ".join(links[:15]))

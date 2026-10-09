"""Wrapper around the free Google Gemini API.

Handles the free tier gracefully:
- discovers which Gemini models your key can actually use (old ones get retired),
- waits on short per-minute limits, switches model when one runs out for the day,
- when every model is out for the day, reports it so the agents pause until the
  quota resets instead of failing every task.
"""
import datetime
import json
import os
import re
import time

from google import genai
from google.genai import types

# Preferred order. Anything not available to your key is skipped automatically.
PREFERRED = [
    "gemini-3.5-flash", "gemini-flash-latest", "gemini-3.1-flash", "gemini-2.5-flash",
    "gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-2.5-flash-lite",
]


class DailyQuotaExceeded(RuntimeError):
    """Every usable model has used up today's free requests."""


def extract_json(text):
    """Pull the first JSON object/array out of a model response."""
    candidates = re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)[::-1] + [text]
    for cand in candidates:
        cand = cand.strip()
        starts = [i for i in (cand.find("{"), cand.find("[")) if i != -1]
        if not starts:
            continue
        start = min(starts)
        closer = "}" if cand[start] == "{" else "]"
        end = cand.rfind(closer)
        if end > start:
            try:
                return json.loads(cand[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("No valid JSON found in model response")


def _next_reset():
    """Free-tier daily quotas reset at midnight Pacific time (07:00-08:00 UTC)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    reset = now.replace(hour=8, minute=5, second=0, microsecond=0)
    if reset <= now:
        reset += datetime.timedelta(days=1)
    return reset.timestamp()


class LLM:
    def __init__(self, model, api_key_env="GEMINI_API_KEY", min_seconds_between_calls=7):
        key = os.environ.get(api_key_env)
        if not key:
            raise RuntimeError(f"Please set the {api_key_env} environment variable.")
        self.client = genai.Client(api_key=key)
        self.gap = min_seconds_between_calls
        self._last_call = 0.0
        self.exhausted_until = {}      # model -> timestamp when it can be used again
        self.models = self._discover_models(model)
        print(f"  Gemini models in use: {', '.join(self.models[:4])}")

    # ---------- model discovery ----------
    def _discover_models(self, configured):
        wanted = [configured] + [m for m in PREFERRED if m != configured]
        try:
            available = set()
            for m in self.client.models.list():
                name = (m.name or "").replace("models/", "")
                actions = getattr(m, "supported_actions", None) or []
                if not actions or "generateContent" in actions:
                    available.add(name)
            usable = [m for m in wanted if m in available]
            if not usable:  # nothing from our list: take any flash model offered
                usable = sorted(n for n in available if "flash" in n and "image" not in n
                                and "tts" not in n and "audio" not in n and "live" not in n)
            return usable or wanted
        except Exception:
            return wanted

    # ---------- quota state ----------
    def _ready_models(self):
        now = time.time()
        return [m for m in self.models if self.exhausted_until.get(m, 0) <= now]

    @property
    def quota_exhausted(self):
        return not self._ready_models()

    @property
    def resume_time(self):
        """When the first model becomes usable again (local time string)."""
        if not self.exhausted_until:
            return None
        ts = min(self.exhausted_until.values())
        return datetime.datetime.fromtimestamp(ts).strftime("%H:%M on %d %b")

    def _wait_turn(self):
        pause = self.gap - (time.time() - self._last_call)
        if pause > 0:
            time.sleep(pause)
        self._last_call = time.time()

    # ---------- main call ----------
    def ask(self, system, prompt, max_tokens=8000):
        config = types.GenerateContentConfig(system_instruction=system,
                                             max_output_tokens=max_tokens, temperature=0.4)
        last_error = None
        for model in self._ready_models():
            for attempt in range(3):
                self._wait_turn()
                try:
                    resp = self.client.models.generate_content(
                        model=model, contents=prompt, config=config)
                    return resp.text or ""
                except Exception as exc:
                    msg = str(exc)
                    last_error = exc
                    if "404" in msg or "NOT_FOUND" in msg or "not supported" in msg.lower():
                        print(f"    (model {model} not available, removing it)")
                        if model in self.models:
                            self.models.remove(model)
                        break
                    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                        if re.search(r"per ?day|PerDay|daily", msg, re.I):
                            print(f"    ({model}: today's free quota used up, switching model)")
                            self.exhausted_until[model] = _next_reset()
                            break
                        m = re.search(r"retry(?:Delay| in)[^0-9]*([0-9.]+)", msg)
                        wait = min(float(m.group(1)) + 2, 90) if m else 30 * (attempt + 1)
                        print(f"    (per-minute limit on {model}, waiting {int(wait)}s...)")
                        time.sleep(wait)
                        if attempt == 2:  # still limited: treat as out for an hour
                            self.exhausted_until[model] = time.time() + 3600
                        continue
                    if "503" in msg or "UNAVAILABLE" in msg or "500" in msg:
                        time.sleep(20 * (attempt + 1))
                        continue
                    raise
        if self.quota_exhausted:
            raise DailyQuotaExceeded(
                f"Gemini free quota used up; it resets around {self.resume_time}")
        raise RuntimeError(f"Gemini request failed: {last_error}")

    def ask_json(self, system, prompt, **kwargs):
        return extract_json(self.ask(system, prompt, **kwargs))

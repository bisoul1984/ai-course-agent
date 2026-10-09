"""Assistant agent: talks to you on Telegram (two-way). Without Telegram it uses the console."""
import os
import re

import requests

HELP = ("Commands:\n/status - progress summary\n/jobs - your job matches\n/questions - open questions\n"
        "/skip N - skip question N\n/pause - stop applying\n/resume - continue applying\n"
        "To answer: reply to the question message, or send  1: your answer  (1 = question number)")


class Assistant:
    def __init__(self, cfg, store):
        cfg = cfg or {}
        self.token = os.environ.get(cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"))
        self.chat = str(os.environ.get(cfg.get("chat_id_env", "TELEGRAM_CHAT_ID")) or "")
        self.store = store

    @property
    def enabled(self):
        return bool(self.token and self.chat)

    def _api(self, method, **payload):
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.token}/{method}",
                              json=payload, timeout=35)
            return r.json() if r.ok else None
        except (requests.RequestException, ValueError) as exc:
            print(f"(Telegram error: {exc})")
            return None

    def send(self, text):
        """Send a message; returns the Telegram message id (or None)."""
        print(text)
        if not self.enabled:
            return None
        res = self._api("sendMessage", chat_id=self.chat, text=text[:4000],
                        disable_web_page_preview=True)
        return (res or {}).get("result", {}).get("message_id")

    def ask(self, qid):
        q = self.store.question(qid)
        course = self.store.get(q["course_id"]) if q["course_id"] else None
        if not course and q["course_id"]:
            job = self.store.get_job(q["course_id"])
            if job:
                course = {"title": f"{job['title']} at {job['company']}"}
        if (q["field"] or "").startswith("job:"):
            context = "💼 Job approval\n"
        else:
            context = f"For: {course['title']}\n" if course else "For your profile\n"
        msg_id = self.send(f"❓ Question {qid}\n{context}{q['question']}\n\n"
                           f"Just reply with the answer (or send /skip {qid}).")
        if msg_id:
            self.store.set_question(qid, tg_message_id=msg_id)

    def poll(self):
        """Read new Telegram messages. Returns a list of (kind, data) events."""
        if not self.enabled:
            return []
        offset = int(self.store.kv_get("tg_offset", 0))
        res = self._api("getUpdates", offset=offset, timeout=0)
        events = []
        for upd in (res or {}).get("result", []):
            self.store.kv_set("tg_offset", upd["update_id"] + 1)
            msg = upd.get("message") or {}
            if str((msg.get("chat") or {}).get("id")) != self.chat:
                continue  # ignore strangers: only YOUR chat can control the agent
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            reply_to = (msg.get("reply_to_message") or {}).get("message_id")
            q = self.store.question_by_message(reply_to) if reply_to else None
            if q:
                events.append(("answer", (q["id"], text)))
            elif text.startswith("/"):
                parts = text.split(maxsplit=1)
                events.append(("command", (parts[0].lower().split("@")[0],
                                           parts[1] if len(parts) > 1 else "")))
            else:
                text = re.sub(r"^\s*N\s*:\s*", "", text)  # people sometimes type the 'N:' literally
                m = re.match(r"^\s*(\d{1,4})\s*[:)]\s*(.+)$", text, re.S)
                open_qs = self.store.open_questions()
                if m and self.store.question(int(m.group(1))):
                    events.append(("answer", (int(m.group(1)), m.group(2).strip())))
                elif len(open_qs) == 1:  # plain message = answer to the only open question
                    events.append(("answer", (open_qs[0]["id"], text.strip())))
                else:
                    events.append(("chat", text))
        return events

    def answer_in_console(self):
        """Fallback when Telegram isn't set up: answer open questions in the terminal."""
        open_qs = self.store.open_questions()
        if not open_qs:
            print("No open questions.")
        for q in open_qs:
            course = self.store.get(q["course_id"]) if q["course_id"] else None
        if not course and q["course_id"]:
            job = self.store.get_job(q["course_id"])
            if job:
                course = {"title": f"{job['title']} at {job['company']}"}
            print(f"\nQuestion {q['id']} ({course['title'] if course else 'profile'}):")
            ans = input(f"{q['question']}\n(Enter = skip for now, 'skip' = never ask) > ").strip()
            if ans.lower() == "skip":
                self.store.set_question(q["id"], status="skipped")
            elif ans:
                self.store.answer_question(q["id"], ans)

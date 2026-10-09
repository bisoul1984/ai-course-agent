"""Orchestrator: runs all agents in a loop and coordinates them through shared memory."""
import copy
import re
import datetime
import time
import traceback

from agent.assistant import HELP, Assistant
from agent.finder import discover, evaluate
from agent import jobs as job_agents
from agent.llm import DailyQuotaExceeded
from agent.profile_agent import BASIC_FIELDS, effective_profile, missing_basics
from agent.verifier import verify

ICON = {"applied": "✅", "submitted_unconfirmed": "☑️", "blocked": "⛔",
        "needs_input": "✋", "failed": "❌", "skipped": "⏭️"}


class Orchestrator:
    def __init__(self, config, store, llm, log=print):
        self.config, self.store, self.llm, self.log = config, store, llm, log
        self.assistant = Assistant(config.get("telegram"), store)

    # ---------- shared helpers ----------
    def profile(self):
        return effective_profile(self.llm, self.config, self.store, log=self.log)

    def _today(self):
        return datetime.date.today().isoformat()

    def applied_today(self):
        return int(self.store.kv_get(f"applied_{self._today()}", 0))

    def status_text(self):
        counts = self.store.status_counts()
        lines = ["📊 Course agent status"]
        for key in ("queued", "waiting", "applied", "submitted_unconfirmed", "needs_input",
                    "blocked", "failed", "skipped", "new"):
            if counts.get(key):
                lines.append(f"{ICON.get(key, '•')} {key}: {counts[key]}")
        upcoming = self.store.by_status("queued", "waiting")[:5]
        if upcoming:
            lines.append("\nNext up:")
            lines += [f"• {r['title']} — deadline {r['deadline'] or '?'} "
                      f"(fit {r['fit_score'] or '?'})" for r in upcoming]
        jc = self.store.job_counts()
        if jc:
            lines.append("\n💼 Jobs: " + ", ".join(f"{k} {v}" for k, v in sorted(jc.items())))
        open_q = len(self.store.open_questions())
        if open_q:
            lines.append(f"\n❓ {open_q} question(s) waiting for your answer — send /questions")
        if self.store.kv_get("paused") == "1":
            lines.append("\n⏸ Applying is paused — send /resume")
        return "\n".join(lines)

    # ---------- agent steps ----------
    def ensure_profile(self):
        """Profile agent: ask for any basic facts the config and CV don't contain."""
        missing = missing_basics(self.profile())
        for field in missing:
            qid, new = self.store.add_question(BASIC_FIELDS[field], field=field)
            if new:
                self.assistant.ask(qid)
        return not missing

    def scout(self):
        self.log("\n🔎 Scout agent: searching for opportunities...")
        found = discover(self.llm, self.store, self.config, log=self.log)
        self.store.kv_set("last_scout", time.time())
        self.log(f"   {found} new opportunities found.")
        self.store.kv_set("report_due", "1")

    def assess(self):
        """Evaluator + Verifier: only spend AI calls when there is something to do."""
        if self.store.by_status("new"):
            self.log("🧮 Evaluator agent: scoring...")
            cfg = copy.deepcopy(self.config)
            cfg["profile"] = self.profile()
            evaluate(self.llm, self.store, cfg, log=self.log)
        recheck_waiting = self.store.by_status("waiting") and \
            time.time() - float(self.store.kv_get("last_waiting_check", 0)) > 24 * 3600
        if [r for r in self.store.by_status("queued") if not r["verified"]] or recheck_waiting:
            self.log("🧐 Verifier agent: checking official pages...")
            verify(self.llm, self.store, log=self.log, include_waiting=bool(recheck_waiting))
            if recheck_waiting:
                self.store.kv_set("last_waiting_check", time.time())
        if self.store.kv_get("report_due") == "1":
            self.store.kv_set("report_due", "0")
            self.assistant.send(self.status_text())

    def apply_queued(self, retry=False):
        if self.store.kv_get("paused") == "1":
            self.log("Applying is paused.")
            return
        from agent.form_filler import apply_to_course
        if not self.ensure_profile():
            self.log("Waiting for your answers to basic profile questions before applying.")
            return
        per_run = self.config.get("max_applications_per_run", 3)
        per_day = self.config.get("max_applications_per_day", 5)
        statuses = ("queued", "needs_input", "failed") if retry else ("queued",)
        todo = [dict(r) for r in self.store.by_status(*statuses)
                if r["verified"] and not self.store.course_has_open_questions(r["id"])]
        cfg = copy.deepcopy(self.config)
        cfg["profile"] = self.profile()
        for course in todo[:per_run]:
            if self.applied_today() >= per_day:
                self.log("Daily application limit reached.")
                break
            attempts = int(self.store.kv_get(f"attempts_{course['id']}", 0)) + 1
            self.store.kv_set(f"attempts_{course['id']}", attempts)
            if attempts > self.config.get("max_attempts_per_course", 3):
                self.store.update(course["id"], status="failed",
                                  notes="Stopped after too many attempts - please apply by hand")
                self.assistant.send(f"❌ {course['title']}: I tried {attempts - 1} times and "
                                    f"couldn't finish. Please apply by hand:\n{course['url']}")
                continue
            self.log(f"\n📝 Applicant agent: {course['title']} (attempt {attempts})")
            try:
                status, note, missing = apply_to_course(course, self.llm, cfg, log=self.log)
            except DailyQuotaExceeded:
                self.store.kv_set(f"attempts_{course['id']}", attempts - 1)
                raise
            # needs_input with nothing to ask = not something the agents can finish now
            if status == "needs_input" and not missing:
                if re.search(r"no application form|not an application form", note, re.I):
                    status = "waiting"
                    note = "No open application form yet - I'll check again. " + note
                else:
                    status = "blocked"
            # a required question you chose to skip: hand this one over to you
            skipped = [t for t in missing if self.store.question_skipped(t)]
            if skipped:
                status, missing = "blocked", []
                note = "You skipped a question this form requires: " + "; ".join(skipped)
            self.store.update(course["id"], status=status, notes=note)
            if status in ("applied", "submitted_unconfirmed"):
                self.store.kv_set(f"applied_{self._today()}", self.applied_today() + 1)
            new_questions = []
            for text in missing:
                qid, new = self.store.add_question(text, course_id=course["id"])
                if new:
                    new_questions.append(qid)
            extra = "\nI'll ask you what I need and continue after you answer." \
                if new_questions else ""
            hint = "\nPlease finish this one yourself:\n" + (course.get("application_url") or
                                                             course["url"]) \
                if status in ("blocked", "failed") else ""
            self.assistant.send(f"{ICON.get(status, '')} {course['title']}\n{status}: {note}"
                                f"\nDeadline: {course.get('deadline')}{hint}{extra}")
            for qid in new_questions:
                self.assistant.ask(qid)

    def handle_messages(self):
        """Assistant agent: process your Telegram replies and commands."""
        for kind, data in self.assistant.poll():
            if kind == "answer":
                qid, text = data
                q = self.store.question(qid)
                if not q:
                    self.assistant.send(f"I don't have a question {qid}.")
                    continue
                self.store.answer_question(qid, text)
                if (q["field"] or "").startswith("job:"):
                    self._job_decision(q["field"][4:], text)
                else:
                    self.assistant.send(f"👍 Saved answer to question {qid}.")
                self._requeue_ready()
            elif kind == "command":
                cmd, arg = data
                if cmd == "/status":
                    self.assistant.send(self.status_text())
                elif cmd == "/questions":
                    qs = self.store.open_questions()
                    if not qs:
                        self.assistant.send("No open questions 🎉")
                    for q in qs:
                        self.assistant.ask(q["id"])
                elif cmd == "/skip" and arg.strip().isdigit():
                    self.store.set_question(int(arg), status="skipped")
                    self.assistant.send(f"Skipped question {arg}.")
                elif cmd == "/jobs":
                    self.assistant.send(self.jobs_text())
                elif cmd == "/pause":
                    self.store.kv_set("paused", "1")
                    self.assistant.send("⏸ Paused. I'll keep searching but won't apply. /resume to continue.")
                elif cmd == "/resume":
                    self.store.kv_set("paused", "0")
                    self.assistant.send("▶️ Resumed.")
                else:
                    self.assistant.send(HELP)
            elif kind == "chat":
                self.assistant.send("I'm your course application agent.\n" + HELP)

    def _requeue_ready(self):
        for r in self.store.by_status("needs_input"):
            if not self.store.course_has_open_questions(r["id"]):
                self.store.update(r["id"], status="queued")
                self.log(f"  re-queued: {r['title']}")
        for r in self.store.jobs_by_status("needs_input"):
            if not self.store.course_has_open_questions(r["id"]):
                self.store.update_job(r["id"], status="approved")

    # ---------- jobs ----------
    def jobs_text(self):
        rows = self.store.jobs_by_status("proposed", "approved", "applied",
                                         "submitted_unconfirmed", "needs_input", "blocked")
        if not rows:
            return "💼 No job matches waiting right now. " + \
                   ", ".join(f"{k} {v}" for k, v in self.store.job_counts().items())
        return "💼 Your job pipeline\n" + "\n".join(
            f"{ICON.get(r['status'], '•')} {r['status']}: {r['title']} — {r['company']} "
            f"(fit {r['fit_score']})" for r in rows[:15])

    def _job_decision(self, job_id, text):
        job = self.store.get_job(job_id)
        if not job:
            return
        yes = re.match(r"\s*(y|yes|ok|apply|go|ኣዎ|አዎ)\b", text, re.I)
        if yes:
            self.store.update_job(job_id, status="approved")
            self.assistant.send(f"👍 I'll apply to {job['title']} at {job['company']}.")
        else:
            self.store.update_job(job_id, status="declined")
            self.assistant.send(f"OK, skipping {job['title']} at {job['company']}.")

    def jobs_cycle(self):
        jcfg = self.config.get("jobs", {})
        last = float(self.store.kv_get("last_job_scout", 0))
        if time.time() - last > jcfg.get("scout_every_hours", 12) * 3600:
            self.log("\n💼 Job scout agent: searching for ML / AI jobs...")
            n = job_agents.scout_jobs(self.llm, self.store, self.config, log=self.log)
            self.log(f"   {n} new job postings.")
        profile = self.profile()
        if self.store.jobs_by_status("new"):
            self.log("💼 Job evaluator agent: scoring...")
            job_agents.evaluate_jobs(self.llm, self.store, self.config, profile, log=self.log)
        self._propose_jobs(profile, jcfg)
        self._apply_jobs(profile, jcfg)

    def _propose_jobs(self, profile, jcfg):
        key = f"job_proposals_{self._today()}"
        sent = int(self.store.kv_get(key, 0))
        for job in [dict(r) for r in self.store.jobs_by_status("queued")]:
            if sent >= jcfg.get("proposals_per_day", 5):
                break
            self.log(f"✍️ Writer agent: cover letter for {job['title']} — {job['company']}")
            letter = job_agents.write_cover_letter(self.llm, job, profile)
            sent += 1
            self.store.kv_set(key, sent)
            kind = {"remote": "🌍 Remote", "visa": "✈️ Visa sponsorship",
                    "ethiopia": "🇪🇹 Ethiopia"}.get(job["kind"], "")
            self.assistant.send(
                f"💼 Job match (fit {job['fit_score']}/100) {kind}\n{job['title']} — "
                f"{job['company']}\n📍 {job['location'] or '?'}"
                + (f"\n💰 {job['salary']}" if job.get("salary") and job["salary"] != "None" else "")
                + f"\nWhy: {job['fit_reason']}\n🔗 {job['url']}\n\n✉️ Cover letter:\n{letter}")
            if jcfg.get("require_approval", True):
                qid, _ = self.store.add_question(
                    f"Apply to {job['title']} at {job['company']}? Reply YES or NO.",
                    course_id=None, field=f"job:{job['id']}")
                self.store.update_job(job["id"], cover_letter=letter, status="proposed")
                self.assistant.ask(qid)
            else:
                self.store.update_job(job["id"], cover_letter=letter, status="approved")

    def _apply_jobs(self, profile, jcfg):
        if self.store.kv_get("paused") == "1":
            return
        from agent.form_filler import apply_to_course
        key = f"job_applied_{self._today()}"
        done = int(self.store.kv_get(key, 0))
        for job in [dict(r) for r in self.store.jobs_by_status("approved")]:
            if done >= jcfg.get("max_applications_per_day", 5):
                break
            if self.store.course_has_open_questions(job["id"]):
                continue
            attempts = int(self.store.kv_get(f"attempts_{job['id']}", 0)) + 1
            self.store.kv_set(f"attempts_{job['id']}", attempts)
            if attempts > self.config.get("max_attempts_per_course", 3):
                self.store.update_job(job["id"], status="failed")
                self.assistant.send(f"❌ {job['title']} — {job['company']}: I couldn't finish "
                                    f"after {attempts - 1} tries. Apply here (your cover letter "
                                    f"is above in this chat): {job['apply_url'] or job['url']}")
                continue
            item = {"id": job["id"], "title": f"{job['title']} at {job['company']}",
                    "organizer": job["company"], "url": job["url"],
                    "application_url": job["apply_url"] or job["url"],
                    "topics": (job.get("description") or "")[:1500]}
            cfg = copy.deepcopy(self.config)
            cfg["profile"] = dict(profile, cover_letter_for_this_application=job["cover_letter"])
            self.log(f"\n📝 Applicant agent (job): {item['title']} (attempt {attempts})")
            try:
                status, note, missing = apply_to_course(item, self.llm, cfg, log=self.log)
            except DailyQuotaExceeded:
                self.store.kv_set(f"attempts_{job['id']}", attempts - 1)
                raise
            if status == "needs_input" and not missing:
                status = "blocked"
            skipped = [t for t in missing if self.store.question_skipped(t)]
            if skipped:
                status, missing = "blocked", []
                note = "You skipped a question this form requires: " + "; ".join(skipped)
            self.store.update_job(job["id"], status=status, notes=note)
            if status in ("applied", "submitted_unconfirmed"):
                done += 1
                self.store.kv_set(key, done)
            new_q = []
            for text in missing:
                qid, new = self.store.add_question(text, course_id=job["id"])
                if new:
                    new_q.append(qid)
            hint = f"\nPlease apply yourself: {job['apply_url'] or job['url']}" \
                if status in ("blocked", "failed") else ""
            self.assistant.send(f"{ICON.get(status, '')} {item['title']}\n{status}: {note}{hint}")
            for qid in new_q:
                self.assistant.ask(qid)

    # ---------- main loops ----------
    def cycle(self, force_scout=False):
        self.handle_messages()
        self._requeue_ready()
        if self.llm.quota_exhausted:
            return  # waiting for the free quota to reset; keep answering Telegram
        try:
            hours = self.config.get("scout_every_hours", 24)
            last = float(self.store.kv_get("last_scout", 0))
            if force_scout or time.time() - last > hours * 3600:
                self.scout()
            self.assess()
            self.apply_queued()
            if self.config.get("jobs", {}).get("enabled", True):
                self.jobs_cycle()
            self.store.kv_set("quota_notice", "0")
        except DailyQuotaExceeded as exc:
            self.log(f"\n⏸ {exc}")
            if self.store.kv_get("quota_notice") != "1":
                self.store.kv_set("quota_notice", "1")
                self.assistant.send(f"⏸ Today's free Gemini limit is used up. I'll continue "
                                    f"automatically around {self.llm.resume_time}.\n"
                                    + self.status_text())
        self.handle_messages()

    def run_forever(self):
        self.assistant.send("🤖 Course agents started. Send /status anytime, /help for commands.")
        sleep = self.config.get("loop_sleep_seconds", 60)
        while True:
            try:
                self.cycle()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                traceback.print_exc()
                self.assistant.send(f"⚠️ Agent error (I'll keep running): {str(exc)[:300]}")
            # stay responsive to Telegram while waiting
            for _ in range(max(1, sleep // 5)):
                time.sleep(5)
                try:
                    self.handle_messages()
                except Exception:
                    pass

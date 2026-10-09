"""SQLite memory shared by all agents: opportunities, questions for you, settings."""
import datetime
import os
import re
import sqlite3

COLUMNS = ["title", "organizer", "url", "application_url", "location", "dates",
           "deadline", "fee", "funding", "eligibility", "topics"]


def normalize_url(url):
    url = (url or "").strip().lower()
    url = re.sub(r"^https?://(www\.)?", "", url)
    return url.split("?")[0].split("#")[0].rstrip("/")


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path="data/agent.db"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS courses(
            id TEXT PRIMARY KEY, title TEXT, organizer TEXT, url TEXT, application_url TEXT,
            location TEXT, dates TEXT, deadline TEXT, fee TEXT, funding TEXT,
            eligibility TEXT, topics TEXT, fit_score INTEGER, fit_reason TEXT,
            status TEXT DEFAULT 'new', notes TEXT, discovered_at TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS questions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, course_id TEXT, field TEXT,
            question TEXT, answer TEXT, tg_message_id INTEGER, status TEXT DEFAULT 'open',
            asked_at TEXT, answered_at TEXT);
        CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY, title TEXT, company TEXT, url TEXT, apply_url TEXT,
            location TEXT, kind TEXT, salary TEXT, source TEXT, description TEXT,
            deadline TEXT, fit_score INTEGER, fit_reason TEXT, cover_letter TEXT,
            status TEXT DEFAULT 'new', notes TEXT, discovered_at TEXT, updated_at TEXT);
        """)
        cols = [r["name"] for r in self.db.execute("PRAGMA table_info(courses)")]
        if "verified" not in cols:
            self.db.execute("ALTER TABLE courses ADD COLUMN verified INTEGER DEFAULT 0")
        self.db.commit()

    # ---------- opportunities ----------
    def add(self, item):
        """Insert a new opportunity. Returns True if it was new."""
        cid = normalize_url(item.get("url"))
        if not cid:
            return False
        title = (item.get("title") or "").strip().lower()
        if self.db.execute("SELECT 1 FROM courses WHERE id=? OR lower(title)=?",
                           (cid, title)).fetchone():
            return False
        values = [str(item.get(c)) if item.get(c) is not None else None for c in COLUMNS]
        self.db.execute(
            f"INSERT INTO courses(id,{','.join(COLUMNS)},status,discovered_at,updated_at) "
            f"VALUES (?,{','.join('?' * len(COLUMNS))},'new',?,?)",
            [cid, *values, now(), now()])
        self.db.commit()
        return True

    def update(self, cid, **fields):
        fields["updated_at"] = now()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE courses SET {sets} WHERE id=?", [*fields.values(), cid])
        self.db.commit()

    def get(self, cid):
        return self.db.execute("SELECT * FROM courses WHERE id=?", (cid,)).fetchone()

    def by_status(self, *statuses):
        marks = ",".join("?" * len(statuses))
        return self.db.execute(
            f"SELECT * FROM courses WHERE status IN ({marks}) "
            "ORDER BY (deadline IS NULL OR deadline='None'), deadline, fit_score DESC",
            statuses).fetchall()

    def all(self):
        return self.db.execute(
            "SELECT * FROM courses ORDER BY fit_score DESC, deadline").fetchall()

    def status_counts(self):
        return {r[0]: r[1] for r in self.db.execute(
            "SELECT status, COUNT(*) FROM courses GROUP BY status")}

    # ---------- jobs ----------
    def add_job(self, job):
        jid = normalize_url(job.get("url"))
        if not jid:
            return False
        key = f"{(job.get('title') or '').strip().lower()}|{(job.get('company') or '').strip().lower()}"
        if self.db.execute("SELECT 1 FROM jobs WHERE id=? OR lower(title)||'|'||lower(company)=?",
                           (jid, key)).fetchone():
            return False
        cols = ["title", "company", "url", "apply_url", "location", "kind", "salary",
                "source", "description", "deadline"]
        vals = [str(job.get(c))[:6000] if job.get(c) is not None else None for c in cols]
        self.db.execute(
            f"INSERT INTO jobs(id,{','.join(cols)},status,discovered_at,updated_at) "
            f"VALUES (?,{','.join('?' * len(cols))},'new',?,?)", [jid, *vals, now(), now()])
        self.db.commit()
        return True

    def update_job(self, jid, **fields):
        fields["updated_at"] = now()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*fields.values(), jid])
        self.db.commit()

    def get_job(self, jid):
        return self.db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()

    def jobs_by_status(self, *statuses):
        marks = ",".join("?" * len(statuses))
        return self.db.execute(
            f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY fit_score DESC, discovered_at",
            statuses).fetchall()

    def job_counts(self):
        return {r[0]: r[1] for r in self.db.execute(
            "SELECT status, COUNT(*) FROM jobs GROUP BY status")}

    # ---------- questions for the applicant ----------
    def add_question(self, question, course_id=None, field=None):
        """Create a question unless the same one is already open or answered.
        Returns (id, is_new)."""
        row = self.db.execute(
            "SELECT id FROM questions WHERE lower(question)=lower(?)",
            (question,)).fetchone()
        if row:
            if course_id:  # same question needed by another course: link it
                self.db.execute("UPDATE questions SET course_id=COALESCE(course_id, ?) "
                                "WHERE id=?", (course_id, row["id"]))
                self.db.commit()
            return row["id"], False
        cur = self.db.execute(
            "INSERT INTO questions(course_id, field, question, asked_at) VALUES (?,?,?,?)",
            (course_id, field, question, now()))
        self.db.commit()
        return cur.lastrowid, True

    def question_skipped(self, question):
        return bool(self.db.execute(
            "SELECT 1 FROM questions WHERE lower(question)=lower(?) AND status='skipped'",
            (question,)).fetchone())

    def question(self, qid):
        return self.db.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()

    def question_by_message(self, message_id):
        return self.db.execute("SELECT * FROM questions WHERE tg_message_id=?",
                               (message_id,)).fetchone()

    def set_question(self, qid, **fields):
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE questions SET {sets} WHERE id=?", [*fields.values(), qid])
        self.db.commit()

    def answer_question(self, qid, answer):
        self.set_question(qid, answer=answer.strip(), status="answered", answered_at=now())

    def open_questions(self):
        return self.db.execute(
            "SELECT * FROM questions WHERE status='open' ORDER BY id").fetchall()

    def answered_questions(self):
        return self.db.execute(
            "SELECT * FROM questions WHERE status='answered' ORDER BY id").fetchall()

    def course_has_open_questions(self, course_id):
        return bool(self.db.execute(
            "SELECT 1 FROM questions WHERE course_id=? AND status='open'",
            (course_id,)).fetchone())

    # ---------- small key/value settings ----------
    def kv_get(self, key, default=None):
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default

    def kv_set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (key, str(value)))
        self.db.commit()

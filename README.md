# AI Course Application Agents

A team of AI agents that finds AI / machine-learning short courses, summer schools and
workshops (Europe, UK, Germany, online), checks them, writes your applications and
submits them — running 24/7 on your computer. When they need something only you know,
they **ask you on Telegram** and continue as soon as you reply.

Free to run: Google Gemini (free tier) + DuckDuckGo search.

## The agents

| Agent | What it does |
|---|---|
| **Profile agent** | Reads your CV (`documents/cv.pdf`) and builds your profile; asks you only for missing basics |
| **Scout agent** | Checks known course websites + runs web searches every 24 h |
| **Evaluator agent** | Scores each opportunity for fit and eligibility (0–100) |
| **Verifier agent** | Re-reads the official page: is it real, open, correct deadline, real application link? |
| **Writer + Reviewer agents** | Draft each essay answer, critique it, remove anything untrue, respect word limits |
| **Applicant agent** | Opens the form in a real browser, fills every page, uploads your CV, submits |
| **Assistant agent** | Your Telegram contact: questions, results, daily status, commands |
| **Orchestrator** | Runs everything in a loop, with daily limits and retry limits |

## Job applications (ML / AI engineering)

| Agent | What it does |
|---|---|
| **Job scout** | Every 12 h: remote jobs open to Ethiopia (Remotive, RemoteOK, Himalayas), visa-sponsored jobs (Arbeitnow + web), jobs in Ethiopia (web search) |
| **Job evaluator** | Scores each posting for fit, seniority and whether someone in Ethiopia can legally take it |
| **Writer** | Writes a tailored cover letter for each good match (only true facts from your CV) |
| **Approval** | Sends you the job + cover letter on Telegram — reply **YES** to apply, **NO** to skip |
| **Applicant** | Applies on the company's own form (uploads CV + cover letter). LinkedIn / Indeed / Glassdoor are never automated — you get the link instead |

Salary, work authorisation, notice period and similar questions are always asked on
Telegram, never guessed. Settings are under `jobs:` in `config.yaml`
(`require_approval`, `proposals_per_day`, `min_fit_score`, `scopes`). Send `/jobs` for your pipeline.

## What only you can do

Create the free Gemini key, put your CV in the folder, sign into Google once, and answer
the agents' questions. Agents **cannot** solve CAPTCHAs, pay fees, or apply for your visa —
they'll send you the link when that's needed.

---

## Setup (Windows, once)

1. Install **Python 3.11+** from https://www.python.org/downloads/ — tick **"Add Python to PATH"**.
2. Unzip to e.g. `C:\ai-course-agent`.
3. Double-click **`setup.bat`** (5–10 min). It creates a `.env` file.
4. Free Gemini key: https://aistudio.google.com → Get API key → Create API key.
   Open `.env` in Notepad: `GEMINI_API_KEY=your-key`
5. Telegram (strongly recommended — it's how agents talk to you):
   - In Telegram message **@BotFather** → `/newbot` → copy the token
   - Send any message to your new bot
   - Message **@userinfobot** → copy your id
   - In `.env`: `TELEGRAM_BOT_TOKEN=...` and `TELEGRAM_CHAT_ID=...`
6. Put your CV (text PDF, not a scan) at **`documents\cv.pdf`**.
7. Optional: check `config.yaml` — your role, projects and goals are already there. You can
   leave other profile fields empty; the agents read your CV and ask for the rest.

## Start

Double-click **`run.bat`**:
1. **Check my setup** — fix anything it reports
2. **Sign in to Google** — once (many schools use Google Forms)
3. **START THE AGENTS** — leave the window open (minimise it)

Or just double-click **`start_agents.bat`** any time.

**Start automatically when your PC turns on:** press `Win+R`, type `shell:startup`, Enter,
and put a shortcut to `start_agents.bat` in that folder.

The first search takes 10–20 minutes (free-tier pauses). Then you'll get a status
message on Telegram.

## Talking to your agents (Telegram)

| You send | Result |
|---|---|
| reply to a ❓ question message | answer saved, the agents continue that application |
| `5: Yes` | answers question 5 |
| `/status` | progress summary |
| `/questions` | re-sends all unanswered questions |
| `/skip 5` | skip question 5 |
| `/pause` / `/resume` | stop / restart applying (searching continues) |

Only your own Telegram chat can control the agents; messages from anyone else are ignored.
No Telegram? Questions appear in the window — answer them with menu option 7.

## Safety limits (`config.yaml`)

| Setting | Default | Meaning |
|---|---|---|
| `auto_submit` | true | false = fills forms, waits for you to press Enter |
| `min_fit_score` | 65 | only apply to good matches |
| `max_applications_per_run` / `_per_day` | 3 / 5 | caps |
| `max_attempts_per_course` | 3 | then it asks you to apply by hand |
| `scout_every_hours` | 24 | search frequency |

Agents only use facts from `config.yaml`, your CV and your answers — they never invent
qualifications. Every submission is saved in `logs\` (answers + screenshots).

## Course statuses

`queued` ready to apply · `waiting` applications not open yet (re-checked daily) ·
`applied` ✅ · `submitted_unconfirmed` check your email · `needs_input` waiting for your
answer · `blocked` CAPTCHA/login/payment — finish by hand · `failed` see `logs\` ·
`skipped` not eligible / closed / low fit.

## Commands (any OS)

```
python main.py check | login | auto | run | profile | answer | list | export
python main.py discover | evaluate | verify | apply [--retry]
```
Linux/Mac: `bash setup.sh`, then `bash run.sh auto`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` not recognized | Reinstall Python with "Add Python to PATH" |
| `Please set the GEMINI_API_KEY` | Key must be in `.env` (exact file name `.env`) |
| Telegram test fails | Send your bot a message first; check token and id in `.env` |
| "CV has no readable text" | Export your CV from Word/Google Docs as PDF (not a photo/scan) |
| "free-tier limit reached, waiting…" | Normal — it continues by itself |
| A form fills wrongly | Send the screenshot from `logs\` to get the code adjusted |

## Files

```
main.py                   commands
config.yaml               settings + profile
.env                      your secret keys (from .env.example) — never share
agent/orchestrator.py     runs all agents in a loop
agent/profile_agent.py    CV reading + profile building
agent/finder.py           Scout + Evaluator agents
agent/verifier.py         Verifier agent
agent/writer.py           Writer + Reviewer agents
agent/form_filler.py      Applicant agent (browser)
agent/assistant.py        Assistant agent (Telegram)
agent/llm.py              Gemini connection
agent/websearch.py        free search + page reading
agent/storage.py          shared memory (data/agent.db)
setup.bat / run.bat / start_agents.bat    Windows launchers
```

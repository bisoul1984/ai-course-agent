"""AI Course Application Agents

  python main.py check            test your setup (keys, search, Telegram, CV, browser)
  python main.py auto             START THE AGENTS: run 24/7, search daily, apply, ask you on Telegram
  python main.py run              one full cycle now (search + verify + apply), then stop
  python main.py discover|evaluate|verify|apply [--retry]   run one agent step
  python main.py profile          show the profile the agents will use
  python main.py answer           answer open questions in this window (if no Telegram)
  python main.py list | export    show tracked courses / save them to courses.csv
  python main.py login            sign into Google once in the agents' browser
"""
import argparse
import copy
import csv
import json
import os

import yaml
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
load_dotenv(os.path.join(HERE, ".env"))

from agent.storage import Store  # noqa: E402


def load_config():
    with open("config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def make_llm(config):
    from agent.llm import LLM
    return LLM(config["model"], config.get("api_key_env", "GEMINI_API_KEY"),
               config.get("min_seconds_between_calls", 7))


def show_list(store):
    rows = store.all()
    if not rows:
        print("No opportunities tracked yet. Run: python main.py run")
    for r in rows:
        print(f"{(r['status'] or ''):<22} {str(r['fit_score'] or '-'):>3}  "
              f"{str(r['deadline'] or '?'):<10}  {r['title']}")
    open_q = store.open_questions()
    if open_q:
        print(f"\n{len(open_q)} open question(s) for you. Answer on Telegram or: python main.py answer")


def export_csv(store):
    rows = store.all()
    with open("courses.csv", "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        if rows:
            writer.writerow(rows[0].keys())
            writer.writerows([tuple(r) for r in rows])
    print(f"Saved {len(rows)} opportunities to {os.path.abspath('courses.csv')}")


def check(config, store):
    from agent.assistant import Assistant
    from agent.profile_agent import missing_basics, effective_profile
    from agent.websearch import search
    problems = 0

    print("1) Gemini API key...", end=" ", flush=True)
    llm = None
    try:
        llm = make_llm(config)
        reply = llm.ask("Reply with exactly: OK", "Say OK")
        print("works" if "OK" in reply.upper() else f"unexpected reply: {reply[:60]}")
    except Exception as exc:
        problems += 1
        print(f"PROBLEM: {exc}")

    print("2) Free web search...", end=" ", flush=True)
    results = search("machine learning summer school", max_results=3)
    print(f"works ({len(results)} results)" if results else "PROBLEM: no results (internet?)")
    problems += not results

    print("3) Telegram...", end=" ", flush=True)
    assistant = Assistant(config.get("telegram"), store)
    if not assistant.enabled:
        print("not set up - agents will ask questions in this window instead (python main.py answer)")
    else:
        print("works - check your phone" if assistant.send("✅ Test message from your course agents")
              else "PROBLEM: check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, and send your bot a message first")

    print("4) CV + profile...", end=" ", flush=True)
    cv = config["profile"].get("cv_path")
    if not os.path.exists(cv or ""):
        print(f"no CV at {cv} - add it so the Profile agent can read it")
        problems += 1
    elif llm:
        prof = effective_profile(llm, config, store, log=lambda *_: None)
        missing = missing_basics(prof)
        print("complete" if not missing else
              f"agents will ask you for: {', '.join(missing)}")

    print("5) Browser...", end=" ", flush=True)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        print("works")
    except Exception as exc:
        problems += 1
        print(f"PROBLEM: run 'python -m playwright install chromium' ({str(exc)[:80]})")

    print("\nReady! Start the agents with: python main.py auto" if not problems
          else "\nFix the problems above, then run the check again.")


def login():
    from playwright.sync_api import sync_playwright
    from agent.form_filler import BROWSER_PROFILE
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(BROWSER_PROFILE, headless=False)
        ctx.new_page().goto("https://accounts.google.com")
        input("Sign in to your Google account in the browser, then press Enter here...")
        ctx.close()


def main():
    parser = argparse.ArgumentParser(description="AI course application agents")
    parser.add_argument("command", choices=["check", "auto", "run", "discover", "evaluate",
                                            "verify", "apply", "profile", "answer", "list",
                                            "export", "login"])
    parser.add_argument("--retry", action="store_true",
                        help="with apply: also retry needs_input and failed ones")
    args = parser.parse_args()

    config, store = load_config(), Store()
    simple = {"list": lambda: show_list(store), "export": lambda: export_csv(store),
              "login": login}
    if args.command in simple:
        return simple[args.command]()
    if args.command == "check":
        return check(config, store)
    if args.command == "answer":
        from agent.assistant import Assistant
        return Assistant(None, store).answer_in_console()

    from agent.orchestrator import Orchestrator
    llm = make_llm(config)
    orch = Orchestrator(config, store, llm)

    if args.command == "auto":
        import socket
        lock = socket.socket()
        try:  # only one copy of the agents may run at a time
            lock.bind(("127.0.0.1", 47831))
        except OSError:
            print("The agents are already running in another window. Close this one.")
            input("Press Enter to close...")
            return
        return orch.run_forever()
    if args.command == "run":
        return orch.cycle(force_scout=True)
    if args.command == "profile":
        prof = orch.profile()
        print(json.dumps(prof, ensure_ascii=False, indent=2))
        from agent.profile_agent import missing_basics
        missing = missing_basics(prof)
        if missing:
            print("\nStill missing (agents will ask you):", ", ".join(missing))
        return
    if args.command == "discover":
        from agent.finder import discover
        return print(discover(llm, store, config), "new opportunities")
    if args.command == "evaluate":
        from agent.finder import evaluate
        cfg = copy.deepcopy(config)
        cfg["profile"] = orch.profile()
        return print(evaluate(llm, store, cfg), "queued")
    if args.command == "verify":
        from agent.verifier import verify
        return print(verify(llm, store), "verified and ready")
    if args.command == "apply":
        return orch.apply_queued(retry=args.retry)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")

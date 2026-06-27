"""
AI WarKing — Copilot Bridge klijent
Komunicira sa server.py (127.0.0.1:5000) putem /copilot_api.
Nema API ključeva. Nema interneta. Sve lokalno.

Pokretanje:
    python copilot_client.py              # interaktivni REPL
    python copilot_client.py -m "tekst"  # jedna poruka
    python copilot_client.py --fix main.py
    python copilot_client.py --read main.py
"""

import sys
import json
import time
import argparse
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:5000"

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _post(endpoint: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))

def _get(endpoint: str) -> dict:
    req = urllib.request.Request(f"{BASE_URL}{endpoint}", method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))

# ── Core funkcije ─────────────────────────────────────────────────────────────

def check_server() -> bool:
    try:
        r = _get("/status")
        print(f"[SERVER] online | folder: {r.get('base_dir') or 'nije izabran'} "
              f"| sesija: {r.get('session_id','')[:8]}")
        return True
    except Exception:
        print("[GREŠKA] Server nije dostupan. Pokreni server.py.")
        return False

def send_message(message: str, auto_fix_path: str = None, sync: bool = True) -> str:
    """Pošalji poruku i vrati odgovor."""
    body = {"message": message}
    if auto_fix_path:
        body["auto_fix_path"] = auto_fix_path
    resp = _post("/copilot_api", body)
    return resp.get("response", f"[GREŠKA] {resp}")

def tool_call(tool: str, path: str = None, content: str = None) -> str:
    """Pošalji tool komandu prirodnim jezikom."""
    if tool == "list":
        msg = "list"
    elif tool == "read" and path:
        msg = f"pročitaj {path}"
    elif tool == "write" and path:
        msg = f"napiši u {path} {content or ''}"
    elif tool == "run" and path:
        msg = f"pokreni {path}"
    elif tool == "fix" and path:
        msg = f"popravi {path}"
    elif tool == "delete" and path:
        msg = f"obriši {path}"
    else:
        msg = tool
    return send_message(msg)

def show_memory():
    r = _get("/memory")
    print(f"\n[MEMORY BUFFER] — {r.get('count', 0)} poruka u kontekstu")
    for i, m in enumerate(r.get("memory", []), 1):
        role = m.get("role", "?")
        content = m.get("content", "")[:120].replace("\n", " ")
        print(f"  {i:2}. [{role.upper()}] {content}")

def clear_memory():
    r = _post("/memory/clear", {})
    print(f"[MEMORY] {r.get('message', 'ok')}")

def show_sessions():
    r = _get("/sessions")
    print(f"\n[SESIJE] — {len(r)} snimljenih sesija u ./sessions/")
    for s in r[-10:]:
        print(f"  {s['file']}  |  {s['started'][:19]}  |  {s['messages']} poruka")

# ── Interaktivni REPL ─────────────────────────────────────────────────────────

HELP_TEXT = """
Naredbe:
  <poruka>              Pošalji poruku agentu
  /read <path>          Pročitaj fajl
  /write <path>         Upiši u fajl (interaktivno)
  /run <path>           Pokreni Python fajl
  /fix <path>           Auto-fix grešaka u fajlu
  /list                 Prikaži fajlove u folderu
  /memory               Prikaži memory buffer
  /clear                Obriši memory buffer
  /sessions             Prikaži sesije
  /status               Status servera
  /help                 Ova poruka
  /exit ili /quit       Izlaz
"""

def repl():
    print("=" * 60)
    print("  Copilot Local Bridge — klijent")
    print("  Sve radi lokalno (127.0.0.1:5000)")
    print("=" * 60)
    if not check_server():
        sys.exit(1)
    print(HELP_TEXT)

    while True:
        try:
            line = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nDoviđenja.")
            break

        if not line:
            continue

        if line in ("/exit", "/quit"):
            print("Doviđenja.")
            break
        elif line == "/help":
            print(HELP_TEXT)
        elif line == "/memory":
            show_memory()
        elif line == "/clear":
            clear_memory()
        elif line == "/sessions":
            show_sessions()
        elif line == "/status":
            check_server()
        elif line == "/list":
            resp = tool_call("list")
            print(f"[FAJLOVI]\n{resp}")
        elif line.startswith("/read "):
            path = line[6:].strip()
            resp = tool_call("read", path=path)
            print(f"[SADRŽAJ {path}]\n{resp}")
        elif line.startswith("/run "):
            path = line[5:].strip()
            resp = tool_call("run", path=path)
            print(f"[RUN {path}]\n{resp}")
        elif line.startswith("/fix "):
            path = line[5:].strip()
            print(f"[FIX] Pokrećem auto-fix za: {path}")
            resp = send_message(f"Pokreni auto-fix za {path}", auto_fix_path=path)
            print(f"[FIX ODGOVOR]\n{resp}")
        elif line.startswith("/write "):
            path = line[7:].strip()
            print(f"Upiši sadržaj (završi sa EOF / Ctrl+Z/D):")
            lines = []
            try:
                while True:
                    lines.append(input())
            except EOFError:
                pass
            content = "\n".join(lines)
            resp = tool_call("write", path=path, content=content)
            print(f"[WRITE] {resp}")
        else:
            # Obična poruka
            resp = send_message(line)
            print(f"\n[AGENT]\n{resp}\n")

# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Copilot Local Bridge klijent")
    parser.add_argument("--message", "-m", help="Pošalji jednu poruku i izađi")
    parser.add_argument("--fix", help="Auto-fix za zadani fajl")
    parser.add_argument("--read", help="Pročitaj fajl")
    parser.add_argument("--run", help="Pokreni fajl")
    parser.add_argument("--list", action="store_true", help="Prikaži fajlove")
    parser.add_argument("--memory", action="store_true", help="Prikaži memory buffer")
    parser.add_argument("--sessions", action="store_true", help="Prikaži sesije")
    args = parser.parse_args()

    if args.message:
        if not check_server():
            sys.exit(1)
        print(send_message(args.message))
    elif args.fix:
        if not check_server():
            sys.exit(1)
        print(send_message(f"Pokreni auto-fix za {args.fix}", auto_fix_path=args.fix))
    elif args.read:
        if not check_server():
            sys.exit(1)
        print(tool_call("read", path=args.read))
    elif args.run:
        if not check_server():
            sys.exit(1)
        print(tool_call("run", path=args.run))
    elif args.list:
        if not check_server():
            sys.exit(1)
        print(tool_call("list"))
    elif args.memory:
        if not check_server():
            sys.exit(1)
        show_memory()
    elif args.sessions:
        if not check_server():
            sys.exit(1)
        show_sessions()
    else:
        repl()

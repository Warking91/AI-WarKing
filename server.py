"""
AI WarKing — Copilot Bridge
Lokalni AI asistent koji razumije prirodni jezik.
Bez API ključeva. Bez interneta. Sve lokalno.
http://127.0.0.1:5000
"""

import os, re, json, time, subprocess, threading, datetime, uuid, queue, traceback
import tkinter as tk
from tkinter import filedialog, ttk, scrolledtext, simpledialog, messagebox
from flask import Flask, request, jsonify

app      = Flask(__name__)
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS = os.path.join(ROOT_DIR, "sessions")
os.makedirs(SESSIONS, exist_ok=True)

BASE_DIR = None
session  = {"id": str(uuid.uuid4()), "started": datetime.datetime.now().isoformat(), "messages": []}
memory   = []          # kratkotrajna memorija, max 30
MEM_MAX  = 30
mem_lock = threading.Lock()
state    = {"status": "running", "paused": False, "error": None}
st_lock  = threading.Lock()

_log_cb      = None
_chat_cb     = None
_last_file   = None   # zadnji fajl koji je korišten (kontekst)
_pending     = {}     # čeka na dopunski input od korisnika

# ─────────────────────────────────────────────────────────────────────────────
# Core helpers
# ─────────────────────────────────────────────────────────────────────────────

def log(txt):
    print(txt)
    if _log_cb:
        try: _log_cb(txt)
        except: pass

def save_session():
    with open(os.path.join(SESSIONS, f"s_{session['id'][:8]}.json"), "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2, ensure_ascii=False)

def remember(role, content):
    session["messages"].append({"id": uuid.uuid4().hex[:6], "time": datetime.datetime.now().isoformat(), "role": role, "content": content})
    save_session()
    with mem_lock:
        memory.append({"role": role, "content": content})
        if len(memory) > MEM_MAX:
            memory.pop(0)
    if _chat_cb:
        try: _chat_cb(role, content)
        except: pass

def safe(path):
    if not BASE_DIR: raise PermissionError("Folder nije izabran — klikni 📂 Folder u toolbaru.")
    full = os.path.abspath(os.path.join(BASE_DIR, path))
    if not full.startswith(os.path.abspath(BASE_DIR)):
        raise PermissionError("Izlaz iz sandbox foldera nije dozvoljen.")
    return full

def project_files():
    if not BASE_DIR: return []
    out = []
    for r, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
        for f in files:
            out.append(os.path.relpath(os.path.join(r, f), BASE_DIR).replace("\\", "/"))
    return out

# ─────────────────────────────────────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────────────────────────────────────

def do_write(path, content):
    full = safe(path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return path, len(content)

def do_read(path):
    with open(safe(path), "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def do_run(path):
    r = subprocess.run(["python", safe(path)], capture_output=True, text=True, cwd=BASE_DIR, timeout=30)
    return r.stdout, r.stderr, r.returncode

def do_delete(path):
    os.remove(safe(path))

def do_mkdir(path):
    os.makedirs(safe(path), exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Intent parser  —  razumije prirodni jezik, bez @@TOOL:
# ─────────────────────────────────────────────────────────────────────────────

# Ekstraktuje ime fajla iz teksta (prva stvar koja izgleda kao path)
_FILE_RE = re.compile(r'(["\']?)(\S+\.\w{1,6})\1')

def _find_file(text):
    m = _FILE_RE.search(text)
    return m.group(2) if m else None

def _find_content(text, after_file):
    """Vraća sadržaj koji treba upisati u fajl."""
    # Traži iza ključnih riječi
    m = re.search(
        r'(?:i\s+(?:unutra\s+)?)?(?:napisi|napiši|stavi|piše|pise|sadrzi|sadržaj|'
        r'content|tekst|text|write|sa\s+(?:sadrzajem|sadržajem)|unutra)\s*[:\-]?\s*(.*)',
        after_file, re.IGNORECASE | re.DOTALL
    )
    if m:
        return m.group(1).strip().strip('"\'')
    return ""

def parse_intent(msg):
    """
    Vraća dict: {"action": str, "path": str, "content": str, "raw": str}
    Akcije: write, read, run, fix, delete, mkdir, list, ask_filename, chat
    """
    global _pending
    raw   = msg.strip()
    lower = raw.lower()
    f     = _find_file(raw)

    # ── PENDING — korisnik odgovara na pitanje o nazivu fajla ─────────────────
    if _pending.get("waiting") == "filename":
        _pending = {}
        # ako je odgovor samo ime fajla ili "test.py" ili "napravi test.py"
        fname = _find_file(raw)
        if not fname:
            # možda je samo napisao "test.py" bez ekstenzije → tretiramo kao ime
            stripped = raw.strip().split()[0] if raw.strip().split() else ""
            if re.match(r'^[\w\-]+\.\w+$', stripped):
                fname = stripped
            else:
                fname = raw.strip().split()[0]
                if not re.search(r'\.', fname):
                    fname = fname + ".txt"
        return {"action": "write", "path": fname, "content": _pending.get("content", ""), "raw": raw}

    # ── LIST ─────────────────────────────────────────────────────────────────
    list_kw = r'^(list|ls|dir|prikaži\s*fajlove|prikazi\s*fajlove|koji\s*fajlovi|'  \
              r'šta\s*ima|sta\s*ima|što\s*ima|sto\s*ima|fajlovi|lista|listaj)$'
    if re.match(list_kw, lower):
        return {"action": "list", "path": "", "content": "", "raw": raw}

    # ── WRITE / CREATE ────────────────────────────────────────────────────────
    write_verbs = r'(?:napravi|kreiraj|stvori|napiši|napisi|upiši|upisi|stavi|dodaj|'  \
                  r'write|create|make|touch)'
    write_m = re.match(
        rf'^{write_verbs}\s+(?:mi\s+)?(?:fajl\s+|file\s+|u\s+)?(.*)$',
        raw, re.IGNORECASE | re.DOTALL
    )
    if write_m:
        rest   = write_m.group(1).strip()
        file_m = _FILE_RE.search(rest)
        if file_m:
            fname   = file_m.group(2)
            after   = rest[file_m.end():].strip()
            content = _find_content(raw, after) or _find_content(raw, rest)
            if not content and after and not re.match(r'^(i|sa|s|with|da|te|pa)\s*$', after.lower()):
                content = after.strip().strip('"\':')
            return {"action": "write", "path": fname, "content": content, "raw": raw}
        else:
            # "napravi fajl" bez naziva → pitaj korisnika
            content_m = _find_content(raw, rest)
            return {"action": "ask_filename", "path": "", "content": content_m, "raw": raw}

    # "u X.txt napisi/stavi Y"
    in_file = re.match(
        r'^u\s+(\S+\.\w+)\s+(?:napisi|napiši|stavi|upiši|upisi|dodaj|write)\s*[:\-]?\s*(.*)',
        raw, re.IGNORECASE | re.DOTALL
    )
    if in_file:
        return {"action": "write", "path": in_file.group(1), "content": in_file.group(2).strip(), "raw": raw}

    # "napisi/stavi u X.txt Y"
    write2 = re.match(
        r'^(?:napisi|napiši|stavi|upiši|upisi|write)\s+(?:u\s+)?(\S+\.\w+)\s*(.*)',
        raw, re.IGNORECASE | re.DOTALL
    )
    if write2:
        return {"action": "write", "path": write2.group(1), "content": write2.group(2).strip(), "raw": raw}

    # "napisi Y u X.txt"
    write3 = re.match(
        r'^(?:napisi|napiši|stavi|upiši|upisi|write)\s+(.*?)\s+u\s+(\S+\.\w+)',
        raw, re.IGNORECASE | re.DOTALL
    )
    if write3:
        return {"action": "write", "path": write3.group(2), "content": write3.group(1).strip(), "raw": raw}

    # ── READ ─────────────────────────────────────────────────────────────────
    read_m = re.match(
        r'^(?:procitaj|pročitaj|otvori|prikaži\s*fajl|pokazi\s*fajl|read|show|cat|open)\s+(.+)',
        raw, re.IGNORECASE
    )
    if read_m:
        fp = _find_file(read_m.group(1))
        if fp: return {"action": "read", "path": fp, "content": "", "raw": raw}

    # ── RUN ──────────────────────────────────────────────────────────────────
    run_m = re.match(
        r'^(?:pokreni|izvrsi|izvrši|pusti|startaj|run|execute|start)\s+(.+)',
        raw, re.IGNORECASE
    )
    if run_m:
        fp = _find_file(run_m.group(1))
        if fp: return {"action": "run", "path": fp, "content": "", "raw": raw}

    # ── FIX ──────────────────────────────────────────────────────────────────
    fix_m = re.match(
        r'^(?:popravi|ispravi|fix|debug|sredi|popravi\s*grešku|popravi\s*gresku)\s*(.*)',
        raw, re.IGNORECASE
    )
    if fix_m:
        fp = _find_file(fix_m.group(1)) or _last_file
        if fp: return {"action": "fix", "path": fp, "content": "", "raw": raw}

    # ── "neradi" / "ima gresku" → auto-fix zadnjeg fajla ────────────────────
    if any(k in lower for k in ("neradi", "ne radi", "ima grešku", "ima gresku",
                                 "popravi ga", "popravi je", "ispravi", "sredi")):
        if _last_file:
            return {"action": "fix", "path": _last_file, "content": "", "raw": raw}

    # ── DELETE ───────────────────────────────────────────────────────────────
    del_m = re.match(
        r'^(?:obrisi|obriši|ukloni|brisi|briši|delete|rm|remove)\s+(.+)',
        raw, re.IGNORECASE
    )
    if del_m:
        fp = _find_file(del_m.group(1))
        if fp: return {"action": "delete", "path": fp, "content": "", "raw": raw}

    # ── MKDIR ────────────────────────────────────────────────────────────────
    mkdir_m = re.match(
        r'^(?:mkdir|napravi\s+folder|kreiraj\s+folder|novi\s+folder|new\s+folder)\s+(\S+)',
        raw, re.IGNORECASE
    )
    if mkdir_m:
        return {"action": "mkdir", "path": mkdir_m.group(1), "content": "", "raw": raw}

    # ── Ako postoji fajl u poruci i kontekst sugerira akciju ─────────────────
    if f:
        if any(k in lower for k in ("napisi", "napiši", "stavi", "upiši", "upisi", "write", "dodaj")):
            return {"action": "write", "path": f, "content": _find_content(raw, raw), "raw": raw}
        if any(k in lower for k in ("procitaj", "pročitaj", "otvori", "read", "prikaži", "pokazi")):
            return {"action": "read", "path": f, "content": "", "raw": raw}
        if any(k in lower for k in ("pokreni", "izvrsi", "izvrši", "run", "execute", "radi")):
            return {"action": "run", "path": f, "content": "", "raw": raw}
        if any(k in lower for k in ("popravi", "ispravi", "fix", "debug")):
            return {"action": "fix", "path": f, "content": "", "raw": raw}
        if any(k in lower for k in ("obrisi", "obriši", "ukloni", "delete", "rm")):
            return {"action": "delete", "path": f, "content": "", "raw": raw}

    return {"action": "chat", "path": "", "content": "", "raw": raw}

# ─────────────────────────────────────────────────────────────────────────────
# AI WarKing brain  —  ličnost + odgovori
# ─────────────────────────────────────────────────────────────────────────────

import random, ast, textwrap

# ─────────────────────────────────────────────────────────────────────────────
# Auto-fix engine  —  pokušava stvarno popraviti Python kod
# ─────────────────────────────────────────────────────────────────────────────

def _auto_fix(path):
    global _last_file
    _last_file = path

    try:
        code = do_read(path)
    except FileNotFoundError:
        return f"❌ Fajl `{path}` ne postoji. Napiši `list` da vidiš šta ima."
    except Exception as e:
        return f"❌ Ne mogu pročitati `{path}`: {e}"

    # Provjeri radi li uopće
    try:
        stdout, stderr, rc = do_run(path)
    except FileNotFoundError:
        return f"❌ Python nije pronađen ili `{path}` ne postoji."
    except subprocess.TimeoutExpired:
        return f"⏱ `{path}` se nije završio za 30s — možda beskonačna petlja?"
    except Exception as e:
        return f"❌ Greška pri pokretanju: {e}"

    if rc == 0:
        return f"✅ `{path}` radi bez grešaka! Nema šta popravljati."

    error_text = (stderr or stdout).strip()
    original   = code
    fixed      = code
    applied    = []

    # ── 1. Pokušaj autopep8 ───────────────────────────────────────────────────
    try:
        import autopep8
        fixed = autopep8.fix_code(fixed, options={"aggressive": 2, "max_line_length": 120})
        if fixed != original:
            applied.append("autopep8 (PEP8 formatiranje)")
    except ImportError:
        pass

    # ── 2. Zamijeni tabove sa 4 razmaka ──────────────────────────────────────
    if "\t" in fixed:
        fixed = fixed.expandtabs(4)
        applied.append("tabs → spaces")

    # ── 3. Dodaj nedostajuće dvotačke (if/for/while/def/class bez :) ─────────
    def _add_colons(src):
        kw_re = re.compile(
            r'^(\s*(?:if|elif|else|for|while|def|class|try|except|finally|with)'
            r'(?:\s[^\n#:]+?)?)(\s*)(#.*)?$'
        )
        lines = src.splitlines()
        changed = False
        for i, ln in enumerate(lines):
            m = kw_re.match(ln)
            if m and not ln.rstrip().endswith(':'):
                lines[i] = ln.rstrip() + ':'
                changed = True
        return ("\n".join(lines), changed)

    fixed2, col_changed = _add_colons(fixed)
    if col_changed:
        fixed = fixed2
        applied.append("dodane dvotačke")

    # ── 4. Popravi indentaciju (pokušaj ast.parse) ────────────────────────────
    try:
        ast.parse(fixed)
    except SyntaxError as e:
        # IndentationError — pokušaj autopep8 aggressive ako već nije
        if "indent" in str(e).lower() and "autopep8" not in " ".join(applied):
            try:
                import autopep8
                fixed = autopep8.fix_code(fixed, options={"aggressive": 3})
                applied.append("autopep8 aggressive indentation")
            except ImportError:
                pass

    # ── 5. Dodaj nedostajući __main__ guard ako skript nema ──────────────────
    if (path.endswith(".py") and
        "def main" in fixed and
        'if __name__' not in fixed):
        fixed = fixed.rstrip() + '\n\nif __name__ == "__main__":\n    main()\n'
        applied.append("dodan __main__ guard")

    # ── 6. Popravi neodgovarajuće print pozive (Python 2 stil) ───────────────
    fixed_tmp = re.sub(r'\bprint\s+([^(\n][^\n]*)', r'print(\1)', fixed)
    if fixed_tmp != fixed:
        fixed = fixed_tmp
        applied.append("print → print() (Python 3)")

    # ── Ako je nešto promijenjeno, snimi i provjeri ───────────────────────────
    if fixed != original:
        do_write(path, fixed)
        try:
            o2, e2, rc2 = do_run(path)
        except Exception as ex:
            return f"🔧 Primijenio sam popravke ali ne mogu testirati: {ex}"

        if rc2 == 0:
            out = o2[:400] if o2 else "(bez outputa)"
            return (
                f"✅ **{path}** popravljen i radi!\n\n"
                f"Primijenjeno: {', '.join(applied)}\n\n"
                f"Output:\n```\n{out}\n```"
            )
        else:
            remaining = (e2 or o2)[:500]
            return (
                f"🔧 Primijenjeno: {', '.join(applied)}\n"
                f"Ali ostala je greška:\n\n```\n{remaining}\n```\n\n"
                f"Originalna greška bila:\n```\n{error_text[:300]}\n```\n\n"
                f"Ova greška zahtijeva ručni pregled — otvori fajl u editoru."
            )
    else:
        # Ništa automatski ne možemo promijeniti — prikaži analizu greške
        err_lower = error_text.lower()
        tip = ""
        if "nameerror" in err_lower:
            m = re.search(r"name '(\w+)' is not defined", error_text)
            if m: tip = f"\n💡 `{m.group(1)}` nije definisan — provjeri pravopis ili dodaj `import {m.group(1)}`."
        elif "modulenotfounderror" in err_lower or "importerror" in err_lower:
            m = re.search(r"No module named '(\S+)'", error_text)
            if m: tip = f"\n💡 Instaliraj: `pip install {m.group(1)}`"
        elif "typeerror" in err_lower:
            tip = "\n💡 Provjeri tipove argumenata — možda prosljeđuješ string umjesto broja ili obrnuto."
        elif "zerodivisionerror" in err_lower:
            tip = "\n💡 Dijeljenje s nulom — dodaj provjeru `if x != 0:` prije dijeljenja."
        elif "indexerror" in err_lower:
            tip = "\n💡 Lista je kraća nego što misliš — provjeri dužinu s `len(lista)`."
        elif "keyerror" in err_lower:
            tip = "\n💡 Ključ ne postoji — koristi `dict.get(key, default)` umjesto `dict[key]`."

        return (
            f"🔍 Analizirao sam `{path}` ali ne mogu automatski popraviti ovu grešku.\n\n"
            f"Greška:\n```\n{error_text[:600]}\n```{tip}\n\n"
            f"Otvori fajl u editoru i pogledaj liniju {_extract_line(error_text)}."
        )


def _extract_line(error_text):
    m = re.search(r'line (\d+)', error_text)
    return m.group(1) if m else "?"


PERSONALITY = [
    "Hej, tu sam. Šta radiš?",
    "Spreman! Daj mi neku komandu.",
    "Na usluzi. Dev mod: ON. 🟢",
    "Ovdje sam. Šta kujemo danas?",
]

def ai_respond(intent):
    global _last_file, _pending
    action  = intent["action"]
    path    = intent["path"]
    content = intent["content"]
    raw     = intent["raw"]
    lower   = raw.lower()

    # ── LIST ──────────────────────────────────────────────────────────────────
    if action == "list":
        files = project_files()
        if not files:
            return "📁 Folder je prazan ili nije izabran."
        py  = [f for f in files if f.endswith(".py")]
        ext = {}
        for f in files:
            e = os.path.splitext(f)[1] or "ostalo"
            ext[e] = ext.get(e, 0) + 1
        ext_str = "  ".join(f"{v}×{k}" for k, v in sorted(ext.items()))
        tree = "\n".join(f"  {'🐍' if f.endswith('.py') else '📄'} {f}" for f in files[:40])
        suffix = f"\n  ... i još {len(files)-40}" if len(files) > 40 else ""
        return f"📁 **{os.path.basename(BASE_DIR)}** — {len(files)} fajlova ({ext_str})\n\n{tree}{suffix}"

    # ── ASK FILENAME ─────────────────────────────────────────────────────────
    if action == "ask_filename":
        _pending = {"waiting": "filename", "content": content}
        return "Koji naziv fajla? (npr. `test.py`, `podaci.txt`, `index.html`)"

    # ── WRITE / CREATE ────────────────────────────────────────────────────────
    if action == "write":
        if not path:
            return "Ne znam koji fajl. Reci npr: *napravi hello.txt i unutra napiši Hello World*"
        try:
            _, nbytes = do_write(path, content)
            _last_file = path
            log(f"[WRITE] {path} ({nbytes} ch)")
            return (
                f"✅ **{path}** kreiran/ažuriran ({nbytes} znakova).\n"
                f"{'Sadržaj: `' + content[:60] + ('...' if len(content)>60 else '') + '`' if content else 'Fajl je prazan.'}"
            )
        except Exception as e:
            return f"❌ Greška pri pisanju u `{path}`: {e}"

    # ── READ ──────────────────────────────────────────────────────────────────
    if action == "read":
        try:
            txt = do_read(path)
            _last_file = path
            lines = txt.splitlines()
            preview = "\n".join(lines[:30])
            suffix  = f"\n\n...({len(lines)-30} linija više)" if len(lines) > 30 else ""
            return f"📄 **{path}** ({len(lines)} linija):\n\n```\n{preview}\n```{suffix}"
        except FileNotFoundError:
            return f"❌ Fajl `{path}` ne postoji. Provjeri ime ili napiši `list` da vidiš šta ima."
        except Exception as e:
            return f"❌ Greška: {e}"

    # ── RUN ───────────────────────────────────────────────────────────────────
    if action == "run":
        if not path.endswith(".py"):
            return f"❌ `{path}` nije Python fajl. Mogu pokrenuti samo `.py` fajlove."
        try:
            _last_file = path
            stdout, stderr, rc = do_run(path)
            if rc == 0:
                out = stdout[:800] if stdout else "(nema outputa)"
                return f"▶ **{path}** — završio OK (rc=0)\n\n```\n{out}\n```"
            else:
                err = (stderr or stdout)[:800]
                return (
                    f"💥 **{path}** — greška (rc={rc})\n\n```\n{err}\n```\n\n"
                    f"Napiši `popravi {path}` da probam automatski fixati."
                )
        except FileNotFoundError:
            return f"❌ `{path}` ne postoji."
        except Exception as e:
            return f"❌ Greška pri pokretanju: {e}"

    # ── FIX ───────────────────────────────────────────────────────────────────
    if action == "fix":
        return _auto_fix(path)

    # ── DELETE ────────────────────────────────────────────────────────────────
    if action == "delete":
        try:
            do_delete(path)
            return f"🗑 **{path}** obrisan."
        except FileNotFoundError:
            return f"❌ `{path}` ne postoji."
        except Exception as e:
            return f"❌ Greška: {e}"

    # ── MKDIR ─────────────────────────────────────────────────────────────────
    if action == "mkdir":
        try:
            do_mkdir(path)
            return f"📁 Folder **{path}** kreiran."
        except Exception as e:
            return f"❌ Greška: {e}"

    # ── CHAT — ličnost i razgovor ─────────────────────────────────────────────
    return _chat_brain(raw, lower)


def _chat_brain(raw, lower):
    """Razgovor, šale, pitanja, ličnost."""
    # Pozdrav
    if re.search(r'\b(zdravo|hej|ej|cao|čao|hi|hello|hey|bok|oi|yo)\b', lower):
        folder = os.path.basename(BASE_DIR) if BASE_DIR else None
        files  = project_files()
        ctx    = f"Projekt: **{folder}** ({len(files)} fajlova)" if folder else "Folder nije izabran."
        return f"{random.choice(PERSONALITY)}\n{ctx}"

    # Jesi tu
    if re.search(r'jesi\s+(li\s+)?tu|ima\s+ko|slusas|slušaš', lower):
        return "Tu sam, slušam. 👂 Daj komandu ili pitaj šta hoćeš."

    # Status
    if re.search(r'\b(status|kako\s+si|radi\s+li|sve\s+ok|imaš\s+greške)\b', lower):
        with st_lock: st = state.copy()
        files = project_files()
        with mem_lock: mem = len(memory)
        return (
            f"**Status:** {st['status'].upper()} {'⏸' if st['paused'] else '🟢'}\n"
            f"**Sesija:** {session['id'][:8]} — {len(session['messages'])} poruka\n"
            f"**Memorija:** {mem}/{MEM_MAX}\n"
            f"**Projekt:** {os.path.basename(BASE_DIR) if BASE_DIR else 'nije izabran'} "
            f"({len(files)} fajlova)"
        )

    # Help
    if re.search(r'\b(help|pomozi|šta\s+možeš|sta\s+mozes|komande|naredbe|uputa)\b', lower):
        return (
            "**Šta mogu:**\n\n"
            "📁 `list` — prikaži sve fajlove\n"
            "📄 `pročitaj main.py` — sadržaj fajla\n"
            "✍ `napravi test.txt i unutra napiši Hello` — novi fajl\n"
            "✍ `napiši u main.py: <kod>` — upiši sadržaj\n"
            "▶ `pokreni main.py` — pokreni Python\n"
            "🔧 `popravi main.py` — auto-fix grešaka\n"
            "🗑 `obriši old.py` — brisanje\n"
            "📁 `mkdir src/utils` — novi folder\n\n"
            "Pišeš slobodno, bez ikakvih prefiksa. Razumijem bosanski, engleski, mješano."
        )

    # Pitanja o projektu
    if re.search(r'(koliko\s+fajlova|koji\s+fajlovi|šta\s+ima\s+u\s+projektu|sta\s+ima\s+u|projekt)', lower):
        files = project_files()
        if not files:
            return "Folder nije izabran ili je prazan."
        ext = {}
        for f in files:
            e = os.path.splitext(f)[1] or "ostalo"
            ext[e] = ext.get(e, 0) + 1
        return (
            f"📁 **{os.path.basename(BASE_DIR)}**\n"
            f"Fajlova: {len(files)}\n"
            + "\n".join(f"  {v}× `{k}`" for k, v in sorted(ext.items()))
        )

    # Zahvala
    if re.search(r'\b(hvala|thanks|super|odlično|odlicno|bravo|savrseno|savršeno|perfektno)\b', lower):
        return random.choice(["Nema na čemu! 🟢", "Uvijek! Daj još nešto.", "To je to! 💪"])

    # Šale
    if re.search(r'(vic|šalu|šala|nasmiji|smijesno|smiješno|funny|joke)', lower):
        vici = [
            "Zašto Python programer nosi naočale?\nJer ne može vidjet **sharp**! 🐍",
            "Koliko programera treba da promijeni žarulju?\nNijedan — to je **hardware** problem.",
            "Rekursija:\n> Vidi 'Rekursija'.",
            "Bug nije greška — to je **undocumented feature**. 😄",
            "`99 little bugs in the code,\n99 little bugs...\nTake one down, patch it around —\n127 little bugs in the code.`",
        ]
        return random.choice(vici)

    # Pitanje s upitnikom
    if "?" in raw:
        with mem_lock: hist = list(memory)
        ctx = f"\n(Zadnja tema: *{hist[-2]['content'][:50]}*)" if len(hist) >= 2 else ""
        return (
            f"Nisam siguran šta tačno pitaš.{ctx}\n\n"
            "Probaj konkretno — npr. `list`, `pročitaj main.py`, `napravi test.txt`.\n"
            "Ili napiši `help` za sve što mogu."
        )

    # Fallback
    with mem_lock: hist = list(memory)
    ctx = ""
    if len(hist) >= 2:
        ctx = f"\n*(Kontekst: {hist[-2]['content'][:60]})*"
    return (
        f"Nisam skužio šta hoćeš s: *\"{raw[:60]}\"*{ctx}\n\n"
        "Ako hoćeš raditi sa fajlovima — reci mi ime fajla i šta da radim s njim.\n"
        "Napiši `help` za sve komande."
    )

# ─────────────────────────────────────────────────────────────────────────────
# Main handler
# ─────────────────────────────────────────────────────────────────────────────

def handle(message):
    intent   = parse_intent(message)
    response = ai_respond(intent)
    log(f"[AI] intent={intent['action']} path={intent['path']} → {response[:60]}")
    return response

# ─────────────────────────────────────────────────────────────────────────────
# Flask API
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/copilot_api", methods=["POST"])
def copilot_api():
    data = request.json or {}
    msg  = data.get("message", "").strip()
    if not msg:
        return jsonify({"error": "Nedostaje 'message'."}), 400
    try:
        remember("user", msg)
        resp = handle(msg)
        remember("ai_warking", resp)
        return jsonify({"status": "ok", "response": resp})
    except Exception as e:
        tb = traceback.format_exc()
        log(f"[ERROR /copilot_api]\n{tb}")
        return jsonify({"status": "error", "response": f"Greška: {e}"}), 200

@app.route("/list",   methods=["GET"])
def api_list():
    return jsonify(project_files())

@app.route("/read",   methods=["POST"])
def api_read():
    d = request.json or {}
    return jsonify({"content": do_read(d["path"])})

@app.route("/write",  methods=["POST"])
def api_write():
    d = request.json or {}
    do_write(d["path"], d.get("content", ""))
    return jsonify({"status": "ok"})

@app.route("/run",    methods=["POST"])
def api_run():
    d = request.json or {}
    o, e, rc = do_run(d["path"])
    return jsonify({"stdout": o, "stderr": e, "returncode": rc})

@app.route("/status", methods=["GET"])
def api_status():
    with st_lock: st = state.copy()
    with mem_lock: mc = len(memory)
    return jsonify({**st, "base_dir": BASE_DIR, "session_id": session["id"],
                    "session_messages": len(session["messages"]), "memory_count": mc})

@app.route("/control/pause",  methods=["POST"])
def ctrl_pause():
    with st_lock: state.update({"status": "paused", "paused": True})
    return jsonify({"status": "paused"})

@app.route("/control/resume", methods=["POST"])
def ctrl_resume():
    with st_lock: state.update({"status": "running", "paused": False, "error": None})
    return jsonify({"status": "running"})

@app.route("/control/stop",   methods=["POST"])
def ctrl_stop():
    threading.Thread(target=lambda: (time.sleep(0.4), os._exit(0)), daemon=True).start()
    return jsonify({"status": "stopped"})

@app.route("/control/reset",  methods=["POST"])
def ctrl_reset():
    global session
    session = {"id": str(uuid.uuid4()), "started": datetime.datetime.now().isoformat(), "messages": []}
    with mem_lock: memory.clear()
    with st_lock: state.update({"status": "running", "paused": False, "error": None})
    return jsonify({"status": "reset", "new_session": session["id"]})

@app.route("/sessions", methods=["GET"])
def api_sessions():
    out = []
    for fn in sorted(os.listdir(SESSIONS)):
        if fn.endswith(".json"):
            try:
                s = json.load(open(os.path.join(SESSIONS, fn), encoding="utf-8"))
                out.append({"file": fn, "id": s.get("id","")[:8],
                             "started": s.get("started","")[:19],
                             "messages": len(s.get("messages",[]))})
            except: pass
    return jsonify(out)

@app.route("/memory", methods=["GET"])
def api_memory():
    with mem_lock: return jsonify({"count": len(memory), "memory": memory})

@app.route("/memory/clear", methods=["POST"])
def api_memory_clear():
    with mem_lock: memory.clear()
    return jsonify({"status": "ok"})

# ─────────────────────────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────────────────────────

DARK = {
    "bg":"#0f0f0f","bg2":"#1a1a1a","bg3":"#242424","bg4":"#2e2e2e",
    "fg":"#e0e0e0","fg2":"#4fc3f7","fg3":"#81c784","fg4":"#ffb74d","fg5":"#ef5350",
    "accent":"#007acc","accent2":"#005f9e",
    "chat_user":"#1a2a3a","chat_ai":"#1a2d1a","chat_tool":"#2a2a1a",
    "border":"#333",
}
LIGHT = {
    "bg":"#f0f0f0","bg2":"#ffffff","bg3":"#e0e0e0","bg4":"#d0d0d0",
    "fg":"#1a1a1a","fg2":"#0066aa","fg3":"#2e7d32","fg4":"#e65100","fg5":"#c62828",
    "accent":"#007acc","accent2":"#005f9e",
    "chat_user":"#e3f2fd","chat_ai":"#e8f5e9","chat_tool":"#fff9c4",
    "border":"#ccc",
}

T = DARK   # aktivna tema

def start_gui():
    global _log_cb, _chat_cb, BASE_DIR, T

    root = tk.Tk()
    root.title("AI WarKing")
    root.geometry("1500x860")
    root.configure(bg=T["bg"])
    root.minsize(900, 600)

    style = ttk.Style(root)
    style.theme_use("clam")

    def apply_ttk(t):
        style.configure(".", background=t["bg"], foreground=t["fg"],
                         fieldbackground=t["bg2"], troughcolor=t["bg3"],
                         bordercolor=t["border"], selectbackground=t["accent"],
                         selectforeground="#fff", font=("Segoe UI", 10))
        style.configure("Treeview", background=t["bg2"], foreground=t["fg"],
                         fieldbackground=t["bg2"], rowheight=24)
        style.configure("Treeview.Heading", background=t["bg3"],
                         foreground=t["fg2"], relief="flat",
                         font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", t["accent"])])
        style.configure("TNotebook", background=t["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=t["bg3"],
                         foreground=t["fg"], padding=[14, 5])
        style.map("TNotebook.Tab",
                   background=[("selected", t["bg"]), ("active", t["bg2"])],
                   foreground=[("selected", t["fg2"])])

    apply_ttk(T)

    # ── Widget registry za retheming ──────────────────────────────────────────
    W = {"bg": [], "bg2": [], "bg3": [], "bg4": [],
         "fg": [], "fg2": [], "editor": [], "entry": []}

    def reg(widget, *groups):
        for g in groups:
            W[g].append(widget)

    def apply_theme(t):
        root.configure(bg=t["bg"])
        for w in W["bg"]:
            try: w.configure(bg=t["bg"])
            except: pass
        for w in W["bg2"]:
            try: w.configure(bg=t["bg2"])
            except: pass
        for w in W["bg3"]:
            try: w.configure(bg=t["bg3"])
            except: pass
        for w in W["bg4"]:
            try: w.configure(bg=t["bg4"])
            except: pass
        for w in W["fg"]:
            try: w.configure(fg=t["fg"])
            except: pass
        for w in W["fg2"]:
            try: w.configure(fg=t["fg2"])
            except: pass
        for w in W["editor"]:
            try: w.configure(bg=t["bg2"], fg=t["fg"], insertbackground=t["fg2"])
            except: pass
        for w in W["entry"]:
            try: w.configure(bg=t["bg3"], fg=t["fg"], insertbackground=t["fg2"])
            except: pass
        apply_ttk(t)
        chat_disp.tag_config("user_bubble",   background=t["chat_user"],  foreground=t["fg"])
        chat_disp.tag_config("ai_bubble",     background=t["chat_ai"],    foreground=t["fg3"])
        chat_disp.tag_config("tool_bubble",   background=t["chat_tool"],  foreground=t["fg4"])
        chat_disp.tag_config("name_user",     foreground=t["fg2"],  font=("Segoe UI", 9, "bold"))
        chat_disp.tag_config("name_ai",       foreground=t["fg3"],  font=("Segoe UI", 9, "bold"))
        chat_disp.tag_config("name_tool",     foreground=t["fg4"],  font=("Segoe UI", 9, "bold"))
        status_bar.configure(bg=t["accent"])
        status_lbl.configure(bg=t["accent"])
        api_lbl.configure(bg=t["accent"])

    current_theme = {"dark": True}
    def toggle_theme():
        global T
        current_theme["dark"] = not current_theme["dark"]
        T = DARK if current_theme["dark"] else LIGHT
        apply_theme(T)
        theme_btn.configure(text="☀ Light" if current_theme["dark"] else "🌙 Dark")

    # ── Helpers ───────────────────────────────────────────────────────────────
    current_file = tk.StringVar()

    def mk_btn(parent, text, cmd, bg=None, fg=None, **kw):
        bg = bg or T["bg4"]
        fg = fg or T["fg"]
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                      activebackground=T["accent2"], activeforeground="#fff",
                      relief="flat", bd=0, padx=10, pady=5,
                      cursor="hand2", font=("Segoe UI", 9), **kw)
        b.bind("<Enter>", lambda e: b.configure(bg=T["accent"]))
        b.bind("<Leave>", lambda e: b.configure(bg=bg))
        return b

    def set_status(txt, color=None):
        status_lbl.configure(text=txt)

    # ── Top bar ───────────────────────────────────────────────────────────────
    topbar = tk.Frame(root, bg=T["bg3"], height=50)
    topbar.pack(fill="x")
    topbar.pack_propagate(False)
    reg(topbar, "bg3")

    tk.Label(topbar, text="⚡", bg=T["bg3"], fg=T["fg4"],
             font=("Segoe UI", 18)).pack(side="left", padx=(12, 2))
    title_lbl = tk.Label(topbar, text="AI WarKing",
                          bg=T["bg3"], fg=T["fg2"],
                          font=("Segoe UI", 14, "bold"))
    title_lbl.pack(side="left", padx=2)
    reg(topbar, "bg3"); reg(title_lbl, "bg3", "fg2")

    sub_lbl = tk.Label(topbar, text="Copilot Bridge",
                        bg=T["bg3"], fg=T["fg"],
                        font=("Segoe UI", 9))
    sub_lbl.pack(side="left", padx=4)
    reg(sub_lbl, "bg3", "fg")

    srv_dot = tk.Label(topbar, text="● 127.0.0.1:5000",
                        bg=T["bg3"], fg=T["fg3"], font=("Segoe UI", 9))
    srv_dot.pack(side="left", padx=16)
    reg(srv_dot, "bg3")

    sess_lbl = tk.Label(topbar, text=f"#{session['id'][:6]}",
                         bg=T["bg3"], fg=T["fg4"],
                         font=("Consolas", 9))
    sess_lbl.pack(side="right", padx=16)
    reg(sess_lbl, "bg3")

    folder_lbl = tk.Label(topbar, text="📁 nije izabran",
                           bg=T["bg3"], fg=T["fg"],
                           font=("Segoe UI", 9))
    folder_lbl.pack(side="right", padx=8)
    reg(folder_lbl, "bg3", "fg")

    # ── Toolbar ───────────────────────────────────────────────────────────────
    toolbar = tk.Frame(root, bg=T["bg2"], height=40)
    toolbar.pack(fill="x")
    toolbar.pack_propagate(False)
    reg(toolbar, "bg2")
    tk.Frame(toolbar, bg=T["accent"], width=3).pack(side="left", fill="y")

    def choose_folder():
        global BASE_DIR
        d = filedialog.askdirectory(title="Izaberi projekt folder")
        if d:
            BASE_DIR = d
            folder_lbl.configure(text=f"📁 {os.path.basename(d)}")
            refresh_tree()
            set_status(f"Folder: {d}")
            log(f"[FOLDER] {d}")

    def refresh_tree():
        tree.delete(*tree.get_children())
        if not BASE_DIR: return
        def ins(parent, path):
            try: entries = sorted(os.listdir(path))
            except: return
            for name in entries:
                if name.startswith('.') or name == '__pycache__': continue
                full = os.path.join(path, name)
                rel  = os.path.relpath(full, BASE_DIR).replace("\\","/")
                if os.path.isdir(full):
                    node = tree.insert(parent,"end",text=f"📁 {name}",values=(rel,"dir"),open=False)
                    ins(node, full)
                else:
                    ico = "🐍" if name.endswith(".py") else ("📋" if name.endswith((".json",".txt",".md")) else "📄")
                    tree.insert(parent,"end",text=f"{ico} {name}",values=(rel,"file"))
        ins("", BASE_DIR)

    def on_select(event):
        sel = tree.focus()
        if not sel: return
        vals = tree.item(sel)["values"]
        if not vals or vals[1] != "file": return
        rel = vals[0]
        try:
            content = do_read(rel)
            editor.delete("1.0", tk.END)
            editor.insert(tk.END, content)
            current_file.set(rel)
            tab_lbl.configure(text=f"  {os.path.basename(rel)}  ")
            set_status(f"Otvoren: {rel}")
        except Exception as e:
            set_status(f"Greška: {e}")

    def save_file():
        rel = current_file.get()
        if not rel: return
        do_write(rel, editor.get("1.0", tk.END))
        set_status(f"Spremljeno: {rel}")

    def new_file():
        if not BASE_DIR: messagebox.showerror("Greška","Izaberi folder."); return
        name = simpledialog.askstring("Novi fajl", "Ime fajla:")
        if name:
            do_write(name, "")
            refresh_tree()
            set_status(f"Kreiran: {name}")

    def delete_file():
        rel = current_file.get()
        if not rel: return
        if messagebox.askyesno("Obriši", f"Obrisati {rel}?"):
            do_delete(rel)
            editor.delete("1.0",tk.END)
            current_file.set("")
            tab_lbl.configure(text="  —  ")
            refresh_tree()
            set_status(f"Obrisan: {rel}")

    def run_file():
        rel = current_file.get()
        if not rel or not rel.endswith(".py"):
            messagebox.showerror("Greška","Otvori .py fajl."); return
        try:
            out, err, rc = do_run(rel)
        except Exception as e:
            out, err, rc = "", str(e), -1
        log_box.configure(state="normal")
        log_box.delete("1.0",tk.END)
        log_box.insert(tk.END, out + (("\n[STDERR]\n"+err) if err else ""))
        log_box.configure(state="disabled")
        set_status(f"rc={rc}  {rel}")

    def open_client():
        subprocess.Popen(["cmd","/k","python copilot_client.py"], cwd=ROOT_DIR)

    for text, cmd, kw in [
        ("📂 Folder",   choose_folder, {"bg":T["accent"],"fg":"#fff"}),
        ("＋ Novi",      new_file,      {}),
        ("💾 Spremi",   save_file,     {}),
        ("🗑 Obriši",  delete_file,   {}),
        ("▶ Run .py",  run_file,      {"bg":"#1b3a24","fg":T["fg3"]}),
        ("🔄 Osvježi", refresh_tree,  {}),
        ("📋 Sesije",  lambda: None,  {}),
        ("🤖 Klijent", open_client,   {"bg":"#1a3a1a","fg":T["fg3"]}),
    ]:
        b = mk_btn(toolbar, text, cmd, **kw)
        b.pack(side="left", padx=2, pady=4)

    ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=6, pady=6)
    theme_btn = mk_btn(toolbar, "☀ Light", toggle_theme)
    theme_btn.pack(side="left", padx=2, pady=4)

    root.bind("<Control-s>", lambda e: save_file())

    # ── 3-panel layout ────────────────────────────────────────────────────────
    pane = tk.PanedWindow(root, orient="horizontal", bg=T["bg"],
                           sashwidth=5, sashrelief="flat", sashpad=2)
    pane.pack(fill="both", expand=True)
    reg(pane, "bg")

    # LEFT — explorer
    left = tk.Frame(pane, bg=T["bg2"], width=220)
    pane.add(left, minsize=150)
    reg(left, "bg2")

    exp_hdr = tk.Frame(left, bg=T["bg3"], height=28)
    exp_hdr.pack(fill="x")
    exp_hdr.pack_propagate(False)
    reg(exp_hdr, "bg3")
    tk.Label(exp_hdr, text="  EXPLORER", bg=T["bg3"], fg=T["fg2"],
             font=("Segoe UI",8,"bold")).pack(side="left", pady=6)

    tf = tk.Frame(left, bg=T["bg2"]); tf.pack(fill="both",expand=True)
    reg(tf, "bg2")
    tsc = ttk.Scrollbar(tf); tsc.pack(side="right", fill="y")
    tree = ttk.Treeview(tf, yscrollcommand=tsc.set, show="tree", selectmode="browse")
    tree.pack(fill="both",expand=True)
    tsc.config(command=tree.yview)
    tree.bind("<<TreeviewSelect>>", on_select)

    # CENTER — editor
    center = tk.Frame(pane, bg=T["bg"])
    pane.add(center, minsize=420)
    reg(center, "bg")

    tab_bar = tk.Frame(center, bg=T["bg3"], height=32)
    tab_bar.pack(fill="x")
    tab_bar.pack_propagate(False)
    reg(tab_bar, "bg3")
    tab_lbl = tk.Label(tab_bar, text="  —  ", bg=T["bg2"], fg=T["fg"],
                        font=("Segoe UI",9), padx=12, pady=6)
    tab_lbl.pack(side="left")
    reg(tab_lbl, "bg2","fg")

    editor = scrolledtext.ScrolledText(
        center, wrap="none", font=("Cascadia Code",11),
        bg=T["bg2"], fg=T["fg"], insertbackground=T["fg2"],
        selectbackground=T["accent"], relief="flat", bd=0,
        padx=8, pady=8)
    editor.pack(fill="both", expand=True)
    reg(editor, "editor")

    # RIGHT — chat + log + sesije
    right = tk.Frame(pane, bg=T["bg2"], width=380)
    pane.add(right, minsize=250)
    reg(right, "bg2")

    nb = ttk.Notebook(right)
    nb.pack(fill="both", expand=True)

    # ── Chat tab ──────────────────────────────────────────────────────────────
    chat_tab = tk.Frame(nb, bg=T["bg2"]); nb.add(chat_tab, text="  💬 Chat  ")
    reg(chat_tab, "bg2")

    chat_disp = scrolledtext.ScrolledText(
        chat_tab, wrap="word", font=("Segoe UI",10),
        bg=T["bg2"], fg=T["fg"], insertbackground=T["fg2"],
        relief="flat", bd=0, padx=6, pady=6, state="disabled", spacing3=4)
    chat_disp.pack(fill="both", expand=True)
    reg(chat_disp, "bg2","fg")

    # Chat tags
    chat_disp.tag_config("name_user",   foreground=T["fg2"], font=("Segoe UI",9,"bold"))
    chat_disp.tag_config("name_ai",     foreground=T["fg3"], font=("Segoe UI",9,"bold"))
    chat_disp.tag_config("name_tool",   foreground=T["fg4"], font=("Segoe UI",9,"bold"))
    chat_disp.tag_config("user_bubble", background=T["chat_user"],  foreground=T["fg"])
    chat_disp.tag_config("ai_bubble",   background=T["chat_ai"],    foreground=T["fg"])
    chat_disp.tag_config("tool_bubble", background=T["chat_tool"],  foreground=T["fg"])
    chat_disp.tag_config("ts",          foreground="#555", font=("Consolas",8))
    chat_disp.tag_config("code",        font=("Cascadia Code",9), foreground=T["fg4"])
    chat_disp.tag_config("bold",        font=("Segoe UI",10,"bold"))

    # Input area
    inp_frame = tk.Frame(chat_tab, bg=T["bg3"])
    inp_frame.pack(fill="x", padx=6, pady=6)
    reg(inp_frame, "bg3")

    chat_entry = tk.Text(inp_frame, height=3, wrap="word",
                          font=("Segoe UI",10),
                          bg=T["bg4"], fg=T["fg"],
                          insertbackground=T["fg2"],
                          relief="flat", bd=6, padx=4)
    chat_entry.pack(side="left", fill="both", expand=True)
    reg(chat_entry, "entry")

    # Placeholder
    PLACEHOLDER = "Piši slobodno — napravi, pročitaj, pokreni..."
    chat_entry.insert("1.0", PLACEHOLDER)
    chat_entry.configure(fg="#555")
    def on_focus_in(e):
        if chat_entry.get("1.0","end-1c") == PLACEHOLDER:
            chat_entry.delete("1.0",tk.END)
            chat_entry.configure(fg=T["fg"])
    def on_focus_out(e):
        if not chat_entry.get("1.0","end-1c").strip():
            chat_entry.insert("1.0", PLACEHOLDER)
            chat_entry.configure(fg="#555")
    chat_entry.bind("<FocusIn>",  on_focus_in)
    chat_entry.bind("<FocusOut>", on_focus_out)

    send_btn = mk_btn(inp_frame, "↑", None, bg=T["accent"], fg="#fff")
    send_btn.configure(font=("Segoe UI",14,"bold"), width=2)
    send_btn.pack(side="right", padx=(4,0), pady=2, fill="y")

    def _render_chat(role, text):
        """Prikaži poruku u chat displayju sa bubble stilom."""
        chat_disp.configure(state="normal")
        ts = datetime.datetime.now().strftime("%H:%M")

        if role == "user":
            name_tag, bubble_tag = "name_user", "user_bubble"
            label = "Ti"
        elif role in ("ai_warking", "assistant", "copilot"):
            name_tag, bubble_tag = "name_ai", "ai_bubble"
            label = "AI WarKing"
        else:
            name_tag, bubble_tag = "name_tool", "tool_bubble"
            label = "Tool"

        chat_disp.insert(tk.END, f" {label}  ", name_tag)
        chat_disp.insert(tk.END, f"{ts}\n", "ts")

        # Renderuj **bold** i `code` inline
        lines = text.split("\n")
        for line in lines:
            parts = re.split(r'(`[^`]+`|\*\*[^*]+\*\*)', line)
            chat_disp.insert(tk.END, "  ", bubble_tag)
            for part in parts:
                if part.startswith("`") and part.endswith("`"):
                    chat_disp.insert(tk.END, part[1:-1], ("bubble_tag", "code"))
                elif part.startswith("**") and part.endswith("**"):
                    chat_disp.insert(tk.END, part[2:-2], ("bubble_tag", "bold"))
                else:
                    chat_disp.insert(tk.END, part, bubble_tag)
            chat_disp.insert(tk.END, "\n", bubble_tag)

        chat_disp.insert(tk.END, "\n")
        chat_disp.see(tk.END)
        chat_disp.configure(state="disabled")

    def send_chat(event=None):
        msg = chat_entry.get("1.0","end-1c").strip()
        if not msg or msg == PLACEHOLDER: return "break"
        chat_entry.delete("1.0", tk.END)
        root.after(0, lambda: _render_chat("user", msg))

        def _call():
            try:
                import urllib.request as ur
                body = json.dumps({"message": msg}).encode()
                req  = ur.Request("http://127.0.0.1:5000/copilot_api",
                                   data=body,
                                   headers={"Content-Type":"application/json"},
                                   method="POST")
                with ur.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read().decode())
                resp = data.get("response","")
            except Exception as e:
                resp = f"❌ Greška: {e}"
            root.after(0, lambda r=resp: _render_chat("ai_warking", r))
            # Osvježi tree ako je write/mkdir
            root.after(500, refresh_tree)

        threading.Thread(target=_call, daemon=True).start()
        return "break"

    chat_entry.bind("<Return>",       send_chat)
    chat_entry.bind("<Shift-Return>", lambda e: None)
    send_btn.configure(command=send_chat)

    # ── Log tab ───────────────────────────────────────────────────────────────
    log_tab = tk.Frame(nb, bg=T["bg2"]); nb.add(log_tab, text="  📋 Log  ")
    reg(log_tab, "bg2")
    log_box = scrolledtext.ScrolledText(
        log_tab, wrap="word", font=("Cascadia Code",8),
        bg=T["bg2"], fg="#607d8b",
        relief="flat", bd=0, state="disabled", padx=6, pady=6)
    log_box.pack(fill="both", expand=True)

    # ── Sesije tab ────────────────────────────────────────────────────────────
    sess_tab = tk.Frame(nb, bg=T["bg2"]); nb.add(sess_tab, text="  🗂 Sesije  ")
    reg(sess_tab, "bg2")
    sess_info = tk.Label(sess_tab, text="", bg=T["bg2"], fg=T["fg"],
                          font=("Segoe UI",10), justify="left", anchor="nw")
    sess_info.pack(fill="both", expand=True, padx=12, pady=12)
    reg(sess_info, "bg2","fg")

    def refresh_sess():
        cnt = len([f for f in os.listdir(SESSIONS) if f.endswith(".json")])
        with mem_lock: mc = len(memory)
        sess_info.configure(text=(
            f"Sesija ID:    {session['id'][:8]}\n"
            f"Poruke:       {len(session['messages'])}\n"
            f"Memorija:     {mc}/{MEM_MAX}\n"
            f"Sesija (uk.): {cnt}\n\n"
            f"Folder:\n{SESSIONS}"
        ))
        sess_lbl.configure(text=f"#{session['id'][:6]}")
        root.after(3000, refresh_sess)

    refresh_sess()

    # ── Status bar ────────────────────────────────────────────────────────────
    status_bar = tk.Frame(root, bg=T["accent"], height=24)
    status_bar.pack(fill="x", side="bottom")
    status_bar.pack_propagate(False)
    status_lbl = tk.Label(status_bar, text="Spreman. Izaberi folder i počni.",
                           bg=T["accent"], fg="#fff", font=("Segoe UI",9), anchor="w")
    status_lbl.pack(side="left", padx=10)
    api_lbl = tk.Label(status_bar, text="/copilot_api  ●  127.0.0.1:5000",
                        bg=T["accent"], fg="#b3e0ff", font=("Segoe UI",9))
    api_lbl.pack(side="right", padx=10)

    # ── GUI callbacks ─────────────────────────────────────────────────────────
    def gui_log(txt):
        def _do():
            log_box.configure(state="normal")
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            log_box.insert(tk.END, f"[{ts}] {txt}\n")
            log_box.see(tk.END)
            log_box.configure(state="disabled")
        root.after(0, _do)

    def gui_chat(role, content):
        if role in ("ai_warking", "assistant"):
            root.after(0, lambda: _render_chat(role, content))

    _log_cb  = gui_log
    _chat_cb = gui_chat

    # Welcome poruka u chatu
    def _welcome():
        _render_chat("ai_warking",
            "**Zdravo! Ja sam AI WarKing.** 🟢\n\n"
            "Izaberi projekt folder (📂 Folder), pa mi slobodno reci šta radiš.\n\n"
            "Primjeri:\n"
            "• `napravi test.txt i unutra napiši Hello World`\n"
            "• `pročitaj main.py`\n"
            "• `pokreni main.py`\n"
            "• `list`\n\n"
            "Pišeš normalno — bez prefiksa, bez pravila."
        )
    root.after(800, _welcome)

    set_status("AI WarKing spreman  ●  127.0.0.1:5000")
    log("[AI WarKing] Pokrenut. Sesija: " + session["id"][:8])

    root.mainloop()

# ─────────────────────────────────────────────────────────────────────────────
# Start
# ─────────────────────────────────────────────────────────────────────────────

def start_flask():
    log("[SERVER] http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=start_flask, daemon=True).start()
    start_gui()

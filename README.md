# AI-WarKing
Local AI Bridge for Copilot – Python automation, GUI, NLU, offline assistant.
🔥 1. Natural Language → Automatic AI Actions (NLU Parser)
AI WarKing understands commands like a human:

“create test.txt and write hello” → creates file + writes text

“read main.py” → reads file

“run main.py” → executes Python script

“fix main.py” → auto‑fixes code

“delete old.py” → deletes file

“what’s in the folder” → lists files

No prefixes, no special commands, no @@TOOL.

⚡ 2. Local Server (server.py)
The central brain that:

receives messages

analyzes intent

executes actions

manages sessions

stores context

returns formatted responses

Runs on 127.0.0.1:5000.

🖥️ 3. GUI Application (Main Chat Interface)
A modern chat UI featuring:

chat bubbles (user/AI)

bold text, code blocks, syntax highlighting

auto‑refreshing file tree

dark/light theme

placeholder text

welcome message on startup

Feels like a real AI development tool.

🟦 4. Overlay Control Panel (overlay.py)
A floating mini‑panel that stays on top:

shows server status

shows message count

shows session ID

shows memory usage

pause / resume

stop server

reset server

restart all (start_all.bat)

draggable, transparent, always on top

Perfect for multitasking.

🧠 5. _chat_brain() — AI Assistant Personality
AI WarKing includes a built‑in personality:

developer tone

jokes

greetings

status messages

explanations

coding help

Not a “dry” AI — it has character.

🛠️ 6. Tool Engine (write, read, run, fix, delete, list)
Everything works automatically:

file creation

file editing

running Python scripts

automatic error fixing

deleting files

listing directories

All through natural language.

🔄 7. Auto‑Fix System
If a script has an error:

AI WarKing reads it

analyzes it

generates a fix

writes the corrected version

runs it again

Perfect for debugging.

🧩 8. Copilot Bridge (copilot_client.py)
A CLI tool that allows:

sending messages

reading files

running scripts

auto‑fixing

viewing sessions

viewing memory buffer

Works as a terminal version of AI WarKing.

📁 9. start_all.bat — Full Startup Script
With one click, it launches:

the server

the GUI

the overlay panel

Ideal for Windows users.

🔒 10. Fully Local (No Internet Required)
AI WarKing:

sends no data outside

uses no cloud services

requires no API keys

requires no login

Completely private.

🟩 In Short:
AI WarKing is a local AI operating system for developers.

Chat

Tools

GUI

Overlay

NLU

Auto‑fix

File manager

Terminal bridge

All in one.

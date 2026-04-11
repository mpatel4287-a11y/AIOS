#!/usr/bin/env python3
import os, sys, time, hashlib, base64, requests, json
import threading, subprocess, ctypes
import psutil
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.syntax import Syntax
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from prompt_toolkit.history import FileHistory

console = Console()

OLLAMA_URL   = "http://localhost:11434/api/generate"
LLM_MODEL    = "llama3.2:3b"
VISION_MODEL = "llava:7b"
DB_PATH      = os.path.expanduser("~/.aios/fileindex")
os.makedirs(DB_PATH, exist_ok=True)

# ─────────────────────────────────────────────
# SUPPRESS ALSA NOISE
# ─────────────────────────────────────────────
def suppress_alsa():
    try:
        asound = ctypes.cdll.LoadLibrary("libasound.so.2")
        asound.snd_lib_error_set_handler(
            ctypes.CFUNCTYPE(
                None, ctypes.c_char_p, ctypes.c_int,
                ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p
            )(lambda *a: None)
        )
    except:
        pass

suppress_alsa()

# ─────────────────────────────────────────────
# CORE: ASK OLLAMA (no streaming — reliable)
# ─────────────────────────────────────────────
def ask(prompt, model=None, timeout=60):
    """Send prompt to Ollama, return response string."""
    try:
        r = requests.post(OLLAMA_URL, json={
            "model":  model or LLM_MODEL,
            "prompt": prompt,
            "stream": False
        }, timeout=timeout)
        data = r.json()
        if "error" in data:
            return f"Ollama error: {data['error']}"
        return data.get("response", "").strip()
    except requests.exceptions.ConnectionError:
        return "Cannot connect to Ollama. Run: sudo systemctl start ollama"
    except requests.exceptions.Timeout:
        return "Ollama timed out — model may be too slow on CPU."
    except Exception as e:
        return f"Error: {e}"

def get_ctx():
    """Get live system stats as a short string."""
    try:
        cpu  = psutil.cpu_percent(interval=0.3)
        ram  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        procs = sorted(
            psutil.process_iter(["name", "cpu_percent"]),
            key=lambda p: p.info["cpu_percent"] or 0, reverse=True
        )[:4]
        top = ", ".join(
            p.info["name"] for p in procs
            if p.info.get("name")
        )
        return (f"CPU {cpu:.1f}% | RAM {ram.percent:.1f}% "
                f"({ram.available//1024//1024}MB free) | "
                f"Disk {disk.percent:.1f}% | Top procs: {top}")
    except:
        return "system stats unavailable"

# ─────────────────────────────────────────────
# MODULE 1 — AI SYSTEM ASSISTANT
# ─────────────────────────────────────────────
def run_aiosys():
    console.print(Panel.fit(
        "[bold green]aiosys[/bold green] — AI system assistant\n"
        "[dim]Ask anything. Type 'back' to return to menu.[/dim]",
        border_style="green"
    ))

    session = PromptSession(
        history=FileHistory(os.path.expanduser("~/.aios/sys_history"))
    )
    history_text = ""

    SYSTEM = (
        "You are aiosys, an AI Linux assistant on Ubuntu 24.04. "
        "You have real system data. Be concise — 2-3 sentences max. "
        "If you need to run a command, output ONLY this on its own line: "
        "CMD: <the exact bash command>"
    )

    while True:
        try:
            user = session.prompt("\n  you → ").strip()
            if not user:
                continue
            if user.lower() in ("back", "exit", "quit"):
                break

            ctx    = get_ctx()
            prompt = (f"{SYSTEM}\n\n"
                      f"Current system: {ctx}\n\n"
                      f"{history_text}"
                      f"User: {user}\n"
                      f"Assistant:")

            console.print("[dim]thinking...[/dim]", end="\r")
            response = ask(prompt)

            if not response:
                console.print("[red]No response from Ollama.[/red]")
                continue

            # Check for command
            lines = response.splitlines()
            cmd_line = None
            reply_lines = []
            for line in lines:
                if line.strip().startswith("CMD:"):
                    cmd_line = line.strip().replace("CMD:", "").strip()
                else:
                    reply_lines.append(line)

            reply = "\n".join(reply_lines).strip()
            if reply:
                console.print(f"\n[bold green]aiosys:[/bold green] {reply}")

            if cmd_line:
                console.print(f"\n[yellow]Command:[/yellow] {cmd_line}")
                confirm = input("  Run this? [y/n] → ").strip().lower()
                if confirm == "y":
                    out = subprocess.run(
                        cmd_line, shell=True,
                        capture_output=True, text=True,
                        cwd=os.path.expanduser("~")
                    )
                    result = (out.stdout or out.stderr or "").strip()
                    if result:
                        console.print(Panel(result, border_style="dim",
                                            title="output"))

            history_text += f"User: {user}\nAssistant: {response}\n\n"
            # Keep history short
            lines_h = history_text.split("\n")
            if len(lines_h) > 30:
                history_text = "\n".join(lines_h[-30:])

        except KeyboardInterrupt:
            console.print("\n[dim]Type 'back' to return.[/dim]")
        except EOFError:
            break

# ─────────────────────────────────────────────
# MODULE 2 — NATURAL LANGUAGE SHELL
# ─────────────────────────────────────────────
def run_nlshell():
    console.print(Panel.fit(
        "[bold green]NL Shell[/bold green] — English → Linux commands\n"
        "[dim]'!' prefix for raw bash  |  'back' to return to menu\n"
        "Supports: install, download, file ops, system commands[/dim]",
        border_style="green"
    ))

    session = PromptSession(
        history=FileHistory(os.path.expanduser("~/.aios/shell_history")),
        style=Style.from_dict({"prompt": "ansicyan bold"})
    )

    SYSTEM = """You are a Linux bash expert on Ubuntu 24.04.
Convert the user's English request into the correct bash command.

Important rules:
1. For installing packages use: sudo apt install -y <package>
2. For downloading files use: wget <url> or curl -LO <url>
3. For pip packages use: pip install <package>
4. For updating system use: sudo apt update && sudo apt upgrade -y
5. Always use -y flag for apt to avoid interactive prompts
6. For multiple steps combine with && 

Reply in EXACTLY this format — nothing else:
CMD: <the complete bash command>
EXPLAIN: <one line plain English>
SUDO: yes   (only add this line if command needs sudo)
CONFIRM: yes  (only add this line if command deletes or overwrites files)

If unclear reply:
ERROR: <what is unclear>"""

    def run_live(cmd, use_sudo=False):
        """Run command with live output — perfect for downloads and installs."""
        console.print(f"\n[yellow]Running:[/yellow] {cmd}\n")
        try:
            process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=os.path.expanduser("~")
            )
            # Stream output live
            for line in iter(process.stdout.readline, ""):
                print(line, end="", flush=True)
            process.wait()
            print()
            if process.returncode == 0:
                console.print("[green]  Done.[/green]")
            else:
                console.print(f"[red]  Exited with code {process.returncode}[/red]")
            return process.returncode
        except Exception as e:
            console.print(f"[red]  Error: {e}[/red]")
            return 1

    def check_sudo_available():
        """Check if user can use sudo."""
        result = subprocess.run(
            "sudo -n true", shell=True,
            capture_output=True
        )
        return result.returncode == 0

    while True:
        try:
            user = session.prompt("\n  shell → ").strip()
            if not user:
                continue
            if user.lower() in ("back", "exit", "quit"):
                break

            # Raw bash passthrough with !
            if user.startswith("!"):
                raw = user[1:].strip()
                if raw:
                    run_live(raw)
                continue

            # Quick shortcuts
            low = user.lower()
            if low in ("update", "upgrade", "update system"):
                console.print("[dim]Updating system...[/dim]")
                run_live("sudo apt update && sudo apt upgrade -y")
                continue
            if low.startswith("install ") and len(low.split()) == 2:
                pkg = low.split()[1]
                run_live(f"sudo apt install -y {pkg}")
                continue
            if low.startswith("pip install "):
                run_live(user)
                continue
            if low.startswith("download "):
                url = user.split(None, 1)[1]
                run_live(f"wget '{url}' -P ~/Downloads/")
                continue

            # ── Quick shortcuts (bypass AI entirely for common tasks) ──
            low = user.lower().strip()

            # Open applications
            open_apps = {
                "firefox": "firefox",
                "chrome": "google-chrome",
                "terminal": "gnome-terminal",
                "files": "nautilus",
                "settings": "gnome-control-center",
                "calculator": "gnome-calculator",
                "text editor": "gedit",
                "vs code": "code",
                "vscode": "code",
                "vlc": "vlc",
}
            for keyword, app_cmd in open_apps.items():
                if keyword in low and any(
                    w in low for w in ["open", "launch", "start", "run"]
                ):
                    console.print(f"[dim]Opening {keyword}...[/dim]")
                    subprocess.Popen(
            app_cmd, shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
            console.print(f"[green]  Launched {keyword}.[/green]")
            user = None
            break

            if user is None:
                continue

            if low in ("update", "upgrade", "update system",
           "update my system"):
                run_live("sudo apt update && sudo apt upgrade -y")
                continue

            # install <package> — single word after install
            if low.startswith("install ") and len(low.split()) == 2:
                pkg = low.split()[1]
                run_live(f"sudo apt install -y {pkg}")
                continue

            # uninstall / remove
            if any(low.startswith(x) for x in
                ["uninstall ", "remove ", "delete package "]):
                pkg = low.split()[-1]
                console.print(f"[dim]Removing {pkg}...[/dim]")
                run_live(f"sudo apt remove -y {pkg}")
                continue

            if low.startswith("pip install "):
                run_live(user)
                continue

            if low.startswith("download "):
                url = user.split(None, 1)[1].strip()
                run_live(f"wget -c '{url}' -P ~/Downloads/")
                continue

            # Ask AI to translate
            console.print("[dim]translating...[/dim]", end="\r")
            prompt   = f"{SYSTEM}\n\nUser request: {user}\n"
            response = ask(prompt, timeout=30)

            if not response:
                console.print("[red]No response from Ollama.[/red]")
                continue

            # Parse
            cmd            = None
            explain        = ""
            needs_confirm  = False
            needs_sudo     = False

            for line in response.splitlines():
                line = line.strip()
                if line.startswith("CMD:"):
                    cmd = line[4:].strip()
                elif line.startswith("EXPLAIN:"):
                    explain = line[8:].strip()
                elif line.upper().startswith("CONFIRM:"):
                    needs_confirm = True
                elif line.upper().startswith("SUDO:"):
                    needs_sudo = True
                elif line.startswith("ERROR:"):
                    console.print(
                        f"[yellow]Unclear:[/yellow] "
                        f"{line[6:].strip()}")
                    cmd = None

            if not cmd:
                if response and not response.startswith("ERROR"):
                    console.print(f"[dim]{response}[/dim]")
                continue

            # ── Validate command before running ──
            # Catch hallucinated apt package names
            if "apt install" in cmd or "apt-get install" in cmd:
                # Extract package name
                parts = cmd.split()
                pkg_candidates = [
                    p for p in parts
                    if not p.startswith("-")
                    and p not in ("apt", "apt-get", "install",
                                  "sudo", "-y", "&&")
                ]
                if pkg_candidates:
                    pkg = pkg_candidates[-1]
                    # Quick check if package exists
                    check = subprocess.run(
                        f"apt-cache show {pkg} 2>/dev/null | head -1",
                        shell=True, capture_output=True, text=True
                    )
                    if not check.stdout.strip():
                        console.print(
                            f"[red]Package '{pkg}' not found "
                            f"in apt.[/red]")
                        # Try to find similar
                        suggest = subprocess.run(
                            f"apt-cache search {pkg} 2>/dev/null "
                            f"| head -5",
                            shell=True, capture_output=True, text=True
                        )
                        if suggest.stdout.strip():
                            console.print(
                                "[dim]Similar packages:[/dim]")
                            console.print(suggest.stdout.strip())
                        console.print(
                            "[dim]Run '!apt-cache search <name>' "
                            "to search manually.[/dim]")
                        continue

            # Auto-detect sudo need
            if any(cmd.startswith(x) for x in
                   ["sudo ", "apt ", "apt-get ", "systemctl ",
                    "service ", "mount ", "umount "]):
                needs_sudo = True

            # Show the command
            console.print()
            console.print(Syntax(cmd, "bash", theme="monokai",
                                 background_color="default"))
            if explain:
                console.print(f"[dim]{explain}[/dim]")

            # Warnings
            if needs_sudo:
                console.print("[yellow]  Requires sudo (admin password)[/yellow]")
            if needs_confirm:
                console.print("[red bold]  ⚠ This modifies or deletes files.[/red bold]")

            # Confirm
            confirm = input("\n  Run? [y/n] → ").strip().lower()
            if confirm != "y":
                console.print("[dim]  Skipped.[/dim]")
                continue

            # Detect if command is long-running
            long_running = any(x in cmd for x in [
                "apt ", "apt-get ", "wget ", "curl ", "pip ",
                "npm ", "git clone", "make ", "cmake ",
                "ffmpeg ", "tar ", "unzip ", "cp -r", "rsync "
            ])

            if long_running:
                # Live streaming output for downloads/installs
                run_live(cmd)
            else:
                # Captured output for quick commands
                out = subprocess.run(
                    cmd, shell=True,
                    capture_output=True, text=True,
                    cwd=os.path.expanduser("~")
                )
                stdout = (out.stdout or "").strip()
                stderr = (out.stderr or "").strip()

                if stdout:
                    console.print(Panel(stdout, border_style="dim",
                                        title="output"))
                if stderr:
                    # Some commands write normal output to stderr
                    # Only show as error if command actually failed
                    if out.returncode != 0:
                        console.print(Panel(stderr, border_style="red",
                                            title="error"))
                    else:
                        console.print(Panel(stderr, border_style="dim",
                                            title="info"))
                if not stdout and not stderr:
                    console.print("[green]  Done.[/green]")

        except KeyboardInterrupt:
            console.print("\n[dim]  Ctrl+C — type 'back' to return.[/dim]")
        except EOFError:
            break
# ─────────────────────────────────────────────
# MODULE 3 — AI FILE MANAGER
# ─────────────────────────────────────────────
def run_files():
    try:
        import chromadb
        from chromadb.utils import embedding_functions
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        import fitz
    except ImportError as e:
        console.print(f"[red]Missing library: {e}[/red]")
        console.print("[dim]Run: pip install chromadb watchdog PyMuPDF[/dim]")
        input("Press Enter to return...")
        return

    SKIP_DIRS = {
        ".cache", ".local", ".config", ".mozilla", ".chrome",
        ".thunderbird", "node_modules", "__pycache__", ".git",
        ".npm", ".cargo", ".rustup", ".gradle", "venv", ".venv",
        "env", ".wine", "snap", ".snap", ".docker", ".ollama",
        ".steam", ".var", "lost+found", ".minikube"
    }
    SUPPORTED = {
        ".txt", ".md", ".py", ".json", ".csv", ".html", ".sh",
        ".js", ".ts", ".yaml", ".yml", ".toml", ".ini", ".conf",
        ".log", ".rs", ".go", ".pdf",
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"
    }
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

    # Setup ChromaDB
    chroma = chromadb.PersistentClient(path=DB_PATH)
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    col = chroma.get_or_create_collection("files2", embedding_function=ef)

    # Load already-indexed mtimes into memory (fast lookup)
    indexed_mtimes = {}
    try:
        existing = col.get(include=["metadatas"])
        for i, doc_id in enumerate(existing["ids"]):
            mt = existing["metadatas"][i].get("mtime", 0)
            indexed_mtimes[doc_id] = mt
    except:
        pass

    def read_file(path):
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".pdf":
                doc = fitz.open(path)
                return " ".join(p.get_text() for p in doc)[:3000]
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()[:3000]
        except:
            return ""

    def img_text(path):
        name   = os.path.splitext(
            os.path.basename(path))[0].replace("_", " ").replace("-", " ")
        folder = os.path.basename(os.path.dirname(path))
        ext    = os.path.splitext(path)[1].lower().replace(".", "")
        return f"image photo picture {ext} {name} in folder {folder}"

    def needs_index(path):
        """Return True only if file is new or changed."""
        try:
            doc_id = hashlib.md5(path.encode()).hexdigest()
            mtime  = os.path.getmtime(path)
            stored = indexed_mtimes.get(doc_id, -1)
            return abs(stored - mtime) > 1.0
        except:
            return True

    def index_one(path):
        try:
            ext  = os.path.splitext(path)[1].lower()
            if ext not in SUPPORTED:
                return
            size = os.path.getsize(path)
            if size < 256 or size > 10 * 1024 * 1024:
                return

            doc_id  = hashlib.md5(path.encode()).hexdigest()
            name    = os.path.basename(path)
            mtime   = os.path.getmtime(path)

            if ext in IMAGE_EXTS:
                content = img_text(path)
            elif ext == ".svg":
                try:
                    with open(path, "r", errors="ignore") as f:
                        content = f"SVG image {name}: {f.read()[:300]}"
                except:
                    content = img_text(path)
            else:
                content = read_file(path)

            if not content.strip():
                return

            col.upsert(
                ids=[doc_id],
                documents=[f"{name}\n{content}"],
                metadatas=[{
                    "path": path, "name": name,
                    "ext": ext, "size": size, "mtime": mtime
                }]
            )
            indexed_mtimes[doc_id] = mtime
        except:
            pass

    def index_all():
        console.print("[dim]Scanning home directory...[/dim]")
        all_files = []
        home = os.path.expanduser("~")
        for root, dirs, files in os.walk(home):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in SKIP_DIRS
            ]
            for fname in files:
                path = os.path.join(root, fname)
                ext  = os.path.splitext(path)[1].lower()
                if ext not in SUPPORTED:
                    continue
                try:
                    if os.path.getsize(path) < 256:
                        continue
                except:
                    continue
                all_files.append(path)

        # Only files that actually need indexing
        to_index = [p for p in all_files if needs_index(p)]
        total    = len(to_index)

        if total == 0:
            console.print(f"[green]All {len(all_files)} files already indexed. "
                          f"Nothing new to process.[/green]")
            return

        console.print(f"[dim]{len(all_files)} files found, "
                      f"{total} need indexing...[/dim]")

        BATCH   = 20
        indexed = 0
        for i in range(0, total, BATCH):
            batch = to_index[i:i + BATCH]
            ids, docs, metas = [], [], []
            for path in batch:
                try:
                    ext    = os.path.splitext(path)[1].lower()
                    name   = os.path.basename(path)
                    size   = os.path.getsize(path)
                    mtime  = os.path.getmtime(path)
                    doc_id = hashlib.md5(path.encode()).hexdigest()

                    if ext in IMAGE_EXTS:
                        content = img_text(path)
                    elif ext == ".svg":
                        try:
                            with open(path, "r", errors="ignore") as f:
                                content = f"SVG {name}: {f.read()[:300]}"
                        except:
                            content = img_text(path)
                    else:
                        content = read_file(path)

                    if not content.strip():
                        continue

                    ids.append(doc_id)
                    docs.append(f"{name}\n{content}")
                    metas.append({
                        "path": path, "name": name,
                        "ext": ext, "size": size, "mtime": mtime
                    })
                    indexed_mtimes[doc_id] = mtime
                except:
                    continue

            if ids:
                try:
                    col.upsert(ids=ids, documents=docs, metadatas=metas)
                    indexed += len(ids)
                except:
                    pass

            done = min(i + BATCH, total)
            pct  = int(done / total * 100) if total else 100
            bar  = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"  [{bar}] {pct}%  {done}/{total}  ({indexed} indexed)",
                  end="\r", flush=True)

        print()
        console.print(f"[green]Done! {indexed} new files indexed "
                      f"({col.count()} total).[/green]")

    class Watcher(FileSystemEventHandler):
        def on_created(self, e):
            if not e.is_directory:
                index_one(e.src_path)
                console.print(
                    f"\n[dim]+ {os.path.basename(e.src_path)}[/dim]")
        def on_modified(self, e):
            if not e.is_directory:
                index_one(e.src_path)
        def on_deleted(self, e):
            if not e.is_directory:
                try:
                    doc_id = hashlib.md5(e.src_path.encode()).hexdigest()
                    col.delete(ids=[doc_id])
                    indexed_mtimes.pop(doc_id, None)
                except:
                    pass

    console.print(Panel.fit(
        "[bold green]AI File Manager[/bold green] — semantic search\n"
        "[dim]Commands: reindex · count · inspect <n> · back[/dim]",
        border_style="green"
    ))

    # Index only new/changed files
    index_all()

    # Start watcher
    obs = Observer()
    obs.schedule(Watcher(), os.path.expanduser("~"), recursive=True)
    obs.daemon = True
    try:
        obs.start()
        console.print("[dim]Watching for file changes...[/dim]\n")
    except OSError:
        console.print("[yellow]Could not start file watcher "
                      "(inotify limit). Run:[/yellow]")
        console.print("[dim]echo fs.inotify.max_user_watches=524288 | "
                      "sudo tee -a /etc/sysctl.conf && sudo sysctl -p[/dim]\n")

    last_results = []

    while True:
        try:
            query = Prompt.ask("[cyan]find[/cyan]").strip()
            if not query:
                continue
            if query.lower() in ("back", "exit"):
                break
            if query.lower() == "reindex":
                indexed_mtimes.clear()
                index_all()
                continue
            if query.lower() == "count":
                console.print(f"[dim]{col.count()} files indexed[/dim]")
                continue

            if query.lower().startswith("inspect "):
                parts = query.split()
                if len(parts) > 1 and parts[1].isdigit() and last_results:
                    idx  = int(parts[1]) - 1
                    if 0 <= idx < len(last_results):
                        path = last_results[idx]["path"]
                        ext  = os.path.splitext(path)[1].lower()
                        if ext in IMAGE_EXTS:
                            console.print("[dim]Describing image...[/dim]")
                            try:
                                with open(path, "rb") as f:
                                    b64 = base64.b64encode(
                                        f.read()).decode()
                                r = requests.post(OLLAMA_URL, json={
                                    "model":  VISION_MODEL,
                                    "prompt": "Describe this image in detail.",
                                    "images": [b64],
                                    "stream": False
                                }, timeout=60)
                                desc = r.json().get("response", "").strip()
                                console.print(Panel(
                                    desc,
                                    title=os.path.basename(path),
                                    border_style="cyan"
                                ))
                            except Exception as e:
                                console.print(f"[red]{e}[/red]")
                        else:
                            console.print("[yellow]Only works on images.[/yellow]")
                continue

            # Search
            try:
                count = col.count()
                if count == 0:
                    console.print("[yellow]Index is empty — wait for indexing.[/yellow]")
                    continue
                res = col.query(
                    query_texts=[query],
                    n_results=min(8, count)
                )
            except Exception as e:
                console.print(f"[red]Search error: {e}[/red]")
                continue

            if not res["ids"][0]:
                console.print("[yellow]No matches found.[/yellow]")
                continue

            table = Table(title=f'"{query}"', border_style="dim")
            table.add_column("#",     style="dim",  width=3)
            table.add_column("File",  style="cyan", max_width=30)
            table.add_column("Type",  width=6)
            table.add_column("Size",  width=8)
            table.add_column("Match", width=7)
            table.add_column("Path",  style="dim",  max_width=36)

            last_results = res["metadatas"][0]
            for i, (m, d) in enumerate(zip(last_results,
                                           res["distances"][0])):
                sz  = m.get("size", 0)
                szs = f"{sz//1024}KB" if sz > 1024 else f"{sz}B"
                sc  = max(0, int((1 - d) * 100))
                c   = "green" if sc > 70 else "yellow" if sc > 40 else "dim"
                table.add_row(
                    str(i + 1),
                    m.get("name", "?"),
                    m.get("ext",  "?"),
                    szs,
                    f"[{c}]{sc}%[/{c}]",
                    m.get("path", "?")
                )
            console.print(table)

            choice = Prompt.ask(
                "Open a file? Enter number or Enter to skip",
                default=""
            )
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(last_results):
                    p = last_results[idx]["path"]
                    os.system(f'xdg-open "{p}" 2>/dev/null &')
                    console.print(f"[green]Opening {p}[/green]")

        except KeyboardInterrupt:
            console.print("\n[dim]Type 'back' to return.[/dim]")
        except EOFError:
            break

    try:
        obs.stop()
    except:
        pass

# ─────────────────────────────────────────────
# MODULE 4 — SYSTEM MONITOR
# ─────────────────────────────────────────────
def run_monitor():
    WARN_CPU  = 75
    WARN_RAM  = 80
    WARN_DISK = 85
    WARN_TEMP = 75

    def get_temp():
        try:
            for _, entries in psutil.sensors_temperatures().items():
                for e in entries:
                    if e.current > 0:
                        return e.current
        except:
            pass
        return None

    def bar(pct, w=20):
        f = int(pct / 100 * w)
        return "█" * f + "░" * (w - f)

    def get_snap():
        cpu   = psutil.cpu_percent(interval=0.3)
        ram   = psutil.virtual_memory()
        disk  = psutil.disk_usage("/")
        net   = psutil.net_io_counters()
        temp  = get_temp()
        procs = sorted(
            psutil.process_iter(
                ["pid", "name", "cpu_percent", "memory_percent"]),
            key=lambda p: p.info["cpu_percent"] or 0,
            reverse=True
        )[:8]
        return {
            "cpu": cpu, "ram": ram, "disk": disk,
            "net": net, "temp": temp, "procs": procs,
            "time": datetime.now().strftime("%H:%M:%S")
        }

    def draw(snap, alerts):
        os.system("clear")
        cpu  = snap["cpu"]
        ram  = snap["ram"]
        disk = snap["disk"]
        net  = snap["net"]
        temp = snap["temp"]

        cs = "red" if cpu > WARN_CPU else "yellow" if cpu > 50 else "green"
        rs = ("red" if ram.percent > WARN_RAM
              else "yellow" if ram.percent > 60 else "green")
        ds = ("red" if disk.percent > WARN_DISK
              else "yellow" if disk.percent > 70 else "green")

        console.print(
            f"\n  [bold green]aios monitor[/bold green]  "
            f"[dim]{snap['time']}[/dim]\n"
        )
        console.print(
            f"  [bold]CPU [/bold] [{cs}][{bar(cpu)}] {cpu:.1f}%[/{cs}]")
        console.print(
            f"  [bold]RAM [/bold] [{rs}][{bar(ram.percent)}] "
            f"{ram.percent:.1f}%[/{rs}]  "
            f"[dim]{ram.available//1024//1024}MB free[/dim]")
        console.print(
            f"  [bold]DISK[/bold] [{ds}][{bar(disk.percent)}] "
            f"{disk.percent:.1f}%[/{ds}]  "
            f"[dim]{disk.free//1024//1024//1024}GB free[/dim]")
        if temp:
            ts = ("red" if temp > WARN_TEMP
                  else "yellow" if temp > 60 else "green")
            console.print(
                f"  [bold]TEMP[/bold] [{ts}][{bar(min(temp,100))}] "
                f"{temp:.1f}°C[/{ts}]")
        console.print(
            f"  [bold]NET [/bold] [dim]↑{net.bytes_sent//1024}KB  "
            f"↓{net.bytes_recv//1024}KB[/dim]")
        console.print("")
        console.print("  [bold]Top processes[/bold]")
        for p in snap["procs"]:
            try:
                cp = p.info["cpu_percent"] or 0
                mp = p.info["memory_percent"] or 0
                nm = (p.info["name"] or "?")[:22]
                st = "red" if cp > 40 else "yellow" if cp > 15 else "dim"
                console.print(
                    f"  [{st}]{p.info['pid']:>6}  "
                    f"{nm:<22}  CPU {cp:>5.1f}%  "
                    f"RAM {mp:>4.1f}%[/{st}]")
            except:
                continue
        if alerts:
            console.print("\n  [bold red]Alerts[/bold red]")
            for a in alerts[-3:]:
                console.print(f"  [red]! {a}[/red]")

        console.print(
            "\n  [dim]Commands: a=AI diagnose  "
            "k=kill process  q=back[/dim]")

    def check_alerts(snap, prev):
        alerts = list(prev)
        checks = [
            (snap["cpu"] > WARN_CPU,
             f"CPU at {snap['cpu']:.1f}%"),
            (snap["ram"].percent > WARN_RAM,
             f"RAM at {snap['ram'].percent:.1f}%"),
            (snap["disk"].percent > WARN_DISK,
             f"Disk at {snap['disk'].percent:.1f}%"),
            (snap["temp"] and snap["temp"] > WARN_TEMP,
             f"Temp at {snap['temp']:.1f}°C"),
        ]
        for cond, msg in checks:
            if cond and msg not in alerts:
                alerts.append(msg)
        return alerts[-5:]

    snap   = get_snap()
    alerts = []
    stop   = threading.Event()

    def loop():
        nonlocal snap, alerts
        while not stop.is_set():
            snap   = get_snap()
            alerts = check_alerts(snap, alerts)
            draw(snap, alerts)
            time.sleep(2)

    t = threading.Thread(target=loop, daemon=True)
    t.start()

    while True:
        try:
            cmd = input("  → ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            stop.set()
            break

        if cmd == "q":
            stop.set()
            break

        elif cmd == "a":
            stop.set()
            time.sleep(0.3)
            ctx = (
                f"CPU:{snap['cpu']:.1f}% | "
                f"RAM:{snap['ram'].percent:.1f}% | "
                f"Disk:{snap['disk'].percent:.1f}%"
            )
            if snap["temp"]:
                ctx += f" | Temp:{snap['temp']:.1f}C"
            if alerts:
                ctx += f" | Alerts: {', '.join(alerts)}"

            top = []
            for p in snap["procs"][:3]:
                try:
                    top.append(
                        f"{p.info['name']}(CPU:{p.info['cpu_percent']:.1f}%)"
                    )
                except:
                    pass
            if top:
                ctx += f" | Top: {', '.join(top)}"

            console.print("\n[dim]Asking AI...[/dim]")
            prompt = (
                f"You are a Linux sysadmin. "
                f"Diagnose this system state in 2 sentences "
                f"and give one specific fix command.\n"
                f"System: {ctx}"
            )
            response = ask(prompt)
            console.print(Panel(response, title="AI diagnosis",
                                border_style="cyan"))
            input("\n  Press Enter to continue...")
            stop.clear()
            t = threading.Thread(target=loop, daemon=True)
            t.start()

        elif cmd == "k":
            stop.set()
            time.sleep(0.3)
            pid_s = input("  PID to kill → ").strip()
            if pid_s.isdigit():
                try:
                    p = psutil.Process(int(pid_s))
                    name = p.name()
                    if input(
                            f"  Kill '{name}' (PID {pid_s})? "
                            f"[y/n] → ").lower() == "y":
                        p.terminate()
                        console.print(
                            f"[green]  Terminated {name}[/green]")
                    else:
                        console.print("[dim]  Cancelled.[/dim]")
                except psutil.NoSuchProcess:
                    console.print("[red]  Process not found.[/red]")
                except psutil.AccessDenied:
                    console.print(
                        "[red]  Permission denied. Try sudo.[/red]")
            stop.clear()
            t = threading.Thread(target=loop, daemon=True)
            t.start()

# ─────────────────────────────────────────────
# MODULE 5 — VOICE INTERFACE
# ─────────────────────────────────────────────
def run_voice():
    # Suppress stderr during imports
    devnull  = os.open(os.devnull, os.O_WRONLY)
    old_err  = os.dup(2)
    os.dup2(devnull, 2)
    try:
        import speech_recognition as sr
        import pyttsx3
        engine = pyttsx3.init()
    finally:
        os.dup2(old_err, 2)
        os.close(devnull)
        os.close(old_err)

    engine.setProperty("rate", 160)
    engine.setProperty("volume", 0.9)

    def speak(text):
        clean = (text.replace("*", "").replace("#", "")
                 .replace("`", "").replace("_", ""))
        console.print(f"[bold green]aios:[/bold green] {clean}")
        try:
            engine.say(clean)
            engine.runAndWait()
        except:
            pass

    rec = sr.Recognizer()
    rec.energy_threshold         = 300
    rec.dynamic_energy_threshold = True
    rec.pause_threshold          = 0.8

    def listen(timeout=6):
        """Listen for speech, return text or None."""
        # Suppress ALSA noise during listen
        dn  = os.open(os.devnull, os.O_WRONLY)
        old = os.dup(2)
        os.dup2(dn, 2)
        try:
            with sr.Microphone() as src:
                os.dup2(old, 2)
                os.close(dn)
                os.close(old)
                console.print("[dim]  listening...[/dim]", end="\r")
                try:
                    rec.adjust_for_ambient_noise(src, duration=0.3)
                    audio = rec.listen(
                        src, timeout=timeout, phrase_time_limit=10)
                    text  = rec.recognize_google(audio)
                    console.print(f"[cyan]  you:[/cyan] {text}      ")
                    return text.lower().strip()
                except sr.WaitTimeoutError:
                    return None
                except sr.UnknownValueError:
                    console.print("[dim]  (could not understand)[/dim]")
                    return None
                except sr.RequestError as e:
                    console.print(f"[red]  speech error: {e}[/red]")
                    return None
        except Exception as e:
            try:
                os.dup2(old, 2)
                os.close(dn)
                os.close(old)
            except:
                pass
            console.print(f"[red]  mic error: {e}[/red]")
            return None

    def quick_reply(text):
        """Answer simple questions without hitting Ollama."""
        if any(w in text for w in ["time", "clock"]):
            return f"It is {datetime.now().strftime('%I:%M %p')}."
        if "cpu" in text and any(
                w in text for w in ["how", "what", "usage"]):
            return f"CPU is at {psutil.cpu_percent(0.3):.1f} percent."
        if any(w in text for w in ["ram", "memory"]) and any(
                w in text for w in ["how", "what", "free", "usage"]):
            r = psutil.virtual_memory()
            return (f"RAM is {r.percent:.1f} percent used, "
                    f"{r.available//1024//1024} MB free.")
        if any(w in text for w in ["disk", "storage", "space"]):
            d = psutil.disk_usage("/")
            return (f"Disk is {d.percent:.1f} percent full, "
                    f"{d.free//1024//1024//1024} gigabytes free.")
        if "date" in text or "today" in text:
            return f"Today is {datetime.now().strftime('%A, %B %d %Y')}."
        return None

    console.print(Panel.fit(
        "[bold green]aios voice[/bold green] — talk to your OS\n"
        "[dim]Mode 1: always on  |  Mode 2: wake word 'hey os'\n"
        "Say 'goodbye' to stop  |  Ctrl+C to return to menu[/dim]",
        border_style="green"
    ))

    mode = input("\n  Choose mode [1/2] → ").strip()
    speak("aios voice is ready. How can I help you?")

    history_text = ""

    while True:
        try:
            # Wake word mode
            if mode == "2":
                console.print(
                    "[dim]  say 'hey os' or 'aios'...[/dim]", end="\r")
                wake = listen(timeout=5)
                if not wake:
                    continue
                if not any(w in wake for w in
                           ["hey os", "aios", "hey aios", "a i o s"]):
                    continue
                speak("Yes?")

            text = listen(timeout=8)
            if not text:
                continue

            # Stop commands
            if any(w in text for w in
                   ["goodbye", "bye", "stop listening",
                    "quit", "exit", "go back"]):
                speak("Goodbye!")
                break

            # Quick answers (no AI needed)
            quick = quick_reply(text)
            if quick:
                speak(quick)
                continue

            # Ask Ollama
            console.print("[dim]  thinking...[/dim]", end="\r")
            ctx    = get_ctx()
            prompt = (
                f"You are aios, a Linux voice assistant. "
                f"Answer in plain spoken English only — no markdown, "
                f"no bullet points, no special characters. "
                f"Keep it to 1-2 sentences.\n"
                f"System: {ctx}\n"
                f"{history_text}"
                f"User: {text}\n"
                f"Assistant:"
            )
            response = ask(prompt, timeout=45)

            if not response or response.startswith("Error") or \
               response.startswith("Cannot"):
                speak("Sorry, I could not get a response. "
                      "Please check Ollama is running.")
            else:
                speak(response)
                history_text += f"User: {text}\nAssistant: {response}\n"
                lines = history_text.split("\n")
                if len(lines) > 20:
                    history_text = "\n".join(lines[-20:])

        except KeyboardInterrupt:
            speak("Going back to menu.")
            break

# ─────────────────────────────────────────────
# MAIN MENU
# ─────────────────────────────────────────────
BANNER = """
  [bold green] █████╗ ██╗ ██████╗ ███████╗[/bold green]
  [bold green]██╔══██╗██║██╔═══██╗██╔════╝[/bold green]
  [bold green]███████║██║██║   ██║███████╗[/bold green]
  [bold green]██╔══██║██║██║   ██║╚════██║[/bold green]
  [bold green]██║  ██║██║╚██████╔╝███████║[/bold green]
  [bold green]╚═╝  ╚═╝╚═╝ ╚═════╝ ╚══════╝[/bold green]
  [dim]  AI-native OS v0.2 — built by Meet Patel[/dim]
"""

MODULES = {
    "1": run_aiosys,  "sys":     run_aiosys,
    "2": run_nlshell, "shell":   run_nlshell,
    "3": run_files,   "files":   run_files,
    "4": run_monitor, "monitor": run_monitor,
    "5": run_voice,   "voice":   run_voice,
}

def show_menu():
    os.system("clear")
    console.print(BANNER)
    console.print("  [bold cyan]1[/bold cyan]  aiosys     — AI system assistant")
    console.print("  [bold cyan]2[/bold cyan]  nlshell    — natural language shell")
    console.print("  [bold cyan]3[/bold cyan]  ai_files   — semantic file search")
    console.print("  [bold cyan]4[/bold cyan]  ai_monitor — real time system monitor")
    console.print("  [bold cyan]5[/bold cyan]  ai_voice   — voice interface")
    console.print("  [bold cyan]q[/bold cyan]  quit\n")

def main():
    # Direct launch: aios shell / aios files etc
    if len(sys.argv) > 1:
        key = sys.argv[1].lower()
        if key in MODULES:
            MODULES[key]()
        else:
            console.print(f"[red]Unknown: {key}[/red]")
            console.print(f"[dim]Options: {', '.join(MODULES.keys())}[/dim]")
        return

    while True:
        show_menu()
        try:
            choice = input("  choose → ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            break
        if choice in ("q", "quit", "exit"):
            console.print("[dim]  goodbye.[/dim]")
            break
        elif choice in MODULES:
            MODULES[choice]()
        else:
            console.print("[red]  invalid — enter 1-5 or q[/red]")
            time.sleep(0.8)

if __name__ == "__main__":
    main()
import subprocess, requests, json, os
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"

SYSTEM_PROMPT = """You are a Linux shell assistant on Ubuntu 24.04.
Convert the user's plain English into a safe bash command.

Rules:
1. Reply with ONLY this format — nothing else:
   CMD: <bash command here>
   EXPLAIN: <one line explanation>

2. For dangerous commands (rm -rf, dd, mkfs), add CONFIRM: yes
3. If the request is unclear, reply:
   ERROR: <what's unclear>
4. Never use sudo unless the user explicitly says "as root"
5. Prefer safe flags (e.g. rm -i instead of rm -f)

Examples:
User: show large files in home folder
CMD: du -ah ~ | sort -rh | head -20
EXPLAIN: Lists the 20 largest files/folders in your home directory

User: delete all .tmp files in downloads
CMD: find ~/Downloads -name "*.tmp" -delete
EXPLAIN: Deletes all .tmp files in Downloads
CONFIRM: yes
"""

def ask_ollama(user_input):
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": f"{SYSTEM_PROMPT}\n\nUser: {user_input}\n",
            "stream": False
        }, timeout=30)
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"ERROR: Cannot reach Ollama — {e}"

def parse_response(response):
    cmd, explain, confirm = None, None, False
    for line in response.splitlines():
        if line.startswith("CMD:"):
            cmd = line.replace("CMD:", "").strip()
        elif line.startswith("EXPLAIN:"):
            explain = line.replace("EXPLAIN:", "").strip()
        elif line.startswith("CONFIRM:"):
            confirm = True
        elif line.startswith("ERROR:"):
            return None, line.replace("ERROR:", "").strip(), False
    return cmd, explain, confirm

def run_command(cmd):
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, cwd=os.path.expanduser("~")
    )
    return result.stdout, result.stderr

def main():
    session = PromptSession(
        history=FileHistory(os.path.expanduser("~/.nlshell_history")),
        style=Style.from_dict({"prompt": "ansicyan bold"})
    )
    console.print(Panel.fit(
        "[bold green]NL Shell[/bold green] — speak English, run Linux\n"
        "[dim]Type what you want to do. 'exit' to quit. '!cmd' to run raw bash.[/dim]",
        border_style="green"
    ))

    while True:
        try:
            user_input = session.prompt("  you → ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                break

            # Raw bash passthrough with ! prefix
            if user_input.startswith("!"):
                raw = user_input[1:]
                out, err = run_command(raw)
                if out: console.print(out)
                if err: console.print(f"[red]{err}[/red]")
                continue

            console.print("[dim]thinking...[/dim]", end="\r")
            response = ask_ollama(user_input)
            cmd, explain, needs_confirm = parse_response(response)

            if cmd is None:
                console.print(f"[yellow]unclear:[/yellow] {explain}")
                continue

            # Show the translated command
            console.print()
            console.print(Syntax(cmd, "bash", theme="monokai", background_color="default"))
            console.print(f"[dim]{explain}[/dim]")

            # Ask for confirmation
            if needs_confirm:
                console.print("[red bold]⚠ This command is destructive.[/red bold]")

            confirm = input("  run? [y/n] → ").strip().lower()
            if confirm == "y":
                out, err = run_command(cmd)
                if out:
                    console.print(Panel(out.strip(), border_style="dim", title="output"))
                if err:
                    console.print(Panel(err.strip(), border_style="red", title="error"))
            else:
                console.print("[dim]skipped.[/dim]")

        except KeyboardInterrupt:
            console.print("\n[dim]Ctrl+C — type 'exit' to quit[/dim]")
        except EOFError:
            break

if __name__ == "__main__":
    main()

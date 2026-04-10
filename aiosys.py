import subprocess, psutil, requests, json
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich import print as rprint

console = Console()
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"

SYSTEM_PROMPT = """You are aiosys — the AI brain of a custom Linux OS running on Ubuntu.
You have access to real system data. Be concise, helpful, and direct.
When the user asks to run a command, output ONLY this format:
CMD: <the exact bash command>
For everything else, just answer naturally."""

def get_system_context():
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    procs = sorted(psutil.process_iter(['name','cpu_percent','memory_percent']),
                   key=lambda p: p.info['cpu_percent'] or 0, reverse=True)[:5]
    top = ', '.join([p.info['name'] for p in procs if p.info['name']])
    return (f"System state — CPU: {cpu}% | "
            f"RAM: {ram.percent}% used ({ram.available//1024//1024}MB free) | "
            f"Disk: {disk.percent}% used | Top processes: {top}")

def ask_ollama(prompt, history):
    ctx = get_system_context()
    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\nCurrent " + ctx}]
    messages += history
    messages.append({"role": "user", "content": prompt})
    full_prompt = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in messages])
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": full_prompt,
            "stream": True
        }, stream=True, timeout=60)
        response = ""
        console.print("\n[bold green]aiosys:[/bold green] ", end="")
        for line in r.iter_lines():
            if line:
                data = json.loads(line)
                chunk = data.get("response", "")
                print(chunk, end="", flush=True)
                response += chunk
                if data.get("done"):
                    break
        print()
        return response.strip()
    except Exception as e:
        return f"Error: {e}"

def execute_command(cmd):
    console.print(f"\n[yellow]Run:[/yellow] {cmd}")
    confirm = Prompt.ask("[red]Execute?[/red]", choices=["y", "n"], default="n")
    if confirm == "y":
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        output = result.stdout or result.stderr
        if output:
            console.print(Panel(output.strip(), title="output", border_style="dim"))
        return output
    return "Cancelled."

def main():
    console.print(Panel.fit(
        "[bold]aiosys v0.1[/bold] — AI-native OS daemon\n"
        "[dim]Type anything in plain English. Say 'exit' to quit.[/dim]",
        border_style="green"
    ))
    history = []
    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]you[/bold cyan]")
            if user_input.lower() in ("exit", "quit"):
                console.print("[dim]aiosys shutting down.[/dim]")
                break
            response = ask_ollama(user_input, history)
            if response.startswith("CMD:"):
                cmd = response.replace("CMD:", "").strip()
                execute_command(cmd)
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": response})
            if len(history) > 20:
                history = history[-20:]
        except KeyboardInterrupt:
            console.print("\n[dim]Use 'exit' to quit.[/dim]")

if __name__ == "__main__":
    main()

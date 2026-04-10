import os, time, requests, json, threading
import psutil
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich.prompt import Prompt
from datetime import datetime

console = Console()
OLLAMA_URL  = "http://localhost:11434/api/generate"
MODEL       = "llama3.2:3b"

# --- Thresholds ---
WARN_CPU    = 75   # %
WARN_RAM    = 80   # %
WARN_DISK   = 85   # %
WARN_TEMP   = 75   # celsius

# --- Ask AI about a system problem ---
def ask_ai(problem):
    prompt = f"""You are an expert Linux system administrator.
The user's system has this issue: {problem}
Give a SHORT diagnosis (2 sentences max) and ONE specific fix command.
Format:
DIAGNOSIS: <what's happening>
FIX: <exact bash command>"""
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False
        }, timeout=30)
        return r.json().get("response", "").strip()
    except:
        return "Could not reach Ollama."

# --- Get CPU temperature ---
def get_temp():
    try:
        temps = psutil.sensors_temperatures()
        if not temps:
            return None
        for name, entries in temps.items():
            for entry in entries:
                if entry.current > 0:
                    return entry.current
    except:
        return None

# --- Get system snapshot ---
def get_snapshot():
    cpu     = psutil.cpu_percent(interval=0.3)
    ram     = psutil.virtual_memory()
    disk    = psutil.disk_usage("/")
    net     = psutil.net_io_counters()
    temp    = get_temp()
    procs   = sorted(
        psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]),
        key=lambda p: p.info["cpu_percent"] or 0,
        reverse=True
    )[:8]
    return {
        "cpu":    cpu,
        "ram":    ram,
        "disk":   disk,
        "net":    net,
        "temp":   temp,
        "procs":  procs,
        "time":   datetime.now().strftime("%H:%M:%S")
    }

# --- Build the live dashboard ---
def build_dashboard(snap, alerts):
    lines = []

    # Header
    lines.append(Text(
        f"  aios monitor — {snap['time']}",
        style="bold green"
    ))
    lines.append(Text(""))

    # CPU
    cpu = snap["cpu"]
    cpu_bar = make_bar(cpu)
    cpu_style = "red" if cpu > WARN_CPU else "yellow" if cpu > 50 else "green"
    lines.append(Text(f"  CPU   {cpu_bar} {cpu:.1f}%", style=cpu_style))

    # RAM
    ram = snap["ram"]
    ram_pct = ram.percent
    ram_bar = make_bar(ram_pct)
    ram_style = "red" if ram_pct > WARN_RAM else "yellow" if ram_pct > 60 else "green"
    ram_used = ram.used  // 1024 // 1024
    ram_total = ram.total // 1024 // 1024
    lines.append(Text(
        f"  RAM   {ram_bar} {ram_pct:.1f}%  ({ram_used}MB / {ram_total}MB)",
        style=ram_style
    ))

    # Disk
    disk = snap["disk"]
    disk_pct = disk.percent
    disk_bar = make_bar(disk_pct)
    disk_style = "red" if disk_pct > WARN_DISK else "yellow" if disk_pct > 70 else "green"
    disk_free = disk.free // 1024 // 1024 // 1024
    lines.append(Text(
        f"  DISK  {disk_bar} {disk_pct:.1f}%  ({disk_free}GB free)",
        style=disk_style
    ))

    # Temperature
    temp = snap["temp"]
    if temp:
        temp_style = "red" if temp > WARN_TEMP else "yellow" if temp > 60 else "green"
        temp_bar = make_bar(min(temp, 100))
        lines.append(Text(
            f"  TEMP  {temp_bar} {temp:.1f}°C",
            style=temp_style
        ))

    # Network
    net = snap["net"]
    sent = net.bytes_sent // 1024
    recv = net.bytes_recv // 1024
    lines.append(Text(
        f"  NET   ↑ {sent}KB sent   ↓ {recv}KB recv",
        style="dim"
    ))

    lines.append(Text(""))

    # Top processes
    lines.append(Text("  Top processes:", style="bold"))
    for p in snap["procs"]:
        try:
            cpu_p = p.info["cpu_percent"] or 0
            ram_p = p.info["memory_percent"] or 0
            name  = (p.info["name"] or "?")[:22]
            pid   = p.info["pid"]
            style = "red" if cpu_p > 40 else "yellow" if cpu_p > 15 else "dim"
            lines.append(Text(
                f"  {pid:>6}  {name:<22}  CPU {cpu_p:>5.1f}%  RAM {ram_p:>4.1f}%",
                style=style
            ))
        except:
            continue

    # Alerts
    if alerts:
        lines.append(Text(""))
        lines.append(Text("  Alerts:", style="bold red"))
        for alert in alerts[-3:]:
            lines.append(Text(f"  ! {alert}", style="red"))

    lines.append(Text(""))
    lines.append(Text(
        "  [q] quit   [a] ask AI about current state   [k] kill a process",
        style="dim"
    ))

    combined = Text("\n").join(lines)
    return Panel(combined, border_style="green", title="aios monitor")

def make_bar(pct, width=20):
    filled = int(pct / 100 * width)
    bar    = "█" * filled + "░" * (width - filled)
    return f"[{bar}]"

# --- Detect problems and generate alerts ---
def check_alerts(snap, prev_alerts):
    alerts = list(prev_alerts)
    cpu  = snap["cpu"]
    ram  = snap["ram"].percent
    disk = snap["disk"].percent
    temp = snap["temp"]

    if cpu > WARN_CPU:
        msg = f"CPU at {cpu:.1f}%"
        if msg not in alerts:
            alerts.append(msg)
    if ram > WARN_RAM:
        msg = f"RAM at {ram:.1f}%"
        if msg not in alerts:
            alerts.append(msg)
    if disk > WARN_DISK:
        msg = f"Disk at {disk:.1f}%"
        if msg not in alerts:
            alerts.append(msg)
    if temp and temp > WARN_TEMP:
        msg = f"Temperature at {temp:.1f}°C"
        if msg not in alerts:
            alerts.append(msg)

    return alerts[-5:]  # keep last 5

# --- Kill a process by PID ---
def kill_process():
    pid_str = input("\n  Enter PID to kill → ").strip()
    if pid_str.isdigit():
        pid = int(pid_str)
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            confirm = input(f"  Kill '{name}' (PID {pid})? [y/n] → ").strip().lower()
            if confirm == "y":
                proc.terminate()
                console.print(f"[green]  Sent SIGTERM to {name} (PID {pid})[/green]")
            else:
                console.print("[dim]  Cancelled.[/dim]")
        except psutil.NoSuchProcess:
            console.print("[red]  Process not found.[/red]")
        except psutil.AccessDenied:
            console.print("[red]  Permission denied — try with sudo.[/red]")

# --- Ask AI about current system state ---
def ai_diagnose(snap, alerts):
    parts = []
    parts.append(f"CPU: {snap['cpu']:.1f}%")
    parts.append(f"RAM: {snap['ram'].percent:.1f}% ({snap['ram'].used//1024//1024}MB used)")
    parts.append(f"Disk: {snap['disk'].percent:.1f}%")
    if snap["temp"]:
        parts.append(f"Temperature: {snap['temp']:.1f}C")

    top_procs = []
    for p in snap["procs"][:3]:
        try:
            top_procs.append(f"{p.info['name']} (CPU:{p.info['cpu_percent']:.1f}%)")
        except:
            pass
    if top_procs:
        parts.append(f"Top processes: {', '.join(top_procs)}")
    if alerts:
        parts.append(f"Active alerts: {', '.join(alerts)}")

    problem = " | ".join(parts)
    console.print("\n[dim]  Asking AI...[/dim]")
    response = ask_ai(problem)
    console.print(Panel(response, title="AI diagnosis", border_style="cyan"))
    input("\n  Press Enter to continue...")

# --- Main ---
def main():
    console.print(Panel.fit(
        "[bold green]aios monitor[/bold green] — real time system intelligence\n"
        "[dim]Watches CPU · RAM · Disk · Temp · Processes[/dim]",
        border_style="green"
    ))

    alerts   = []
    snapshot = get_snapshot()

    stop_flag = threading.Event()

    def monitor_loop():
        nonlocal snapshot, alerts
        while not stop_flag.is_set():
            snapshot = get_snapshot()
            alerts   = check_alerts(snapshot, alerts)
            # Clear screen and redraw
            os.system("clear")
            console.print(build_dashboard(snapshot, alerts))
            time.sleep(2)

    # Start monitor in background thread
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()

    while True:
        try:
            cmd = input("").strip().lower()
            if cmd == "q":
                stop_flag.set()
                console.print("[dim]Monitor stopped.[/dim]")
                break
            elif cmd == "a":
                stop_flag.set()
                time.sleep(0.3)
                ai_diagnose(snapshot, alerts)
                # Restart monitor
                stop_flag.clear()
                t = threading.Thread(target=monitor_loop, daemon=True)
                t.start()
            elif cmd == "k":
                stop_flag.set()
                time.sleep(0.3)
                kill_process()
                stop_flag.clear()
                t = threading.Thread(target=monitor_loop, daemon=True)
                t.start()
            elif cmd == "h":
                console.print("[dim]Commands: a=AI diagnose  k=kill process  q=quit[/dim]")
        except (KeyboardInterrupt, EOFError):
            stop_flag.set()
            break

if __name__ == "__main__":
    main()
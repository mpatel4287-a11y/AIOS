#!/usr/bin/env python3
"""Sia — AI companion. Voice + gestures + avatar + web search + activity."""
import os, sys, time, re, ctypes, threading, subprocess, tempfile
import psutil, requests
from datetime import datetime

ALSA_ERROR_HANDLER = None

def suppress_alsa():
    global ALSA_ERROR_HANDLER
    try:
        lib = ctypes.cdll.LoadLibrary("libasound.so.2")
        ALSA_ERROR_HANDLER = ctypes.CFUNCTYPE(
            None, ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p
        )(lambda *a: None)
        lib.snd_lib_error_set_handler(ALSA_ERROR_HANDLER)
    except:
        pass

suppress_alsa()

# Fix threading issues with numpy/audio libs
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"]      = "1"
os.environ["MKL_NUM_THREADS"]      = "1"

from rich.console import Console
from rich.panel   import Panel
from rich.table   import Table
console = Console()

# ── Paths ───────────────────────────────────────────────────────
HOME      = os.path.expanduser("~")
AIOS_DIR  = os.path.join(HOME, "aios")
VENV_PY   = os.path.join(AIOS_DIR, "venv/bin/python3")
OLLAMA_URL = "http://localhost:11434/api/generate"
LLM_MODEL  = "llama3.2:3b"
WAKE_WORD  = "sia"

# ── State ───────────────────────────────────────────────────────
speak_lock     = threading.Lock()
conv_history   = []
activity_log   = []
avatar_proc    = None
gesture_proc   = None
sia_busy       = threading.Event()

GESTURE_MAP = {
    "pinch_close":   ("close tab",      "xdotool key ctrl+w"),
    "pinch_open":    ("new tab",         "xdotool key ctrl+t"),
    "swipe_left":    ("previous tab",    "xdotool key ctrl+shift+Tab"),
    "swipe_right":   ("next tab",        "xdotool key ctrl+Tab"),
    "swipe_up":      ("scroll up",       "xdotool key Prior"),
    "swipe_down":    ("scroll down",     "xdotool key Next"),
    "thumbs_up":     ("wake Sia",        "WAKE_SIA"),
    "thumbs_down":   ("volume down",     "pactl set-sink-volume @DEFAULT_SINK@ -10%"),
    "open_palm":     ("escape",          "xdotool key Escape"),
    "fist":          ("lock screen",     "gnome-screensaver-command --lock"),
    "peace":         ("open terminal",   "gnome-terminal"),
    "ok_sign":       ("screenshot",      "gnome-screenshot -f ~/Pictures/gest.png"),
    "point_up":      ("volume up",       "pactl set-sink-volume @DEFAULT_SINK@ +10%"),
    "call_me":       ("brightness down", "brightnessctl set 10%-"),
    "three_fingers": ("brightness up",   "brightnessctl set +10%"),
}

# ═══════════════════════════════════════════════════════════════
# AVATAR
# ═══════════════════════════════════════════════════════════════
def avatar_set(expr):
    global avatar_proc
    if avatar_proc and avatar_proc.poll() is None:
        try:
            avatar_proc.stdin.write(f"EXPR:{expr}\n")
            avatar_proc.stdin.flush()
        except:
            pass

def start_avatar():
    global avatar_proc
    script = os.path.join(AIOS_DIR, "sia_avatar.py")
    if not os.path.exists(script):
        console.print("[yellow]  sia_avatar.py not found[/yellow]")
        return
    try:
        avatar_proc = subprocess.Popen(
            [VENV_PY, script],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1
        )
        console.print("[dim]  Avatar started.[/dim]")
    except Exception as e:
        console.print(f"[yellow]  Avatar: {e}[/yellow]")

# ═══════════════════════════════════════════════════════════════
# VOICE — gTTS + mpg123 (no segfaults, natural female voice)
# ═══════════════════════════════════════════════════════════════
def speak(text):
    """Speak text — uses gTTS female voice."""
    with speak_lock:
        clean = re.sub(r"[*#`_\[\]<>\n]", "", text).strip()
        if not clean:
            return
        console.print(f"\n[bold magenta]Sia:[/bold magenta] {clean}")
        avatar_set("speaking")
        _voice(clean)
        avatar_set("idle")

def _voice(text):
    """Internal voice function — gTTS with espeak fallback."""
    # Try gTTS (Google TTS — natural female, needs internet)
    try:
        from gtts import gTTS
        tts = gTTS(text=text, lang="en", tld="co.in", slow=False)
        tmp = tempfile.mktemp(suffix=".mp3")
        tts.save(tmp)
        subprocess.run(
            ["mpg123", "-q", tmp],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30
        )
        os.unlink(tmp)
        return
    except Exception as e:
        console.print(f"[dim]  gtts failed: {e} — using espeak[/dim]")

    # Fallback — espeak female voice (offline)
    try:
        subprocess.run(
            ["espeak-ng", "-v", "en-us+f3",
             "-s", "140", "-p", "68", "-a", "90", text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15
        )
    except Exception:
        try:
            subprocess.run(
                ["espeak", "-v", "en-us+f3",
                 "-s", "140", "-p", "68", text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e:
            console.print(f"[red]  voice error: {e}[/red]")

def speak_bg(text):
    """Non-blocking speak."""
    threading.Thread(
        target=speak, args=(text,), daemon=True).start()

# ═══════════════════════════════════════════════════════════════
# SPEECH RECOGNITION — runs in its own thread only
# ═══════════════════════════════════════════════════════════════
def _get_sr():
    """Import speech_recognition with stderr suppressed."""
    dn = os.open(os.devnull, os.O_WRONLY)
    old = os.dup(2)
    os.dup2(dn, 2)
    try:
        import speech_recognition as sr
        return sr
    finally:
        os.dup2(old, 2)
        os.close(dn)
        os.close(old)

def listen_once(timeout=8, phrase=12):
    """Listen for one voice command."""
    sr  = _get_sr()
    rec = sr.Recognizer()
    rec.energy_threshold         = 300
    rec.dynamic_energy_threshold = True
    rec.pause_threshold          = 0.8

    dn = os.open(os.devnull, os.O_WRONLY)
    old = os.dup(2)
    os.dup2(dn, 2)
    try:
        with sr.Microphone() as src:
            os.dup2(old, 2)
            os.close(dn)
            os.close(old)
            avatar_set("listening")
            console.print("[dim]  listening...[/dim]", end="\r")
            rec.adjust_for_ambient_noise(src, duration=0.2)
            try:
                audio = rec.listen(
                    src, timeout=timeout,
                    phrase_time_limit=phrase)
                text = rec.recognize_google(audio)
                console.print(f"[cyan]  you:[/cyan] {text}      ")
                avatar_set("thinking")
                return text.lower().strip()
            except sr.WaitTimeoutError:
                avatar_set("idle")
                return None
            except sr.UnknownValueError:
                console.print("[dim]  (unclear)[/dim]")
                avatar_set("idle")
                return None
            except Exception as e:
                console.print(f"[red]  listen: {e}[/red]")
                avatar_set("idle")
                return None
    except Exception as e:
        try:
            os.dup2(old, 2)
            os.close(dn)
            os.close(old)
        except:
            pass
        console.print(f"[red]  mic: {e}[/red]")
        return None

def wake_word_loop():
    """Always-on background wake word listener."""
    # Wait for startup speak to finish before opening mic
    time.sleep(6)

    sr  = _get_sr()
    rec = sr.Recognizer()
    rec.energy_threshold         = 250
    rec.dynamic_energy_threshold = True
    rec.pause_threshold          = 0.5
    console.print("[dim]  Wake word active — say 'Sia'[/dim]")

    while True:
        # Don't listen while Sia is speaking
        if sia_busy.is_set():
            time.sleep(0.5)
            continue
        try:
            dn = os.open(os.devnull, os.O_WRONLY)
            old = os.dup(2)
            os.dup2(dn, 2)
            with sr.Microphone() as src:
                os.dup2(old, 2)
                os.close(dn)
                os.close(old)
                rec.adjust_for_ambient_noise(src, duration=0.2)
                try:
                    audio = rec.listen(
                        src, timeout=3, phrase_time_limit=4)
                    text = rec.recognize_google(audio).lower()
                    if WAKE_WORD in text and not sia_busy.is_set():
                        console.print(
                            "\n[bold magenta]"
                            "[ Sia woke up ]"
                            "[/bold magenta]")
                        avatar_set("excited")
                        sia_busy.set()
                        handle_convo()
                        sia_busy.clear()
                except:
                    pass
        except Exception:
            try:
                os.dup2(old, 2)
                os.close(dn)
                os.close(old)
            except:
                pass
            time.sleep(1)

# ═══════════════════════════════════════════════════════════════
# WEB SEARCH
# ═══════════════════════════════════════════════════════════════
def web_search(query):
    try:
        from duckduckgo_search import DDGS
        with DDGS() as d:
            results = list(d.text(query, max_results=3))
        if not results:
            return "I couldn't find anything on that."
        snippets = " ".join(
            r.get("body", "") for r in results[:3]
            if r.get("body"))[:700]
        prompt = (
            f"Summarize this search result about '{query}' "
            f"into 2 natural spoken sentences. "
            f"Plain English only, no markdown.\n\n{snippets}")
        return ask_ai(prompt)
    except ImportError:
        return "Web search not available. Run: pip install duckduckgo-search"
    except Exception as e:
        return f"Search error: {e}"

WEB_WORDS = [
    "search for", "google", "look up", "find online",
    "latest news", "what happened", "news about",
    "weather", "price of", "who won", "when is",
    "where is", "who is",
]

def needs_search(t):
    return any(w in t.lower() for w in WEB_WORDS)

# ═══════════════════════════════════════════════════════════════
# AI BRAIN
# ═══════════════════════════════════════════════════════════════
def ask_ai(prompt, timeout=45):
    try:
        r = requests.post(
            OLLAMA_URL,
            json={"model": LLM_MODEL,
                  "prompt": prompt,
                  "stream": False},
            timeout=timeout)
        d = r.json()
        if "error" in d:
            return f"AI error: {d['error']}"
        return d.get("response", "").strip()
    except requests.exceptions.ConnectionError:
        return "I cannot reach Ollama. Please start it."
    except Exception as e:
        return f"Error: {e}"

def get_ctx():
    try:
        cpu  = psutil.cpu_percent(0.2)
        ram  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return (f"CPU {cpu:.0f}%, "
                f"RAM {ram.percent:.0f}% "
                f"({ram.available//1024//1024}MB free), "
                f"Disk {disk.percent:.0f}%")
    except:
        return ""

def sia_think(user_text):
    global conv_history

    # Web search if needed
    if needs_search(user_text):
        console.print("[dim]  searching web...[/dim]")
        avatar_set("thinking")
        result = web_search(user_text)
        if result and not result.startswith("Error"):
            conv_history.append({"r": "User", "c": user_text})
            conv_history.append({"r": "Sia",  "c": result})
            return result

    # Build history
    hist = "".join(
        f"{h['r']}: {h['c']}\n"
        for h in conv_history[-8:])

    prompt = (
        "You are Sia, a warm and intelligent female AI companion "
        "built into Linux. Reply in 1-3 sentences max. "
        "Plain spoken English only — "
        "no markdown, no bullets, no special characters.\n"
        f"System: {get_ctx()}\n\n"
        f"{hist}"
        f"User: {user_text}\n"
        "Sia:"
    )

    avatar_set("thinking")
    resp = ask_ai(prompt)

    conv_history.append({"r": "User", "c": user_text})
    conv_history.append({"r": "Sia",  "c": resp})
    if len(conv_history) > 20:
        conv_history = conv_history[-20:]

    return resp

# ═══════════════════════════════════════════════════════════════
# QUICK COMMANDS — instant, no AI needed
# ═══════════════════════════════════════════════════════════════
def quick(text):
    t = text.lower()

    # Time / date
    if any(w in t for w in
           ["what time", "current time", "time is it"]):
        return f"It's {datetime.now().strftime('%I:%M %p')}."
    if any(w in t for w in
           ["what date", "what day", "today's date"]):
        return (f"Today is "
                f"{datetime.now().strftime('%A, %B %d %Y')}.")

    # Battery
    if "battery" in t:
        try:
            b = psutil.sensors_battery()
            if b:
                s = "charging" if b.power_plugged else "on battery"
                return f"Battery is {b.percent:.0f}% and {s}."
        except:
            pass

    # System stats
    if "cpu" in t and any(
            w in t for w in ["how", "usage", "percent"]):
        return f"CPU is at {psutil.cpu_percent(0.3):.0f}%."

    if any(w in t for w in ["ram", "memory"]) and any(
            w in t for w in ["how", "free", "usage"]):
        r = psutil.virtual_memory()
        return (f"RAM is {r.percent:.0f}% used, "
                f"{r.available//1024//1024} MB free.")

    if any(w in t for w in ["disk", "storage", "space"]):
        d = psutil.disk_usage("/")
        return (f"Disk is {d.percent:.0f}% full, "
                f"{d.free//1024//1024//1024} GB free.")

    # Open apps
    apps = {
        "Brave":    ["Brave"],
        "browser":    ["Brave"],
        "terminal":   ["gnome-terminal"],
        "files":      ["nautilus"],
        "file manager": ["nautilus"],
        "settings":   ["gnome-control-center"],
        "calculator": ["gnome-calculator"],
        "vs code":    ["code"],
        "vscode":     ["code"],
    }
    for name, cmd in apps.items():
        if name in t and any(
                w in t for w in
                ["open", "launch", "start", "run"]):
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            avatar_set("happy")
            return f"Opening {name}."

    # Volume
    if any(w in t for w in ["volume up", "louder"]):
        subprocess.run(
            "pactl set-sink-volume @DEFAULT_SINK@ +10%",
            shell=True)
        return "Volume up."
    if any(w in t for w in ["volume down", "quieter"]):
        subprocess.run(
            "pactl set-sink-volume @DEFAULT_SINK@ -10%",
            shell=True)
        return "Volume down."
    if "mute" in t:
        subprocess.run(
            "pactl set-sink-mute @DEFAULT_SINK@ toggle",
            shell=True)
        return "Toggled mute."

    # Brightness
    if any(w in t for w in ["brighter", "brightness up"]):
        subprocess.run(
            "brightnessctl set +10%",
            shell=True, stdout=subprocess.DEVNULL)
        return "Brighter."
    if any(w in t for w in ["dimmer", "brightness down"]):
        subprocess.run(
            "brightnessctl set 10%-",
            shell=True, stdout=subprocess.DEVNULL)
        return "Dimmer."

    # Screenshot
    if "screenshot" in t:
        fn = (f"~/Pictures/sia_"
              f"{datetime.now().strftime('%H%M%S')}.png")
        subprocess.Popen(
            f"gnome-screenshot -f {fn}", shell=True)
        return "Screenshot saved to Pictures."

    # Lock
    if "lock" in t and "screen" in t:
        subprocess.Popen(
            "gnome-screensaver-command --lock", shell=True)
        return "Locking screen."

    return None  # Not a quick command — go to AI

# ═══════════════════════════════════════════════════════════════
# CONVERSATION HANDLER
# ═══════════════════════════════════════════════════════════════
def handle_convo():
    """Full conversation loop after wake word."""
    speak("Yes, I'm here!")
    silent = 0

    while True:
        # Don't try to listen while speaking
        while speak_lock.locked():
            time.sleep(0.1)

        text = listen_once(timeout=8)

        if text is None:
            silent += 1
            if silent >= 2:
                speak("I'll keep listening in the background.")
                avatar_set("idle")
                break
            continue

        silent = 0

        if any(w in text for w in
               ["goodbye", "bye", "sleep", "stop",
                "never mind", "that's all", "thats all"]):
            speak("Okay, I'm always here when you need me!")
            avatar_set("idle")
            break

        q = quick(text)
        if q:
            speak(q)
            continue

        resp = sia_think(text)
        speak(resp if resp else "I'm not sure about that.")

# ═══════════════════════════════════════════════════════════════
# GESTURE SUBPROCESS
# ═══════════════════════════════════════════════════════════════
def run_gestures():
    global gesture_proc
    script = os.path.join(AIOS_DIR, "sia_gesture.py")
    if not os.path.exists(script):
        console.print("[yellow]  sia_gesture.py not found[/yellow]")
        return
    try:
        gesture_proc = subprocess.Popen(
            [VENV_PY, script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1
        )
        console.print("[dim]  Gesture process started.[/dim]")

        for line in gesture_proc.stdout:
            line = line.strip()
            if line == "GESTURE_READY":
                console.print("[green]  Gestures ready.[/green]")
            elif line.startswith("GESTURE:"):
                g = line[8:]
                if g in GESTURE_MAP:
                    label, cmd = GESTURE_MAP[g]
                    console.print(
                        f"[bold cyan]  gesture:[/bold cyan] "
                        f"{g} → {label}")
                    if cmd == "WAKE_SIA":
                        if not sia_busy.is_set():
                            avatar_set("excited")
                            sia_busy.set()
                            threading.Thread(
                                target=lambda: (
                                    handle_convo(),
                                    sia_busy.clear()),
                                daemon=True).start()
                    else:
                        subprocess.Popen(
                            cmd, shell=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
            elif line.startswith("GESTURE_ERROR:"):
                console.print(f"[yellow]  {line}[/yellow]")

    except Exception as e:
        console.print(f"[yellow]  Gesture: {e}[/yellow]")

# ═══════════════════════════════════════════════════════════════
# ACTIVITY MONITOR
# ═══════════════════════════════════════════════════════════════
def get_window():
    try:
        r = subprocess.run(
            "xdotool getactivewindow getwindowname 2>/dev/null",
            shell=True, capture_output=True, text=True)
        return r.stdout.strip() or "unknown"
    except:
        return "unknown"

def categorize(title):
    t = title.lower()
    if any(w in t for w in
           ["youtube", "netflix", "twitch", "prime"]):
        return "entertainment"
    if any(w in t for w in
           ["Brave", "chrome", "chromium"]):
        return "browsing"
    if any(w in t for w in
           ["code", "vscode", "vim", "nano", "pycharm"]):
        return "coding"
    if any(w in t for w in ["terminal", "bash", "zsh"]):
        return "terminal"
    if any(w in t for w in
           ["slack", "discord", "telegram", "whatsapp"]):
        return "communication"
    return "other"

EXPR_MAP = {
    "coding":        "working",
    "entertainment": "happy",
    "communication": "happy",
    "terminal":      "working",
    "browsing":      "idle",
    "other":         "idle",
}

def activity_monitor():
    console.print("[dim]  Activity monitor active.[/dim]")
    app_times   = {}
    last_win    = ""
    last_t      = time.time()
    last_report = time.time()
    last_active = time.time()
    idle_warned = False

    # Track keyboard/mouse activity
    try:
        from pynput import mouse, keyboard

        def on_act(*a):
            nonlocal last_active, idle_warned
            last_active = time.time()
            idle_warned = False

        ml = mouse.Listener(
            on_move=on_act, on_click=on_act)
        kl = keyboard.Listener(on_press=on_act)
        ml.daemon = kl.daemon = True
        ml.start()
        kl.start()
    except Exception:
        pass

    while True:
        time.sleep(5)
        now = time.time()
        win = get_window()
        cat = categorize(win)

        # Update avatar based on activity
        if not speak_lock.locked():
            avatar_set(EXPR_MAP.get(cat, "idle"))

        # Track time per category
        if win != last_win:
            elapsed = now - last_t
            old_cat = categorize(last_win)
            app_times[old_cat] = (
                app_times.get(old_cat, 0) + elapsed)
            last_win = win
            last_t   = now

        # Idle warning — 30 minutes
        idle_min = (now - last_active) / 60
        if idle_min > 30 and not idle_warned:
            idle_warned = True
            avatar_set("idle")
            speak_bg(
                "Hey, you've been away for 30 minutes. "
                "Everything okay?")

        # Hourly summary
        if now - last_report > 3600:
            last_report = now
            total = sum(app_times.values())
            if total > 300:
                top = sorted(
                    app_times.items(),
                    key=lambda x: x[1],
                    reverse=True)[:2]
                parts = [
                    f"{int(s//60)} minutes on {c}"
                    for c, s in top if s > 60]
                if parts:
                    speak_bg(
                        "In the last hour you spent "
                        + " and ".join(parts) + ".")

        # Log entry
        try:
            activity_log.append({
                "time": datetime.now().isoformat(),
                "win":  win[:60],
                "cat":  cat,
                "cpu":  psutil.cpu_percent(),
                "ram":  psutil.virtual_memory().percent
            })
            if len(activity_log) > 500:
                activity_log.pop(0)
        except:
            pass

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def show_status():
    os.system("clear")
    console.print(Panel(
        "[bold magenta]  ███████╗██╗  █████╗ [/bold magenta]\n"
        "[bold magenta]  ██╔════╝██║ ██╔══██╗[/bold magenta]\n"
        "[bold magenta]  ███████╗██║ ███████║[/bold magenta]\n"
        "[bold magenta]  ╚════██║██║ ██╔══██║[/bold magenta]\n"
        "[bold magenta]  ███████║██║ ██║  ██║[/bold magenta]\n"
        "[bold magenta]  ╚══════╝╚═╝ ╚═╝  ╚═╝[/bold magenta]\n"
        "[dim]    Your AI companion — always here[/dim]",
        border_style="magenta"))
    ico = "[green]●[/green]"
    console.print(f"  {ico} Wake word  — say 'Sia' anytime")
    console.print(f"  {ico} Gestures   — webcam active")
    console.print(f"  {ico} Activity   — watching your work")
    console.print(f"  {ico} Web search — DuckDuckGo")
    console.print(f"  {ico} Voice      — gTTS female (Google)")
    console.print()
    console.print(
        "  [dim]t=talk  g=gestures  a=activity  "
        "s=status  q=quit[/dim]\n")

def main():
    show_status()

    # Start avatar first
    start_avatar()
    time.sleep(0.5)

    # Start background services
    services = [
        ("activity", activity_monitor),
        ("gestures", run_gestures),
        ("wake",     wake_word_loop),
    ]
    for name, fn in services:
        threading.Thread(
            target=fn, daemon=True, name=name).start()
        time.sleep(0.3)

    # Greeting — in background thread, not main thread
    # This prevents audio conflict with mic initialization
    def greet():
        time.sleep(2)  # Wait for all threads to settle
        speak(
            "Hi! I'm Sia, your personal AI companion. "
            "I'm now active and always with you. "
            "Just say my name whenever you need me!")

    threading.Thread(target=greet, daemon=True).start()

    # Main loop
    while True:
        try:
            cmd = input("  sia → ").strip().lower()
            if not cmd:
                continue

            if cmd == "q":
                speak("Goodbye! I'll always be here for you.")
                time.sleep(3)
                if avatar_proc:
                    try:
                        avatar_proc.terminate()
                    except:
                        pass
                break

            elif cmd == "t":
                # Text mode conversation
                speak("I'm listening, go ahead!")
                while True:
                    try:
                        text = input("  you → ").strip()
                        if not text or text.lower() in (
                                "back", "exit", "stop", "done"):
                            speak("Back to standby.")
                            break
                        q = quick(text)
                        speak(q if q else sia_think(text))
                    except (KeyboardInterrupt, EOFError):
                        break

            elif cmd == "v":
                # Voice conversation from menu
                sia_busy.set()
                handle_convo()
                sia_busy.clear()

            elif cmd == "g":
                # Show gesture map
                t = Table(
                    title="Sia Gesture Map",
                    border_style="magenta")
                t.add_column(
                    "Gesture", style="cyan", width=16)
                t.add_column(
                    "Action",  style="green")
                for g, (label, _) in GESTURE_MAP.items():
                    t.add_row(g, label)
                console.print(t)

            elif cmd == "a":
                # Activity summary
                if activity_log:
                    last = activity_log[-1]
                    console.print(Panel(
                        f"Window:   {last.get('win','?')}\n"
                        f"Category: {last.get('cat','?')}\n"
                        f"CPU: {last.get('cpu',0):.0f}%  "
                        f"RAM: {last.get('ram',0):.0f}%",
                        title="Current Activity",
                        border_style="magenta"))
                else:
                    console.print("[dim]No data yet.[/dim]")

            elif cmd == "s":
                show_status()

            else:
                # Treat input as text to Sia
                q = quick(cmd)
                speak(q if q else sia_think(cmd))

        except (KeyboardInterrupt, EOFError):
            speak("Goodbye!")
            time.sleep(2)
            if avatar_proc:
                try:
                    avatar_proc.terminate()
                except:
                    pass
            break

if __name__ == "__main__":
    main()
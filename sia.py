#!/usr/bin/env python3
"""
Sia — AI-native OS Companion
Always-on · Female voice · Gesture control · Activity monitor · Web search
"""
import os, sys, time, json, threading, subprocess, queue
import hashlib, requests, ctypes, re
from datetime import datetime, timedelta
from collections import deque
import psutil

# ── Suppress ALSA noise ──────────────────────────────────────────
def suppress_alsa():
    try:
        lib = ctypes.cdll.LoadLibrary("libasound.so.2")
        lib.snd_lib_error_set_handler(
            ctypes.CFUNCTYPE(
                None, ctypes.c_char_p, ctypes.c_int,
                ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p
            )(lambda *a: None)
        )
    except:
        pass

suppress_alsa()

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# ── Config ───────────────────────────────────────────────────────
WAKE_WORD    = "sia"
OLLAMA_URL   = "http://localhost:11434/api/generate"
LLM_MODEL    = "llama3.2:3b"
PIPER_MODEL  = os.path.expanduser("~/.aios/piper/en_US-amy-medium.onnx")
PIPER_CONFIG = os.path.expanduser("~/.aios/piper/en_US-amy-medium.onnx.json")
LOG_PATH     = os.path.expanduser("~/.aios/sia_activity.json")

# ── Shared state ─────────────────────────────────────────────────
sia_awake        = threading.Event()   # True when responding
speech_queue     = queue.Queue()       # text to speak
gesture_queue    = queue.Queue()       # detected gestures
activity_log     = []                  # app usage log
conversation_history = []             # chat memory

# ═══════════════════════════════════════════════════════════════
# 1. VOICE — Piper TTS (female)
# ═══════════════════════════════════════════════════════════════
def speak(text):
    """Speak using Piper female TTS."""
    clean = re.sub(r"[*#`_\[\]]", "", text).strip()
    if not clean:
        return
    console.print(f"\n[bold magenta]Sia:[/bold magenta] {clean}")
    try:
        process = subprocess.Popen(
            ["python3", "-c",
             f"""
import sys
sys.stderr = open('/dev/null','w')
from piper import PiperVoice
import wave, io, subprocess
voice = PiperVoice.load('{PIPER_MODEL}', config_path='{PIPER_CONFIG}', use_cuda=False)
buf = io.BytesIO()
with wave.open(buf, 'wb') as wf:
    voice.synthesize('''{clean.replace("'","")}''', wf)
p = subprocess.Popen(['aplay','-q','-'], stdin=subprocess.PIPE)
p.communicate(buf.getvalue())
"""],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        process.wait(timeout=30)
    except Exception:
        # Fallback to espeak female if piper fails
        subprocess.run(
            ["espeak", "-v", "en-us+f3", "-s", "150", clean],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

def speak_async(text):
    """Non-blocking speak."""
    threading.Thread(target=speak, args=(text,), daemon=True).start()

# ═══════════════════════════════════════════════════════════════
# 2. SPEECH RECOGNITION — Wake word + commands
# ═══════════════════════════════════════════════════════════════
def listen_for_wake_word():
    """Background thread — always listening for 'Sia'."""
    # Suppress stderr
    dn = os.open(os.devnull, os.O_WRONLY)
    old = os.dup(2)
    os.dup2(dn, 2)
    try:
        import speech_recognition as sr
    except ImportError:
        console.print("[red]speech_recognition not installed. Run: pip install SpeechRecognition[/red]")
        return
    finally:
        os.dup2(old, 2)
        os.close(dn); os.close(old)

    rec = sr.Recognizer()
    rec.energy_threshold         = 300
    rec.dynamic_energy_threshold = True
    rec.pause_threshold          = 0.6

    console.print("[dim]  Sia is listening for wake word...[/dim]")

    while True:
        try:
            dn  = os.open(os.devnull, os.O_WRONLY)
            old = os.dup(2)
            os.dup2(dn, 2)
            with sr.Microphone() as src:
                os.dup2(old, 2)
                os.close(dn); os.close(old)
                rec.adjust_for_ambient_noise(src, duration=0.2)
                try:
                    audio = rec.listen(src, timeout=3,
                                       phrase_time_limit=4)
                    text  = rec.recognize_google(audio).lower()
                    if WAKE_WORD in text:
                        console.print(
                            f"\n[bold magenta][ Sia woke up ][/bold magenta]")
                        sia_awake.set()
                        handle_conversation()
                        sia_awake.clear()
                except sr.WaitTimeoutError:
                    pass
                except sr.UnknownValueError:
                    pass
                except Exception:
                    pass
        except Exception:
            try:
                os.dup2(old, 2)
                os.close(dn); os.close(old)
            except:
                pass
            time.sleep(1)

def listen_once(timeout=8):
    """Listen for a single command after wake."""
    dn = os.open(os.devnull, os.O_WRONLY)
    old = os.dup(2)
    os.dup2(dn, 2)
    try:
        import speech_recognition as sr
    except ImportError:
        console.print("[red]speech_recognition not installed. Run: pip install SpeechRecognition[/red]")
        return None
    finally:
        os.dup2(old, 2)
        os.close(dn); os.close(old)

    rec = sr.Recognizer()
    rec.energy_threshold = 300
    rec.pause_threshold  = 0.8

    dn  = os.open(os.devnull, os.O_WRONLY)
    old = os.dup(2)
    os.dup2(dn, 2)
    try:
        with sr.Microphone() as src:
            os.dup2(old, 2)
            os.close(dn); os.close(old)
            console.print("[dim]  listening...[/dim]", end="\r")
            rec.adjust_for_ambient_noise(src, duration=0.2)
            try:
                audio = rec.listen(src, timeout=timeout,
                                   phrase_time_limit=12)
                text  = rec.recognize_google(audio)
                console.print(f"[cyan]  you:[/cyan] {text}      ")
                return text.lower().strip()
            except sr.WaitTimeoutError:
                return None
            except sr.UnknownValueError:
                console.print("[dim]  (could not understand)[/dim]")
                return None
            except Exception as e:
                return None
    except Exception:
        try:
            os.dup2(old, 2)
            os.close(dn); os.close(old)
        except:
            pass
        return None

# ═══════════════════════════════════════════════════════════════
# 3. INTERNET SEARCH
# ═══════════════════════════════════════════════════════════════
def search_web(query):
    """Search DuckDuckGo and return a spoken-friendly answer."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
        if not results:
            return None
        # Combine top 3 snippets
        snippets = [r.get("body", "") for r in results if r.get("body")]
        combined = " ".join(snippets[:2])[:600]
        # Ask Ollama to summarize into spoken answer
        prompt = (
            f"Summarize this web search result for '{query}' "
            f"into 2 spoken sentences. Plain English only, no markdown.\n\n"
            f"Search results: {combined}"
        )
        return ask_ollama(prompt)
    except Exception as e:
        console.print(f"[red]Search error: {e}[/red]")
        return None

def needs_web_search(text):
    """Detect if query needs internet."""
    web_keywords = [
        "search", "google", "look up", "find online",
        "what is the latest", "news", "today", "current",
        "weather", "price", "score", "who won", "when is",
        "how to", "what is", "tell me about", "explain"
    ]
    return any(kw in text for kw in web_keywords)

# ═══════════════════════════════════════════════════════════════
# 4. AI BRAIN — Ollama
# ═══════════════════════════════════════════════════════════════
def ask_ollama(prompt, timeout=45):
    try:
        r = requests.post(OLLAMA_URL, json={
            "model":  LLM_MODEL,
            "prompt": prompt,
            "stream": False
        }, timeout=timeout)
        data = r.json()
        if "error" in data:
            return f"I got an error: {data['error']}"
        return data.get("response", "").strip()
    except requests.exceptions.ConnectionError:
        return "I cannot reach my AI engine. Please start Ollama."
    except Exception as e:
        return f"Something went wrong: {e}"

def get_system_context():
    try:
        cpu  = psutil.cpu_percent(0.2)
        ram  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return (f"CPU {cpu:.0f}%, RAM {ram.percent:.0f}% "
                f"({ram.available//1024//1024}MB free), "
                f"Disk {disk.percent:.0f}%")
    except:
        return ""

def sia_think(user_text):
    """Main AI response — checks web search need first."""
    global conversation_history

    # Check if web search needed
    if needs_web_search(user_text):
        console.print("[dim]  searching the web...[/dim]")
        web_result = search_web(user_text)
        if web_result:
            return web_result

    # Build conversation prompt
    ctx     = get_system_context()
    history = ""
    for h in conversation_history[-8:]:
        history += f"{h['role']}: {h['content']}\n"

    prompt = (
        f"You are Sia, a female AI assistant built into Linux. "
        f"You are helpful, smart, and friendly with a warm personality. "
        f"Keep replies to 1-3 sentences. Plain English only — no markdown, "
        f"no bullet points. Speak naturally as if talking.\n"
        f"System: {ctx}\n\n"
        f"{history}"
        f"User: {user_text}\n"
        f"Sia:"
    )
    response = ask_ollama(prompt)

    # Save to history
    conversation_history.append({"role": "User", "content": user_text})
    conversation_history.append({"role": "Sia",  "content": response})
    if len(conversation_history) > 20:
        conversation_history = conversation_history[-20:]

    return response

# ═══════════════════════════════════════════════════════════════
# 5. QUICK COMMANDS — instant responses without AI
# ═══════════════════════════════════════════════════════════════
def handle_quick_command(text):
    """Handle common commands instantly."""
    t = text.lower()

    # Time / date
    if any(w in t for w in ["time", "clock"]):
        return f"It's {datetime.now().strftime('%I:%M %p')}."
    if any(w in t for w in ["date", "today", "day is"]):
        return f"Today is {datetime.now().strftime('%A, %B %d %Y')}."

    # System stats
    if "cpu" in t:
        return f"CPU is at {psutil.cpu_percent(0.3):.0f} percent."
    if any(w in t for w in ["ram", "memory"]):
        r = psutil.virtual_memory()
        return f"RAM is {r.percent:.0f} percent used, {r.available//1024//1024} megabytes free."
    if any(w in t for w in ["disk", "storage", "space"]):
        d = psutil.disk_usage("/")
        return f"Disk is {d.percent:.0f} percent full, {d.free//1024//1024//1024} gigabytes free."
    if "battery" in t:
        try:
            b = psutil.sensors_battery()
            if b:
                status = "charging" if b.power_plugged else "not charging"
                return f"Battery is at {b.percent:.0f} percent and {status}."
        except:
            pass

    # App control
    apps = {
        "firefox": "firefox",
        "browser": "firefox",
        "terminal": "gnome-terminal",
        "files": "nautilus",
        "settings": "gnome-control-center",
        "calculator": "gnome-calculator",
        "vs code": "code",
        "vscode": "code",
    }
    for name, cmd in apps.items():
        if name in t and any(
            w in t for w in ["open", "launch", "start", "run"]
        ):
            subprocess.Popen(cmd, shell=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return f"Opening {name} for you."

    # Volume
    if "volume up" in t or "louder" in t:
        subprocess.run("pactl set-sink-volume @DEFAULT_SINK@ +10%",
                       shell=True)
        return "Volume increased."
    if "volume down" in t or "quieter" in t:
        subprocess.run("pactl set-sink-volume @DEFAULT_SINK@ -10%",
                       shell=True)
        return "Volume decreased."
    if "mute" in t:
        subprocess.run("pactl set-sink-mute @DEFAULT_SINK@ toggle",
                       shell=True)
        return "Toggled mute."

    # Brightness
    if "brightness up" in t or "brighter" in t:
        subprocess.run("brightnessctl set +10%", shell=True,
                       stdout=subprocess.DEVNULL)
        return "Brightness increased."
    if "brightness down" in t or "dimmer" in t:
        subprocess.run("brightnessctl set 10%-", shell=True,
                       stdout=subprocess.DEVNULL)
        return "Brightness decreased."

    # Screenshot
    if "screenshot" in t:
        fname = f"~/Pictures/screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        subprocess.Popen(f"gnome-screenshot -f {fname}", shell=True)
        return "Screenshot taken and saved to Pictures."

    # Lock
    if "lock" in t and "screen" in t:
        subprocess.Popen("gnome-screensaver-command --lock", shell=True)
        return "Locking the screen."

    # Aios modules
    if any(w in t for w in ["open shell", "nl shell", "natural language shell"]):
        subprocess.Popen(
            "gnome-terminal -- bash -c 'cd ~/aios && source venv/bin/activate && python3 aios.py shell; bash'",
            shell=True)
        return "Opening the natural language shell."
    if any(w in t for w in ["file search", "find files", "search files"]):
        subprocess.Popen(
            "gnome-terminal -- bash -c 'cd ~/aios && source venv/bin/activate && python3 aios.py files; bash'",
            shell=True)
        return "Opening AI file search."
    if any(w in t for w in ["monitor", "system monitor"]):
        subprocess.Popen(
            "gnome-terminal -- bash -c 'cd ~/aios && source venv/bin/activate && python3 aios.py monitor; bash'",
            shell=True)
        return "Opening system monitor."

    return None  # Not a quick command — send to AI

# ═══════════════════════════════════════════════════════════════
# 6. CONVERSATION HANDLER
# ═══════════════════════════════════════════════════════════════
def handle_conversation():
    """Full back-and-forth conversation after wake word."""
    speak("Yes, I'm here.")
    silent_count = 0

    while True:
        text = listen_once(timeout=8)

        if text is None:
            silent_count += 1
            if silent_count >= 2:
                speak("I'll be here when you need me.")
                break
            continue

        silent_count = 0

        # Stop words
        if any(w in text for w in
               ["goodbye", "bye", "sleep", "stop", "never mind",
                "that's all", "thats all"]):
            speak("Okay, I'll be listening in the background.")
            break

        # Try quick command first
        quick = handle_quick_command(text)
        if quick:
            speak(quick)
            continue

        # Full AI response
        console.print("[dim]  thinking...[/dim]", end="\r")
        response = sia_think(text)
        if response:
            speak(response)
        else:
            speak("I'm not sure about that. Can you rephrase?")

# ═══════════════════════════════════════════════════════════════
# 7. GESTURE CONTROL — MediaPipe
# ═══════════════════════════════════════════════════════════════

# Gesture → action mapping (fully customizable)
GESTURE_ACTIONS = {
    "pinch_close":    ("close tab",       "xdotool key ctrl+w"),
    "pinch_open":     ("new tab",         "xdotool key ctrl+t"),
    "swipe_left":     ("previous tab",    "xdotool key ctrl+shift+Tab"),
    "swipe_right":    ("next tab",        "xdotool key ctrl+Tab"),
    "swipe_up":       ("scroll up",       "xdotool key Prior"),
    "swipe_down":     ("scroll down",     "xdotool key Next"),
    "thumbs_up":      ("wake Sia",        "WAKE_SIA"),
    "thumbs_down":    ("volume down",     "pactl set-sink-volume @DEFAULT_SINK@ -10%"),
    "open_palm":      ("stop/back",       "xdotool key Escape"),
    "fist":           ("lock screen",     "gnome-screensaver-command --lock"),
    "peace":          ("open terminal",   "gnome-terminal"),
    "ok_sign":        ("screenshot",      "gnome-screenshot -f ~/Pictures/gesture_screenshot.png"),
    "point_up":       ("volume up",       "pactl set-sink-volume @DEFAULT_SINK@ +10%"),
    "call_me":        ("brightness down", "brightnessctl set 10%-"),
    "three_fingers":  ("brightness up",   "brightnessctl set +10%"),
}

def run_gesture_action(gesture):
    """Execute the action mapped to a gesture."""
    if gesture not in GESTURE_ACTIONS:
        return
    label, cmd = GESTURE_ACTIONS[gesture]
    console.print(f"[bold cyan]  gesture:[/bold cyan] {gesture} → {label}")

    if cmd == "WAKE_SIA":
        console.print("[bold magenta][ Sia woke up via gesture ][/bold magenta]")
        threading.Thread(target=handle_conversation, daemon=True).start()
        return

    subprocess.Popen(cmd, shell=True,
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)

def detect_gesture(hand_landmarks, hand_history):
    """Detect gesture from MediaPipe hand landmarks."""
    import mediapipe as mp
    lm = hand_landmarks.landmark
    mp_hands = mp.solutions.hands

    # Key landmark indices
    WRIST        = 0
    THUMB_TIP    = 4
    THUMB_MCP    = 2
    INDEX_TIP    = 8
    INDEX_MCP    = 5
    MIDDLE_TIP   = 12
    MIDDLE_MCP   = 9
    RING_TIP     = 16
    RING_MCP     = 13
    PINKY_TIP    = 20
    PINKY_MCP    = 17

    def tip_above_mcp(tip, mcp):
        return lm[tip].y < lm[mcp].y

    def dist(a, b):
        return ((lm[a].x - lm[b].x)**2 +
                (lm[a].y - lm[b].y)**2) ** 0.5

    # Finger states
    thumb_up   = lm[THUMB_TIP].y < lm[THUMB_MCP].y
    index_up   = tip_above_mcp(INDEX_TIP, INDEX_MCP)
    middle_up  = tip_above_mcp(MIDDLE_TIP, MIDDLE_MCP)
    ring_up    = tip_above_mcp(RING_TIP, RING_MCP)
    pinky_up   = tip_above_mcp(PINKY_TIP, PINKY_MCP)

    fingers_up = sum([index_up, middle_up, ring_up, pinky_up])

    # Pinch detection (thumb + index close together)
    pinch_dist = dist(THUMB_TIP, INDEX_TIP)

    # Track pinch history for open/close
    if "pinch" not in hand_history:
        hand_history["pinch"] = deque(maxlen=10)
    hand_history["pinch"].append(pinch_dist)

    # Swipe detection — track wrist x position
    wrist_x = lm[WRIST].x
    if "wrist_x" not in hand_history:
        hand_history["wrist_x"] = deque(maxlen=15)
    hand_history["wrist_x"].append(wrist_x)

    wrist_y = lm[WRIST].y
    if "wrist_y" not in hand_history:
        hand_history["wrist_y"] = deque(maxlen=15)
    hand_history["wrist_y"].append(wrist_y)

    # ── Detect gestures ──────────────────────────────────────

    # Pinch close (fingers come together)
    if (len(hand_history["pinch"]) >= 8 and
            pinch_dist < 0.04 and
            hand_history["pinch"][0] > 0.08):
        return "pinch_close"

    # Pinch open (fingers move apart)
    if (len(hand_history["pinch"]) >= 8 and
            pinch_dist > 0.12 and
            hand_history["pinch"][0] < 0.06):
        return "pinch_open"

    # Swipe left
    if (len(hand_history["wrist_x"]) >= 12 and
            hand_history["wrist_x"][0] - wrist_x > 0.2):
        return "swipe_left"

    # Swipe right
    if (len(hand_history["wrist_x"]) >= 12 and
            wrist_x - hand_history["wrist_x"][0] > 0.2):
        return "swipe_right"

    # Swipe up
    if (len(hand_history["wrist_y"]) >= 12 and
            hand_history["wrist_y"][0] - wrist_y > 0.15):
        return "swipe_up"

    # Swipe down
    if (len(hand_history["wrist_y"]) >= 12 and
            wrist_y - hand_history["wrist_y"][0] > 0.15):
        return "swipe_down"

    # Open palm (all fingers up)
    if fingers_up == 4 and not thumb_up:
        return "open_palm"

    # Fist (all fingers down)
    if fingers_up == 0 and not thumb_up:
        return "fist"

    # Thumbs up (thumb up, others down)
    if thumb_up and fingers_up == 0:
        return "thumbs_up"

    # Thumbs down (thumb down, others down)
    if not thumb_up and fingers_up == 0 and lm[THUMB_TIP].y > lm[WRIST].y:
        return "thumbs_down"

    # Peace / V sign (index + middle up, others down)
    if index_up and middle_up and not ring_up and not pinky_up:
        return "peace"

    # Point up (only index up)
    if index_up and not middle_up and not ring_up and not pinky_up:
        return "point_up"

    # OK sign (thumb + index form circle, others up)
    if pinch_dist < 0.05 and middle_up and ring_up and pinky_up:
        return "ok_sign"

    # Call me (thumb + pinky up)
    if thumb_up and pinky_up and not index_up and not middle_up and not ring_up:
        return "call_me"

    # Three fingers (index + middle + ring)
    if index_up and middle_up and ring_up and not pinky_up:
        return "three_fingers"

    return None

def run_gesture_control():
    """Background thread — webcam gesture detection."""
    try:
        import mediapipe as mp
        import cv2
    except ImportError:
        console.print("[red]mediapipe/opencv not installed. "
                      "Run: pip install mediapipe opencv-python[/red]")
        return

    mp_hands    = mp.solutions.hands
    mp_draw     = mp.solutions.drawing_utils
    hands_model = mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=1,
        min_detection_confidence=0.7,
        min_tracking_confidence=0.6
    )

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        console.print("[red]Cannot open webcam.[/red]")
        return

    console.print("[dim]  Gesture control active (webcam on)[/dim]")

    hand_history       = {}
    last_gesture       = None
    last_gesture_time  = 0
    COOLDOWN           = 1.5  # seconds between same gesture

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        # Flip for mirror view
        frame = cv2.flip(frame, 1)
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = hands_model.process(rgb)

        if result.multi_hand_landmarks:
            for hand_lm in result.multi_hand_landmarks:
                gesture = detect_gesture(hand_lm, hand_history)

                if gesture:
                    now = time.time()
                    # Cooldown — don't repeat same gesture too fast
                    if (gesture != last_gesture or
                            now - last_gesture_time > COOLDOWN):
                        last_gesture      = gesture
                        last_gesture_time = now
                        hand_history.clear()  # Reset after gesture
                        run_gesture_action(gesture)

                # Draw landmarks (optional — comment out for performance)
                mp_draw.draw_landmarks(
                    frame, hand_lm, mp_hands.HAND_CONNECTIONS)

        # Small window showing gesture feed (optional)
        cv2.imshow("Sia Gestures — press Q to hide", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            cv2.destroyAllWindows()

        time.sleep(0.03)  # ~30fps

    cap.release()

# ═══════════════════════════════════════════════════════════════
# 8. ACTIVITY MONITOR — watches what you do (no camera)
# ═══════════════════════════════════════════════════════════════
def get_active_window():
    """Get current active window title."""
    try:
        result = subprocess.run(
            "xdotool getactivewindow getwindowname 2>/dev/null",
            shell=True, capture_output=True, text=True
        )
        return result.stdout.strip() or "unknown"
    except:
        return "unknown"

def categorize_app(window_title):
    """Categorize what the user is doing."""
    t = window_title.lower()
    if any(w in t for w in ["firefox", "chrome", "chromium", "browser"]):
        if any(w in t for w in ["youtube", "netflix", "twitch"]):
            return "entertainment"
        if any(w in t for w in ["stackoverflow", "github", "docs"]):
            return "learning"
        return "browsing"
    if any(w in t for w in ["code", "vscode", "vim", "nano",
                              "gedit", "sublime", "pycharm"]):
        return "coding"
    if any(w in t for w in ["terminal", "bash", "zsh", "konsole"]):
        return "terminal"
    if any(w in t for w in ["libreoffice", "writer", "calc",
                              "document", "spreadsheet"]):
        return "documents"
    if any(w in t for w in ["slack", "discord", "telegram",
                              "whatsapp", "chat"]):
        return "communication"
    return "other"

def run_activity_monitor():
    """Background thread — monitors app usage and gives insights."""
    console.print("[dim]  Activity monitor active[/dim]")

    app_times      = {}       # category → seconds
    last_window    = ""
    last_time      = time.time()
    last_report    = time.time()
    idle_warned    = False
    last_activity  = time.time()

    # Try to detect keyboard/mouse activity
    try:
        from pynput import mouse, keyboard

        def on_activity(*args):
            nonlocal last_activity, idle_warned
            last_activity = time.time()
            idle_warned   = False

        mouse_listener    = mouse.Listener(
            on_move=on_activity, on_click=on_activity)
        keyboard_listener = keyboard.Listener(
            on_press=on_activity)
        mouse_listener.daemon    = True
        keyboard_listener.daemon = True
        mouse_listener.start()
        keyboard_listener.start()
    except Exception:
        pass

    while True:
        time.sleep(5)
        now    = time.time()
        window = get_active_window()

        if window != last_window:
            # Track time on previous window
            elapsed  = now - last_time
            category = categorize_app(last_window)
            app_times[category] = app_times.get(category, 0) + elapsed
            last_window = window
            last_time   = now

        # Idle detection — no activity for 30 minutes
        idle_minutes = (now - last_activity) / 60
        if idle_minutes > 30 and not idle_warned:
            idle_warned = True
            speak_async(
                "Hey, you've been idle for 30 minutes. "
                "Everything okay?"
            )

        # Hourly report
        if now - last_report > 3600:
            last_report = now
            generate_activity_report(app_times)

        # Save log
        try:
            log_entry = {
                "time":     datetime.now().isoformat(),
                "window":   window,
                "category": categorize_app(window),
                "cpu":      psutil.cpu_percent(),
                "ram":      psutil.virtual_memory().percent
            }
            activity_log.append(log_entry)
            if len(activity_log) > 1000:
                activity_log.pop(0)
        except:
            pass

def generate_activity_report(app_times):
    """Generate and speak an activity summary."""
    if not app_times:
        return
    total = sum(app_times.values())
    if total < 60:
        return

    top = sorted(app_times.items(), key=lambda x: x[1], reverse=True)[:3]
    parts = []
    for cat, secs in top:
        mins = int(secs // 60)
        if mins > 0:
            parts.append(f"{mins} minutes {cat}")

    if parts:
        summary = "In the past hour you spent " + ", ".join(parts) + "."
        speak_async(summary)

def get_activity_summary():
    """Return a summary of recent activity for Sia to use."""
    if not activity_log:
        return "No activity data yet."
    recent = activity_log[-12:]  # last minute of 5s samples
    cats   = [e.get("category", "other") for e in recent]
    most   = max(set(cats), key=cats.count) if cats else "unknown"
    window = recent[-1].get("window", "unknown") if recent else "unknown"
    return (f"Currently doing: {most}. "
            f"Active window: {window[:50]}.")

# ═══════════════════════════════════════════════════════════════
# 9. GESTURE CUSTOMIZER
# ═══════════════════════════════════════════════════════════════
def show_gesture_map():
    """Display current gesture mappings."""
    table = Table(title="Sia Gesture Map", border_style="magenta")
    table.add_column("Gesture",  style="cyan", width=16)
    table.add_column("Action",   style="green")
    table.add_column("Command",  style="dim")
    for gesture, (label, cmd) in GESTURE_ACTIONS.items():
        table.add_row(gesture, label, cmd[:50])
    console.print(table)

def customize_gesture():
    """Let user remap a gesture to a custom command."""
    show_gesture_map()
    console.print("\nAvailable gestures:")
    for g in GESTURE_ACTIONS:
        console.print(f"  {g}")
    gesture = input("\nGesture to remap → ").strip()
    if gesture not in GESTURE_ACTIONS:
        console.print("[red]Unknown gesture.[/red]")
        return
    label = input("Action label → ").strip()
    cmd   = input("Bash command → ").strip()
    GESTURE_ACTIONS[gesture] = (label, cmd)
    console.print(f"[green]Remapped '{gesture}' → {label}[/green]")

# ═══════════════════════════════════════════════════════════════
# 10. MAIN — Start everything
# ═══════════════════════════════════════════════════════════════
def show_status():
    """Show Sia's current status."""
    os.system("clear")
    console.print(Panel(
        "[bold magenta]  ███████╗██╗ █████╗ [/bold magenta]\n"
        "[bold magenta]  ██╔════╝██║██╔══██╗[/bold magenta]\n"
        "[bold magenta]  ███████╗██║███████║[/bold magenta]\n"
        "[bold magenta]  ╚════██║██║██╔══██║[/bold magenta]\n"
        "[bold magenta]  ███████║██║██║  ██║[/bold magenta]\n"
        "[bold magenta]  ╚══════╝╚═╝╚═╝  ╚═╝[/bold magenta]\n"
        "[dim]    AI Companion — always with you[/dim]",
        border_style="magenta"
    ))
    console.print("  [bold]Status[/bold]")
    console.print("  [green]●[/green] Voice     — listening for 'Sia'")
    console.print("  [green]●[/green] Gestures  — webcam active")
    console.print("  [green]●[/green] Activity  — monitoring your work")
    console.print("  [green]●[/green] Web search — DuckDuckGo ready")
    console.print()
    console.print("  [bold]Commands[/bold]")
    console.print("  [cyan]g[/cyan]  show gesture map")
    console.print("  [cyan]r[/cyan]  customize a gesture")
    console.print("  [cyan]a[/cyan]  show activity summary")
    console.print("  [cyan]t[/cyan]  talk to Sia (text mode)")
    console.print("  [cyan]q[/cyan]  quit Sia\n")

def main():
    show_status()

    # ── Start all background threads ──────────────────────────
    threads = [
        threading.Thread(target=listen_for_wake_word, daemon=True,
                         name="wake_word"),
        threading.Thread(target=run_gesture_control, daemon=True,
                         name="gestures"),
        threading.Thread(target=run_activity_monitor, daemon=True,
                         name="activity"),
    ]
    for t in threads:
        t.start()

    time.sleep(1)
    speak("Hi, I'm Sia. I'm now always with you. "
          "Just say my name whenever you need me.")

    # ── Main menu loop ────────────────────────────────────────
    while True:
        try:
            cmd = input("  sia → ").strip().lower()

            if cmd == "q":
                speak("Goodbye. I'll always be here when you need me.")
                break

            elif cmd == "g":
                show_gesture_map()

            elif cmd == "r":
                customize_gesture()

            elif cmd == "a":
                summary = get_activity_summary()
                console.print(Panel(summary, title="Activity",
                                    border_style="magenta"))

            elif cmd == "t":
                # Text mode conversation
                speak("Text mode. Type your message.")
                while True:
                    try:
                        text = input("  you → ").strip()
                        if not text or text.lower() in (
                                "back", "exit", "stop"):
                            speak("Back to standby.")
                            break
                        quick = handle_quick_command(text)
                        if quick:
                            speak(quick)
                        else:
                            console.print("[dim]thinking...[/dim]",
                                          end="\r")
                            resp = sia_think(text)
                            speak(resp)
                    except (KeyboardInterrupt, EOFError):
                        break

            elif cmd == "s":
                show_status()

            elif cmd:
                # Treat as text command to Sia
                quick = handle_quick_command(cmd)
                if quick:
                    speak(quick)
                else:
                    resp = sia_think(cmd)
                    speak(resp)

        except (KeyboardInterrupt, EOFError):
            speak("Goodbye!")
            break

if __name__ == "__main__":
    main()

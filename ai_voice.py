import os, requests, json, threading, queue, time
import speech_recognition as sr
import pyttsx3
from rich.console import Console
from rich.panel import Panel
import psutil

console = Console()
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "llama3.2:3b"

# --- Text to speech ---
engine = pyttsx3.init()
engine.setProperty("rate", 165)    # speaking speed
engine.setProperty("volume", 0.9)

def speak(text):
    """Speak text aloud."""
    clean = text.replace("*", "").replace("#", "").replace("`", "")
    console.print(f"[bold green]aios:[/bold green] {clean}")
    engine.say(clean)
    engine.runAndWait()

# --- Speech to text ---
recognizer = sr.Recognizer()
recognizer.energy_threshold    = 300
recognizer.dynamic_energy_threshold = True
recognizer.pause_threshold     = 0.8

def listen(timeout=6):
    """Listen for one voice command. Returns text or None."""
    with sr.Microphone() as source:
        console.print("[dim]  listening...[/dim]", end="\r")
        try:
            recognizer.adjust_for_ambient_noise(source, duration=0.3)
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=8)
            text  = recognizer.recognize_google(audio)
            console.print(f"[cyan]  you said:[/cyan] {text}      ")
            return text.lower().strip()
        except sr.WaitTimeoutError:
            return None
        except sr.UnknownValueError:
            console.print("[dim]  (could not understand)[/dim]")
            return None
        except sr.RequestError as e:
            console.print(f"[red]  speech service error: {e}[/red]")
            return None

# --- Get system context ---
def get_context():
    cpu  = psutil.cpu_percent(interval=0.2)
    ram  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return (
        f"System: CPU {cpu:.1f}%, "
        f"RAM {ram.percent:.1f}% used ({ram.available//1024//1024}MB free), "
        f"Disk {disk.percent:.1f}% used."
    )

# --- Ask Ollama ---
def ask_ai(user_input, history):
    context = get_context()
    system  = f"""You are aios, an AI voice assistant built into Linux.
You are talking to the user through voice — keep all replies SHORT (2-3 sentences max).
Never use markdown, bullet points, or special characters — plain spoken English only.
Current {context}"""

    messages_text = system + "\n\n"
    for h in history[-6:]:
        messages_text += f"{h['role'].upper()}: {h['content']}\n"
    messages_text += f"USER: {user_input}\n"

    try:
        r = requests.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": messages_text,
            "stream": False
        }, timeout=30)
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"Sorry, I could not reach the AI engine. Error: {e}"

# --- Handle special commands ---
def handle_command(text):
    """Check for direct OS commands before sending to AI."""
    if any(w in text for w in ["what time", "current time", "time is it"]):
        from datetime import datetime
        t = datetime.now().strftime("%I:%M %p")
        return f"The current time is {t}."

    if any(w in text for w in ["cpu", "processor"]) and \
       any(w in text for w in ["how", "what", "usage", "percent"]):
        cpu = psutil.cpu_percent(interval=0.5)
        return f"Your CPU is at {cpu:.1f} percent usage right now."

    if any(w in text for w in ["ram", "memory"]) and \
       any(w in text for w in ["how", "what", "usage", "free"]):
        ram = psutil.virtual_memory()
        free = ram.available // 1024 // 1024
        return f"RAM is {ram.percent:.1f} percent used with {free} megabytes free."

    if any(w in text for w in ["disk", "storage", "space"]):
        disk = psutil.disk_usage("/")
        free = disk.free // 1024 // 1024 // 1024
        return f"Disk is {disk.percent:.1f} percent used with {free} gigabytes free."

    if any(w in text for w in ["shutdown", "turn off", "power off"]):
        return "I won't shut down the system without manual confirmation for safety."

    return None  # not a direct command — send to AI

# --- Wake word mode ---
def wait_for_wake_word():
    """Keep listening until 'hey os' or 'aios' is heard."""
    console.print("[dim]  waiting for wake word: 'hey os' or 'aios'...[/dim]", end="\r")
    while True:
        text = listen(timeout=4)
        if text and any(w in text for w in ["hey os", "aios", "hey aios", "a ios"]):
            return True

# --- Main ---
def main():
    console.print(Panel.fit(
        "[bold green]aios voice[/bold green] — talk to your OS\n"
        "[dim]Say 'hey os' to wake  ·  'goodbye' to sleep  ·  Ctrl+C to quit\n"
        "Modes:\n"
        "  [1] Always listening  — responds to everything\n"
        "  [2] Wake word mode    — only wakes on 'hey os'[/dim]",
        border_style="green"
    ))

    mode = input("\n  Choose mode [1/2] → ").strip()
    use_wake_word = (mode == "2")

    speak("aios voice is ready. How can I help you?")

    history = []

    while True:
        try:
            if use_wake_word:
                wait_for_wake_word()
                speak("Yes?")

            text = listen(timeout=6)
            if not text:
                continue

            # Exit command
            if any(w in text for w in ["goodbye", "bye", "stop listening", "quit"]):
                speak("Goodbye. aios voice going to sleep.")
                if use_wake_word:
                    continue
                else:
                    break

            # Try direct command first (instant, no AI needed)
            direct = handle_command(text)
            if direct:
                speak(direct)
                history.append({"role": "user",      "content": text})
                history.append({"role": "assistant",  "content": direct})
                continue

            # Send to AI
            console.print("[dim]  thinking...[/dim]", end="\r")
            response = ask_ai(text, history)

            speak(response)

            history.append({"role": "user",      "content": text})
            history.append({"role": "assistant",  "content": response})

            # Keep history short
            if len(history) > 12:
                history = history[-12:]

        except KeyboardInterrupt:
            speak("Shutting down voice interface.")
            console.print("\n[dim]Voice interface stopped.[/dim]")
            break

if __name__ == "__main__":
    main()

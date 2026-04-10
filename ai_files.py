import os, hashlib, base64, requests
import chromadb
from chromadb.utils import embedding_functions
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
import fitz  # PyMuPDF

console = Console()

# --- Config ---
INDEX_DIRS = [os.path.expanduser("~")]

SKIP_DIRS = {
    ".cache", ".local", ".config", ".mozilla", ".chrome",
    ".thunderbird", "node_modules", "__pycache__", ".git",
    ".npm", ".cargo", ".rustup", ".gradle", "venv",
    ".venv", "env", ".wine", "snap", ".snap",
    ".docker", ".minikube", "proc", "sys", ".ollama",
    ".steam", ".var", "lost+found", ".aios", ".gemini"
}

def is_ignored(path):
    """Check if the path should be ignored (hidden or in SKIP_DIRS)."""
    parts = os.path.abspath(path).split(os.sep)
    for p in parts:
        if not p: continue
        if p.startswith(".") and p not in {".", ".."}:
            return True
        if p in SKIP_DIRS:
            return True
    return False

SUPPORTED = {
    ".txt", ".md", ".py", ".json", ".csv", ".html",
    ".sh", ".js", ".ts", ".yaml", ".yml", ".toml",
    ".ini", ".conf", ".log", ".rs", ".go", ".pdf",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"
}

IMAGE_EXTS  = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
DB_PATH     = os.path.expanduser("~/.aios/fileindex")
OLLAMA_URL  = "http://localhost:11434/api/generate"
VISION_MODEL = "llava:7b"

# --- Setup ChromaDB ---
os.makedirs(DB_PATH, exist_ok=True)
client = chromadb.PersistentClient(path=DB_PATH)
ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)
collection = client.get_or_create_collection(
    name="files",
    embedding_function=ef
)

# --- Check llava ---
def llava_available():
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        return any("llava" in m for m in models)
    except:
        return False

LLAVA_READY = llava_available()
if LLAVA_READY:
    console.print("[green]llava detected — use 'inspect <number>' to AI-describe any image[/green]")
else:
    console.print("[yellow]llava not found — images indexed by filename (ollama pull llava:7b to enable)[/yellow]")

# --- Read file content ---
def read_file(path):
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            doc = fitz.open(path)
            return " ".join(page.get_text() for page in doc)[:3000]
        else:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()[:3000]
    except:
        return ""

# --- Image: fast filename-based description (used during bulk index) ---
def image_text_fast(path):
    name   = os.path.splitext(os.path.basename(path))[0].replace("_", " ").replace("-", " ")
    folder = os.path.basename(os.path.dirname(path))
    ext    = os.path.splitext(path)[1].lower()
    return f"image photo picture {ext.replace('.', '')} {name} folder {folder}"

# --- Image: AI vision description (used on demand) ---
def describe_image_ai(path):
    if not LLAVA_READY:
        return image_text_fast(path)
    try:
        with open(path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
        r = requests.post(OLLAMA_URL, json={
            "model": VISION_MODEL,
            "prompt": (
                "Describe this image for a search index. "
                "Include objects, colors, any visible text, and scene context. "
                "Be specific. 3-4 sentences."
            ),
            "images": [img_b64],
            "stream": False
        }, timeout=60)
        desc = r.json().get("response", "").strip()
        return desc if desc else image_text_fast(path)
    except Exception as e:
        return image_text_fast(path)

# --- File hash for change detection ---
def file_hash(path):
    try:
        s = os.stat(path)
        return hashlib.md5(f"{path}{s.st_mtime}{s.st_size}".encode()).hexdigest()
    except:
        return ""

# --- Index a single file (used by watcher) ---
def index_file(path, use_vision=False):
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext not in SUPPORTED:
            return
        if is_ignored(path):
            return
        size = os.path.getsize(path)
        if size < 512 or size > 10 * 1024 * 1024:
            return

        doc_id = hashlib.md5(path.encode()).hexdigest()
        name   = os.path.basename(path)

        if ext in IMAGE_EXTS:
            content = describe_image_ai(path) if use_vision else image_text_fast(path)
        elif ext == ".svg":
            try:
                with open(path, "r", errors="ignore") as f:
                    content = f"SVG image {name}: {f.read()[:300]}"
            except:
                content = image_text_fast(path)
        else:
            content = read_file(path)

        if not content or not content.strip():
            return

        stat = os.stat(path)
        collection.upsert(
            ids=[doc_id],
            documents=[f"{name}\n{content}"],
            metadatas=[{
                "path":  path,
                "name":  name,
                "ext":   ext,
                "size":  stat.st_size,
                "mtime": stat.st_mtime,
            }]
        )
    except:
        pass

def already_indexed(path):
    """Returns True if file is already indexed with same mtime."""
    try:
        doc_id = hashlib.md5(path.encode()).hexdigest()
        result = collection.get(ids=[doc_id])
        if not result["ids"]:
            return False
        stored_mtime = result["metadatas"][0].get("mtime", 0)
        current_mtime = os.path.getmtime(path)
        return abs(stored_mtime - current_mtime) < 1.0
    except:
        return False

# --- Bulk index all files ---
def index_all():
    console.print("[dim]Scanning home directory...[/dim]")
    all_files = []

    for directory in INDEX_DIRS:
        if not os.path.exists(directory):
            continue
        for root, dirs, files in os.walk(directory):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in SKIP_DIRS
            ]
            for fname in files:
                path = os.path.join(root, fname)
                ext = os.path.splitext(path)[1].lower()
                if ext not in SUPPORTED:
                    continue
                try:
                    size = os.path.getsize(path)
                    if size < 512 or size > 10 * 1024 * 1024:
                        continue
                except:
                    continue
                all_files.append(path)

    total = len(all_files)
    console.print(f"[dim]Found {total} files. Indexing (no AI vision during bulk — fast mode)...[/dim]")

    BATCH   = 20
    indexed = 0

    for i in range(0, total, BATCH):
        batch = all_files[i:i + BATCH]
        ids, docs, metas = [], [], []

        for path in batch:
            # SKIP if already indexed and unchanged
            if already_indexed(path):
                indexed += 1
                continue

            try:
                ext  = os.path.splitext(path)[1].lower()
                name = os.path.basename(path)
                stat = os.stat(path)

                # Always use fast text for images during bulk index
                if ext in IMAGE_EXTS:
                    content = image_text_fast(path)
                elif ext == ".svg":
                    try:
                        with open(path, "r", errors="ignore") as f:
                            content = f"SVG image {name}: {f.read()[:300]}"
                    except:
                        content = image_text_fast(path)
                else:
                    content = read_file(path)

                if not content or not content.strip():
                    continue

                doc_id = hashlib.md5(path.encode()).hexdigest()
                ids.append(doc_id)
                docs.append(f"{name}\n{content}")
                metas.append({
                    "path":  path,
                    "name":  name,
                    "ext":   ext,
                    "size":  stat.st_size,
                    "mtime": stat.st_mtime,
                })
            except:
                continue

        if ids:
            try:
                collection.upsert(ids=ids, documents=docs, metadatas=metas)
                indexed += len(ids)
            except:
                pass

        done = min(i + BATCH, total)
        pct  = int(done / total * 100) if total else 100
        bar  = "#" * (pct // 5) + "-" * (20 - pct // 5)
        print(f"  [{bar}] {pct}% — {done}/{total} files ({indexed} indexed)", end="\r", flush=True)

    print()
    console.print(f"[green]Done! {indexed} files indexed.[/green]")

# --- Live watcher ---
class FileWatcher(FileSystemEventHandler):
    def on_modified(self, event):
        if not event.is_directory and not is_ignored(event.src_path):
            index_file(event.src_path)

    def on_created(self, event):
        if not event.is_directory and not is_ignored(event.src_path):
            index_file(event.src_path)
            console.print(f"\n[dim]+ {os.path.basename(event.src_path)}[/dim]")

    def on_deleted(self, event):
        if not event.is_directory and not is_ignored(event.src_path):
            doc_id = hashlib.md5(event.src_path.encode()).hexdigest()
            try:
                collection.delete(ids=[doc_id])
                console.print(f"\n[dim]- {os.path.basename(event.src_path)}[/dim]")
            except:
                pass

def start_watcher():
    observer = Observer()
    handler  = FileWatcher()
    for directory in INDEX_DIRS:
        if os.path.exists(directory):
            observer.schedule(handler, directory, recursive=True)
    observer.daemon = True
    observer.start()
    console.print("[dim]Watching home directory for changes...[/dim]\n")

# --- Search ---
def search(query, n=8):
    try:
        count = collection.count()
        if count == 0:
            console.print("[yellow]Index empty — wait for indexing to finish.[/yellow]")
            return None
        results = collection.query(
            query_texts=[query],
            n_results=min(n, count)
        )
        return results
    except Exception as e:
        console.print(f"[red]Search error: {e}[/red]")
        return None

def display_results(results, query):
    if not results or not results["ids"][0]:
        console.print("[yellow]No matches found.[/yellow]")
        return []

    table = Table(
        title=f'"{query}"',
        border_style="dim",
        show_lines=False
    )
    table.add_column("#",     style="dim", width=3)
    table.add_column("File",  style="cyan", max_width=32)
    table.add_column("Type",  width=6)
    table.add_column("Size",  width=8)
    table.add_column("Match", width=7)
    table.add_column("Path",  style="dim", max_width=38)

    metas = results["metadatas"][0]
    dists = results["distances"][0]

    for i, (meta, dist) in enumerate(zip(metas, dists)):
        size     = meta.get("size", 0)
        size_str = f"{size // 1024}KB" if size > 1024 else f"{size}B"
        score    = max(0, int((1 - dist) * 100))
        color    = "green" if score > 70 else "yellow" if score > 40 else "dim"
        table.add_row(
            str(i + 1),
            meta.get("name", "?"),
            meta.get("ext",  "?"),
            size_str,
            f"[{color}]{score}%[/{color}]",
            meta.get("path", "?")
        )

    console.print(table)
    return metas

# --- Main ---
def main():
    console.print(Panel.fit(
        "[bold green]AI File Manager[/bold green] — semantic search across your home\n"
        "[dim]Commands:\n"
        "  <anything>          — search\n"
        "  inspect <number>    — AI describe an image result\n"
        "  count               — show total indexed files\n"
        "  reindex             — rebuild the index\n"
        "  exit                — quit[/dim]",
        border_style="green"
    ))

    index_all()
    start_watcher()

    last_results = []

    while True:
        try:
            query = Prompt.ask("\n[cyan]find[/cyan]").strip()
            if not query:
                continue

            if query.lower() == "exit":
                break

            elif query.lower() == "reindex":
                index_all()

            elif query.lower() == "count":
                console.print(f"[dim]{collection.count()} files in index[/dim]")

            elif query.lower().startswith("inspect "):
                # AI vision describe a specific result
                num = query.split()[1]
                if num.isdigit():
                    idx = int(num) - 1
                    if last_results and 0 <= idx < len(last_results):
                        path = last_results[idx]["path"]
                        ext  = os.path.splitext(path)[1].lower()
                        if ext in IMAGE_EXTS:
                            console.print(f"[dim]Describing with llava... (may take 10-20s)[/dim]")
                            desc = describe_image_ai(path)
                            console.print(Panel(desc, title=os.path.basename(path), border_style="cyan"))
                            # Re-index this image with AI description
                            index_file(path, use_vision=True)
                            console.print("[dim]Image re-indexed with AI description.[/dim]")
                        else:
                            console.print("[yellow]inspect only works on image files[/yellow]")
                    else:
                        console.print("[yellow]No result at that number — search first[/yellow]")

            else:
                results = search(query)
                if results:
                    last_results = display_results(results, query)
                    if last_results:
                        choice = Prompt.ask(
                            "Open file? Enter number (or Enter to skip)",
                            default=""
                        )
                        if choice.isdigit():
                            idx = int(choice) - 1
                            if 0 <= idx < len(last_results):
                                path = last_results[idx]["path"]
                                os.system(f'xdg-open "{path}" 2>/dev/null &')
                                console.print(f"[green]Opening {path}[/green]")

        except KeyboardInterrupt:
            console.print("\n[dim]Type 'exit' to quit[/dim]")
        except EOFError:
            break

if __name__ == "__main__":
    main()

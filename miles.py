import sys
import os
import threading
import subprocess
import urllib.request
import urllib.error
import urllib.parse
import asyncio
import json
import queue
import re
import socket
import uuid
import base64
from flask import Flask, request, jsonify, send_file, make_response
from flask_cors import CORS
from PyQt5.QtWidgets import QApplication, QMainWindow, QDesktopWidget
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
from PyQt5.QtCore import Qt, QUrl, pyqtSignal, QObject, QTimer
import time
import psutil
import datetime
import random
import wikipedia
import pyautogui
import glob

try:
    from deep_translator import GoogleTranslator
except ImportError:
    GoogleTranslator = None

try:
    import edge_tts
    EDGE_TTS_OK = True
except ImportError:
    edge_tts = None
    EDGE_TTS_OK = False
    print("[WARN] edge_tts not available. Browser MP3 TTS will use system fallback.")

# --- Audio backend: sounddevice + vosk (no PyAudio / no C++ compiler needed) ---
try:
    import sounddevice as sd
    import numpy as np
    SOUNDDEVICE_OK = True
except ImportError:
    SOUNDDEVICE_OK = False
    print("[WARN] sounddevice not available. Run: pip install sounddevice numpy")

try:
    from vosk import Model, KaldiRecognizer
    VOSK_OK = True
except ImportError:
    VOSK_OK = False
    print("[WARN] vosk not available. Run: pip install vosk")

# --- Flask Server ---
app_flask = Flask(__name__, static_folder='.', static_url_path='')
app_flask.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
CORS(app_flask)

@app_flask.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:latest")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "120"))

# --- Persistent Memory ---
MEMORY_FILE = os.path.join(BASE_DIR, 'memory.json')
SETTINGS_FILE = os.path.join(BASE_DIR, 'settings.json')
DEFAULT_SETTINGS = {
    "ai_provider": "groq",
    "groq_api_key": os.environ.get("GROQ_API_KEY", "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"),
    "groq_model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
    "ollama_model": os.environ.get("OLLAMA_MODEL", "qwen2.5"),
    "voice_rate": 104,
    "voice_pitch": 6,
    "voice_lang": "auto"
}

def json_request(url, payload=None, timeout=10):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'

    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode('utf-8', 'ignore')
        return json.loads(body) if body else {}

def get_ollama_models():
    try:
        data = json_request(f"{OLLAMA_HOST}/api/tags", timeout=5)
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []

# ── Ollama gemma3:latest integration ──────────────────────────────────────────
def _ask_ollama_chat(user_prompt, system_prompt=None, timeout=OLLAMA_TIMEOUT):
    """Fallback: use /api/chat endpoint for models that prefer chat format."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False
    }
    try:
        data = json_request(f"{OLLAMA_HOST}/api/chat", payload, timeout=timeout)
        text = (data.get("message") or {}).get("content", "")
        return text.strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'ignore') if hasattr(e, "read") else str(e)
        raise RuntimeError(f"Ollama HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Ollama at {OLLAMA_HOST}. Start it with: ollama serve") from e
    except TimeoutError as e:
        raise RuntimeError(f"Ollama timed out after {timeout}s while using {OLLAMA_MODEL}.") from e

def ask_ollama(user_prompt, system_prompt=None, timeout=OLLAMA_TIMEOUT):
    generate_payload = {
        "model": OLLAMA_MODEL,
        "prompt": user_prompt,
        "stream": False
    }
    if system_prompt:
        generate_payload["system"] = system_prompt

    try:
        data = json_request(f"{OLLAMA_HOST}/api/generate", generate_payload, timeout=timeout)
        text = data.get("response", "")
        if text:
            return text.strip()
        # Generate returned empty — fall through to chat endpoint
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'ignore') if hasattr(e, "read") else str(e)
        raise RuntimeError(f"Ollama HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Ollama at {OLLAMA_HOST}. Start it with: ollama serve") from e
    except TimeoutError as e:
        raise RuntimeError(f"Ollama timed out after {timeout}s while using {OLLAMA_MODEL}.") from e

    # Fallback for models/adapters that prefer chat format.
    return _ask_ollama_chat(user_prompt, system_prompt, timeout)

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {"conversations": [], "facts": [], "whatsapp_history": []}

def save_memory(mem):
    try:
        with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(mem, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print('Memory save error:', e)

def load_settings():
    settings = DEFAULT_SETTINGS.copy()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                settings.update({k: v for k, v in saved.items() if k in settings})
        except Exception as e:
            print('Settings load error:', e)
    return settings

def save_settings(settings):
    safe = DEFAULT_SETTINGS.copy()
    safe.update({k: v for k, v in settings.items() if k in safe})
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(safe, f, indent=2)
    except Exception as e:
        print('Settings save error:', e)
        raise
    return safe

def public_settings(settings=None):
    settings = settings or load_settings()
    return {
        "ai_provider": settings.get("ai_provider", "groq"),
        "groq_model": settings.get("groq_model", DEFAULT_SETTINGS["groq_model"]),
        "groq_configured": bool(settings.get("groq_api_key")),
        "ollama_model": settings.get("ollama_model", DEFAULT_SETTINGS["ollama_model"]),
        "voice_rate": int(settings.get("voice_rate", DEFAULT_SETTINGS["voice_rate"])),
        "voice_pitch": int(settings.get("voice_pitch", 0)),
        "voice_lang": settings.get("voice_lang", "auto"),
        "tts_engine": "edge-neural" if EDGE_TTS_OK else "windows-sapi"
    }

def ask_groq(user_prompt, system_prompt=None, timeout=60):
    settings = load_settings()
    api_key = settings.get("groq_api_key", "").strip()
    if not api_key:
        raise RuntimeError("Groq API key is not set. Open Settings and save your key.")
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    payload = {
        "model": settings.get("groq_model") or DEFAULT_SETTINGS["groq_model"],
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": 1200
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode('utf-8', 'ignore'))
        return (body.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'ignore') if hasattr(e, "read") else str(e)
        raise RuntimeError(f"Groq HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Groq online API: {e}") from e

def ask_ai(user_prompt, system_prompt=None):
    """Try Groq first (fast online), fall back to Ollama local if Groq fails."""
    settings = load_settings()
    provider = settings.get("ai_provider", "groq")
    if provider == "ollama":
        try:
            return ask_ollama(user_prompt, system_prompt)
        except Exception as e:
            # Fallback to Groq if Ollama fails
            print(f"[AI] Ollama failed, trying Groq backup: {e}")
            try:
                return ask_groq(user_prompt, system_prompt)
            except Exception as e2:
                raise RuntimeError(f"Both Ollama and Groq failed. Ollama: {e}. Groq: {e2}")
    else:
        try:
            return ask_groq(user_prompt, system_prompt)
        except Exception as e:
            # Fallback to Ollama if Groq fails
            print(f"[AI] Groq failed, trying Ollama qwen2.5 backup: {e}")
            try:
                return ask_ollama(user_prompt, system_prompt)
            except Exception as e2:
                raise RuntimeError(f"Both Groq and Ollama failed. Groq: {e}. Ollama: {e2}")

def clean_target(target):
    target = (target or "").strip()
    target = re.sub(r"^https?://", "", target, flags=re.I).split("/")[0].split(":")[0]
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", target):
        raise ValueError("Use a simple domain or IP address only.")
    return target

class SignalHandler(QObject):
    mode_signal = pyqtSignal(str)
    voice_command_signal = pyqtSignal(str)
    exit_signal = pyqtSignal()
    active_listening_signal = pyqtSignal(bool)

signals = SignalHandler()

BLOCKED_COMMANDS = ['format', 'del', 'erase', 'rmdir', 'shutdown', 'restart']

@app_flask.route('/')
def index():
    return app_flask.send_static_file('index.html')

@app_flask.route('/api/cmd', methods=['POST'])
def run_cmd():
    cmd = request.json.get('command', '').strip()
    if not cmd: return jsonify({"error": "No command"})
    if any(cmd.lower().startswith(b) for b in BLOCKED_COMMANDS):
        return jsonify({"error": "Blocked for safety."})
    try:
        if "start " in cmd.lower() or "taskkill" in cmd.lower():
            subprocess.Popen(cmd, shell=True)
            return jsonify({"output": "Command executed."})
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        out = res.stdout if res.stdout else res.stderr
        return jsonify({"output": out[:1000] if out else "Success."})
    except Exception as e: return jsonify({"error": str(e)})

@app_flask.route('/api/scan-url')
def scan_url():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "Missing url."}), 400
    if not url.startswith('http'): url = 'https://' + url
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as r:
            return jsonify({"status": r.status, "content": r.read().decode('utf-8', 'ignore')[:500]})
    except Exception as e: return jsonify({"error": str(e)})

def user_path(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)

def file_item(path):
    try:
        stat = os.stat(path)
        return {
            "name": os.path.basename(path),
            "path": path,
            "folder": os.path.dirname(path),
            "size": stat.st_size,
            "modified": stat.st_mtime
        }
    except OSError:
        return None

@app_flask.route('/api/files/quick')
def quick_files():
    roots = [
        ("Workspace", BASE_DIR),
        ("Downloads", user_path("Downloads")),
        ("Desktop", user_path("Desktop")),
        ("Documents", user_path("Documents")),
    ]
    shortcuts = [{"name": label, "path": path, "kind": "folder"} for label, path in roots if os.path.isdir(path)]
    files = []
    patterns = ["*.txt", "*.md", "*.pdf", "*.docx", "*.xlsx", "*.pptx", "*.py", "*.js", "*.html", "*.json", "*.png", "*.jpg", "*.jpeg"]
    for _label, root in roots:
        if not os.path.isdir(root):
            continue
        for pattern in patterns:
            for path in glob.glob(os.path.join(root, pattern)):
                item = file_item(path)
                if item:
                    item["kind"] = "file"
                    files.append(item)
    files.sort(key=lambda item: item["modified"], reverse=True)
    return jsonify({"shortcuts": shortcuts, "files": files[:24]})

@app_flask.route('/api/files/open', methods=['POST'])
def open_file_api():
    data = request.get_json(silent=True) or {}
    path = os.path.abspath(data.get("path", ""))
    allowed_roots = [os.path.abspath(BASE_DIR), os.path.abspath(user_path("Downloads")), os.path.abspath(user_path("Desktop")), os.path.abspath(user_path("Documents"))]
    if not any(path == root or path.startswith(root + os.sep) for root in allowed_roots):
        return jsonify({"error": "Path is outside quick-access folders."}), 403
    if not os.path.exists(path):
        return jsonify({"error": "File or folder not found."}), 404
    try:
        os.startfile(path)
        return jsonify({"ok": True, "response": f"Opening {os.path.basename(path) or path}."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def html_to_text(html):
    html = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;?", " ", html)
    html = re.sub(r"&amp;?", "&", html)
    html = re.sub(r"&lt;?", "<", html)
    html = re.sub(r"&gt;?", ">", html)
    return re.sub(r"\s+", " ", html).strip()

@app_flask.route('/api/browser', methods=['POST'])
def miles_browser():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    url = (data.get("url") or "").strip()
    if not query and not url:
        return jsonify({"error": "Ask a question or enter a URL."}), 400
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url

    page_text = ""
    source = url
    if url:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as r:
                raw = r.read(350000).decode('utf-8', 'ignore')
            page_text = html_to_text(raw)[:7000]
        except Exception as e:
            page_text = f"Could not load URL: {e}"
    else:
        source = "web search"
        try:
            search_url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
            req = urllib.request.Request(search_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as r:
                raw = r.read(250000).decode('utf-8', 'ignore')
            page_text = html_to_text(raw)[:7000]
        except Exception as e:
            source = "AI knowledge"
            page_text = f"Search unavailable: {e}"

    prompt = (
        "You are Miles Browser inside the MILES HUD. Answer the user's question clearly and concisely. "
        "If page text is provided, use it first. If not enough information is present, say what is missing.\n\n"
        f"Question: {query or 'Summarize this page'}\n"
        f"Source: {source}\n"
        f"Page text:\n{page_text}"
    )
    try:
        answer = ask_ai(prompt)
        return jsonify({"answer": answer, "source": source, "loaded": bool(url and page_text and not page_text.startswith("Could not load"))})
    except Exception as e:
        return jsonify({"error": str(e), "source": source}), 503

@app_flask.route('/api/system_specs')
def system_specs():
    try:
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        disk_path = 'C:\\' if os.name == 'nt' else '/'
        disk = psutil.disk_usage(disk_path)
        return jsonify({
            "cpu_percent": cpu,
            "cpu_cores": psutil.cpu_count(logical=True),
            "mem_total_gb": round(mem.total / (1024**3), 1),
            "mem_used_gb": round(mem.used / (1024**3), 1),
            "mem_percent": mem.percent,
            "disk_total_gb": round(disk.total / (1024**3), 1),
            "disk_used_gb": round(disk.used / (1024**3), 1),
            "disk_percent": disk.percent
        })
    except Exception as e:
        return jsonify({"error": str(e)})

manual_listen_requested = False
manual_listen_event = threading.Event()
voice_request_lock = threading.Lock()
voice_backend_status = {
    "ready": False,
    "listening": False,
    "engine": "starting",
    "last_error": "",
    "last_heard": "",
    "last_wake": ""
}
voice_response_queue = queue.Queue()
speech_lock = threading.Lock()
whatsapp_bridge_process = None
whatsapp_bridge_state = {"status": "offline", "last_error": ""}
current_mode = "full"

# --- Continuous Conversation Mode ---
continuous_mode_active = False
continuous_mode_lock = threading.Lock()
continuous_response_queue = queue.Queue()

@app_flask.route('/api/notepad', methods=['POST'])
def write_notepad():
    try:
        data = request.get_json(silent=True) or {}
        content = data.get('content', '')
        title = data.get('title', 'miles_note').strip() or 'miles_note'
        safe_title = ''.join(c for c in title if c.isalnum() or c in (' ', '_', '-')).strip() or 'miles_note'
        filename = os.path.join(BASE_DIR, f"{safe_title[:40].replace(' ', '_')}.txt")
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        subprocess.Popen(['notepad.exe', filename])
        return jsonify({"response": f"Opened Notepad with {os.path.basename(filename)}.", "file": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app_flask.route('/api/whatsapp/bridge', methods=['GET', 'POST'])
def whatsapp_bridge_control():
    global whatsapp_bridge_process
    running = whatsapp_bridge_process is not None and whatsapp_bridge_process.poll() is None

    if request.method == 'GET':
        # Auto-update status if process exited
        if whatsapp_bridge_process is not None and whatsapp_bridge_process.poll() is not None:
            whatsapp_bridge_state["status"] = "stopped"
            whatsapp_bridge_process = None
            running = False
        return jsonify({"running": running, **whatsapp_bridge_state})

    data = request.get_json(silent=True) or {}
    action = data.get('action', 'start')

    try:
        if action == 'stop':
            if running:
                if os.name == 'nt':
                    # Kill the node process tree
                    subprocess.run(
                        ['taskkill', '/PID', str(whatsapp_bridge_process.pid), '/T', '/F'],
                        capture_output=True, text=True, timeout=10
                    )
                else:
                    whatsapp_bridge_process.terminate()
                whatsapp_bridge_process = None
            whatsapp_bridge_state.update({"status": "stopped", "last_error": ""})
            return jsonify({"running": False, "response": "WhatsApp bridge stopped.", **whatsapp_bridge_state})

        if running:
            return jsonify({"running": True, "response": "WhatsApp bridge is already running.", **whatsapp_bridge_state})

        script = os.path.join(BASE_DIR, 'whatsapp_bridge.js')
        if not os.path.exists(script):
            return jsonify({"error": "whatsapp_bridge.js was not found.", "running": False}), 404
        if not os.path.exists(os.path.join(BASE_DIR, 'node_modules', 'whatsapp-web.js')):
            return jsonify({"error": "Node dependencies are missing. Run: npm install", "running": False}), 500

        # Start node directly (NOT via cmd.exe /k) so we track the real node PID
        # CREATE_NEW_CONSOLE opens a visible terminal for QR code display
        creationflags = 0
        if os.name == 'nt':
            creationflags = subprocess.CREATE_NEW_CONSOLE
            whatsapp_bridge_process = subprocess.Popen(
                ['node', 'whatsapp_bridge.js'],
                cwd=BASE_DIR,
                creationflags=creationflags
            )
        else:
            whatsapp_bridge_process = subprocess.Popen(
                ['node', script], cwd=BASE_DIR
            )

        whatsapp_bridge_state.update({"status": "starting", "last_error": ""})

        # Background health monitor — updates state when node exits
        def _monitor():
            whatsapp_bridge_process.wait()
            if whatsapp_bridge_state.get("status") not in ("stopped", "auth_failure"):
                whatsapp_bridge_state["status"] = "stopped"
            print("[WhatsApp] Bridge process exited.")
        threading.Thread(target=_monitor, daemon=True).start()

        return jsonify({
            "running": True,
            "response": "WhatsApp bridge started. A console window will appear — scan the QR code with WhatsApp.",
            **whatsapp_bridge_state
        })
    except FileNotFoundError:
        err = "node.js not found. Install Node.js from nodejs.org"
        whatsapp_bridge_state.update({"status": "error", "last_error": err})
        return jsonify({"error": err, "running": False, **whatsapp_bridge_state}), 500
    except Exception as e:
        whatsapp_bridge_state.update({"status": "error", "last_error": str(e)})
        return jsonify({"error": str(e), "running": False, **whatsapp_bridge_state}), 500

@app_flask.route('/api/voice', methods=['POST'])
def voice():
    global manual_listen_requested

    if not voice_backend_status.get("ready"):
        detail = voice_backend_status.get("last_error") or "Microphone backend is still starting."
        return jsonify({"error": detail, "status": voice_backend_status}), 503

    if not voice_request_lock.acquire(blocking=False):
        return jsonify({"error": "Microphone is already processing a voice request.", "status": voice_backend_status}), 409
    
    # Clear the queue first to avoid stale responses
    while not voice_response_queue.empty():
        try: voice_response_queue.get_nowait()
        except: pass
        
    manual_listen_requested = True
    manual_listen_event.set()
    voice_backend_status["listening"] = True
    try:
        text = voice_response_queue.get(timeout=18)
        if text.startswith("ERROR:"):
            return jsonify({"error": text[6:]})
            
        # Intercept mode commands directly here
        t_low = text.lower()
        if "set on top" in t_low or "slim" in t_low:
            signals.mode_signal.emit("top")
        elif "hide" in t_low or "minimize" in t_low:
            signals.mode_signal.emit("hide")
        elif "full screen" in t_low or "maximize" in t_low:
            signals.mode_signal.emit("full")
            
        return jsonify({"text": text})
    except queue.Empty:
        manual_listen_requested = False
        manual_listen_event.clear()
        return jsonify({"error": "Voice request timed out. Microphone may be busy."})
    except Exception as e:
        manual_listen_requested = False
        manual_listen_event.clear()
        return jsonify({"error": str(e)})
    finally:
        voice_backend_status["listening"] = False
        voice_request_lock.release()

@app_flask.route('/api/voice/status')
def voice_status():
    return jsonify(voice_backend_status)

HINDI_WORDS = {
    "hai", "hain", "nahi", "nahin", "kya", "kaise", "kaisa", "kaisi", "main",
    "mera", "mere", "meri", "aap", "tum", "kar", "karo", "karen", "bata",
    "bolo", "sun", "suno", "chahiye", "theek", "acha", "accha", "haan",
    "ji", "kaam", "abhi", "kyun", "kyu", "kidhar", "idhar", "udhar",
    "hoon", "hu", "hun", "ho", "tha", "thi", "thik", "thoda", "bahut",
    "bhi", "aur", "yaar", "bhai", "kr", "karta", "karti", "karte",
    "raha", "rahi", "rahe", "mujhe", "tujhe", "hum", "ham", "apna",
    "apni", "apne", "ye", "yeh", "wo", "woh", "matlab", "samjha",
    "samjho", "bol", "bolna", "sunna", "chalo", "jaldi", "ruk", "ruko"
}

HINGLISH_TTS_REPLACEMENTS = [
    (r"\bmain\b", "मैं"), (r"\bmai\b", "मैं"), (r"\bmein\b", "मैं"),
    (r"\bhoon\b|\bhu\b|\bhun\b", "हूं"), (r"\bhai\b", "है"), (r"\bhain\b", "हैं"),
    (r"\bkya\b", "क्या"), (r"\bkaise\b", "कैसे"), (r"\bkaisa\b", "कैसा"),
    (r"\bkaisi\b", "कैसी"), (r"\bnahi\b|\bnahin\b", "नहीं"),
    (r"\baap\b", "आप"), (r"\btum\b", "तुम"), (r"\bmujhe\b", "मुझे"),
    (r"\bmera\b", "मेरा"), (r"\bmere\b", "मेरे"), (r"\bmeri\b", "मेरी"),
    (r"\bkar\b|\bkr\b", "कर"), (r"\bkaro\b", "करो"), (r"\bkaren\b", "करें"),
    (r"\bbata\b", "बता"), (r"\bbatao\b", "बताओ"), (r"\bbolo\b|\bbol\b", "बोलो"),
    (r"\bsuno\b|\bsun\b", "सुनो"), (r"\bchahiye\b", "चाहिए"),
    (r"\btheek\b|\bthik\b", "ठीक"), (r"\bacha\b|\baccha\b", "अच्छा"),
    (r"\bhaan\b", "हाँ"), (r"\bji\b", "जी"), (r"\bkaam\b", "काम"),
    (r"\babhi\b", "अभी"), (r"\bkyun\b|\bkyu\b", "क्यों"),
    (r"\bidhar\b", "इधर"), (r"\budhar\b", "उधर"), (r"\bbahut\b", "बहुत"),
    (r"\bthoda\b", "थोड़ा"), (r"\baur\b", "और"), (r"\bbhi\b", "भी"),
    (r"\bchalo\b", "चलो"), (r"\bjaldi\b", "जल्दी"), (r"\bsamjha\b", "समझा"),
    (r"\bsamjho\b", "समझो"), (r"\byaar\b", "यार"), (r"\bbhai\b", "भाई")
]

def has_devanagari(text):
    return bool(re.search(r"[\u0900-\u097f]", str(text or "")))

def is_hindiish_text(text, lang_value="auto"):
    clean = str(text or "")
    requested = (lang_value or "auto").lower()
    words = set(re.findall(r"[a-zA-Z]+", clean.lower()))
    return requested == "hi" or has_devanagari(clean) or (requested == "auto" and len(words & HINDI_WORDS) >= 1)

def choose_voice(text, lang_value="auto"):
    if is_hindiish_text(text, lang_value):
        return "hi-IN-MadhurNeural", "hi-IN"
    return "en-IN-PrabhatNeural", "en-IN"

def prepare_text_for_tts(text, detected_lang, lang_value="auto"):
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if detected_lang != "hi-IN" or has_devanagari(clean):
        return clean

    if GoogleTranslator:
        try:
            translated = GoogleTranslator(source='auto', target='hi').translate(clean)
            if translated and has_devanagari(translated):
                return translated
        except Exception as e:
            print(f"Hindi TTS translation fallback: {e}")

    converted = clean
    for pattern, replacement in HINGLISH_TTS_REPLACEMENTS:
        converted = re.sub(pattern, replacement, converted, flags=re.I)
    return converted

def play_mp3_with_windows_player(filename, estimated_seconds):
    ps_path = filename.replace("'", "''")
    wait_ms = int(max(1800, min(45000, estimated_seconds * 1000)))
    script = (
        "Add-Type -AssemblyName PresentationCore; "
        "$p = New-Object System.Windows.Media.MediaPlayer; "
        f"$p.Open([Uri]'{ps_path}'); "
        "$p.Volume = 1; "
        "$p.Play(); "
        f"Start-Sleep -Milliseconds {wait_ms}; "
        "$p.Stop(); "
        "$p.Close();"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=max(10, int(wait_ms / 1000) + 5)
    )

def speak_with_edge_neural(text, rate_value=100, pitch_value=0, lang_value="auto"):
    if not EDGE_TTS_OK:
        raise RuntimeError("edge_tts is not installed")
    clean_text = re.sub(r"\s+", " ", str(text)).strip()[:1200]
    if not clean_text:
        return
    voice_name, detected_lang = choose_voice(clean_text, lang_value)
    tts_text = prepare_text_for_tts(clean_text, detected_lang, lang_value)
    edge_rate = f"{max(70, min(170, int(rate_value))) - 100:+d}%"
    edge_pitch = f"{max(-30, min(30, int(pitch_value))):+d}Hz"
    filename = os.path.join(BASE_DIR, f"voice_{uuid.uuid4().hex}.mp3")

    async def generate_tts():
        communicate = edge_tts.Communicate(tts_text, voice_name, rate=edge_rate, pitch=edge_pitch)
        await communicate.save(filename)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(generate_tts())
        estimated_seconds = 1.0 + (len(tts_text.split()) / 2.35)
        play_mp3_with_windows_player(filename, estimated_seconds)
    finally:
        loop.close()
        try:
            if os.path.exists(filename):
                os.remove(filename)
        except Exception:
            pass

def speak_with_windows_sapi(text, rate_value=100):
    clean_text = re.sub(r"\s+", " ", str(text)).strip()[:900]
    if not clean_text:
        return
    sapi_rate = max(-10, min(10, int(round((int(rate_value) - 100) / 7))))
    encoded_text = base64.b64encode(clean_text.encode("utf-8")).decode("ascii")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Volume = 100; "
        f"$s.Rate = {sapi_rate}; "
        f"$bytes = [Convert]::FromBase64String('{encoded_text}'); "
        "$text = [Text.Encoding]::UTF8.GetString($bytes); "
        "$s.Speak($text); "
        "$s.Dispose();"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=45
    )

def system_speak_async(text, rate_value=100, pitch_value=0, lang_value="auto"):
    """Speak with neural Edge TTS when available, falling back to Windows SAPI."""
    if not text:
        return

    def worker():
        try:
            with speech_lock:
                try:
                    speak_with_edge_neural(text, rate_value, pitch_value, lang_value)
                except Exception as edge_error:
                    print(f"Neural speech unavailable, using Windows voice: {edge_error}")
                    speak_with_windows_sapi(text, rate_value)
        except Exception as e:
            print(f"System speech error: {e}")

    threading.Thread(target=worker, daemon=True).start()

@app_flask.route('/api/say', methods=['POST'])
def say_api():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    settings = load_settings()
    try:
        rate_value = max(70, min(170, int(data.get("rate", settings.get("voice_rate", 100)))))
        pitch_value = max(-30, min(30, int(data.get("pitch", settings.get("voice_pitch", 0)))))
    except (TypeError, ValueError):
        rate_value, pitch_value = 100, 0
    lang_value = data.get("lang") or settings.get("voice_lang", "auto")
    system_speak_async(text, rate_value, pitch_value, lang_value)
    voice_name, detected_lang = choose_voice(text, lang_value)
    return jsonify({"ok": True, "engine": "edge-neural" if EDGE_TTS_OK else "windows-sapi", "voice": voice_name, "lang": detected_lang})

@app_flask.route('/api/tts')
def tts():
    import uuid
    import asyncio
    import os
    text = request.args.get('text', 'Hello')
    rate = request.args.get('rate')
    pitch = request.args.get('pitch')
    lang = request.args.get('lang')
    settings = load_settings()
    try:
        rate_value = int(rate) if rate is not None else int(settings.get("voice_rate", 100))
        pitch_value = int(pitch) if pitch is not None else int(settings.get("voice_pitch", 0))
    except ValueError:
        rate_value, pitch_value = 100, 0
    lang_value = lang if lang else settings.get("voice_lang", "auto")
    
    rate_value = max(70, min(170, rate_value))
    pitch_value = max(-30, min(30, pitch_value))
    edge_rate = f"{rate_value - 100:+d}%"
    edge_pitch = f"{pitch_value:+d}Hz"
    
    voice_name, _detected_lang = choose_voice(text, lang_value)
    
    filename = os.path.join(BASE_DIR, f"voice_{uuid.uuid4().hex}.mp3")
    try:
        if not EDGE_TTS_OK:
            raise RuntimeError("edge_tts is not installed")

        async def generate_tts():
            communicate = edge_tts.Communicate(text, voice_name, rate=edge_rate, pitch=edge_pitch)
            await communicate.save(filename)
            
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(generate_tts())
        finally:
            loop.close()
        
        import io
        with open(filename, 'rb') as f:
            audio_data = f.read()
            
        try:
            os.remove(filename)
        except:
            pass
            
        return send_file(
            io.BytesIO(audio_data), 
            mimetype='audio/mpeg', 
            as_attachment=False, 
            download_name='voice.mp3'
        )
    except Exception as e:
        print(f"TTS Error: {e}. Falling back to system speaker.")
        system_speak_async(text, rate_value, pitch_value, lang_value)
        return jsonify({"spoken": True, "fallback": "windows-sapi", "error": str(e)})

@app_flask.route('/api/settings', methods=['GET', 'POST'])
def settings_api():
    if request.method == 'GET':
        return jsonify(public_settings())
    try:
        data = request.get_json(silent=True) or {}
        settings = load_settings()
        if data.get("groq_api_key"):
            settings["groq_api_key"] = str(data.get("groq_api_key")).strip()
        if data.get("groq_model"):
            settings["groq_model"] = str(data.get("groq_model")).strip()
        if data.get("ollama_model"):
            settings["ollama_model"] = str(data.get("ollama_model")).strip()
            # Update live OLLAMA_MODEL too
            global OLLAMA_MODEL
            OLLAMA_MODEL = settings["ollama_model"]
        if data.get("ai_provider") in ("groq", "ollama"):
            settings["ai_provider"] = data.get("ai_provider")
        if data.get("voice_lang") in ("auto", "en", "hi"):
            settings["voice_lang"] = data.get("voice_lang")
        if "voice_rate" in data:
            settings["voice_rate"] = max(70, min(170, int(data.get("voice_rate"))))
        if "voice_pitch" in data:
            settings["voice_pitch"] = max(-30, min(30, int(data.get("voice_pitch"))))
        settings = save_settings(settings)
        return jsonify(public_settings(settings))
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app_flask.route('/api/voice/continuous', methods=['POST'])
def voice_continuous():
    """Poll for voice input in continuous conversation mode."""
    global continuous_mode_active
    if not voice_backend_status.get("ready"):
        return jsonify({"error": "Voice backend not ready."}), 503
    if not voice_backend_status.get("listening"):
        # Signal the background thread to record one phrase
        while not voice_response_queue.empty():
            try: voice_response_queue.get_nowait()
            except: pass
        if not voice_request_lock.acquire(blocking=False):
            return jsonify({"busy": True})
        global manual_listen_requested
        manual_listen_requested = True
        manual_listen_event.set()
        voice_backend_status["listening"] = True
        try:
            text = voice_response_queue.get(timeout=15)
            if text.startswith("ERROR:"):
                return jsonify({"error": text[6:]})
            return jsonify({"text": text})
        except queue.Empty:
            manual_listen_event.clear()
            return jsonify({"error": "timeout"})
        finally:
            voice_backend_status["listening"] = False
            voice_request_lock.release()
    return jsonify({"busy": True})

@app_flask.route('/api/mode', methods=['POST'])
def set_mode():
    mode = request.json.get('mode', 'full')
    signals.mode_signal.emit(mode)
    return jsonify({"success": True})

@app_flask.route('/api/scan-screen', methods=['POST'])
def scan_screen():
    try:
        import io
        import base64
        try:
            from PIL import ImageGrab
        except ImportError:
            return jsonify({"error": "Pillow library is missing. Please run: pip install pillow"}), 500

        data = request.get_json(silent=True) or {}
        question = data.get('question', "What is on this screen? Describe it briefly.")
        
        # Take screenshot
        img = ImageGrab.grab()
        # Resize to save bandwidth
        img.thumbnail((1280, 720))
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=80)
        img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        # Use Groq Vision API
        settings = load_settings()
        api_key = settings.get("groq_api_key", "").strip()
        if not api_key:
            return jsonify({"error": "Groq API key is required for Vision scanning. Add it in settings."}), 400
            
        payload = {
            "model": "meta-llama/llama-4-scout-17b-16e-instruct",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_str}"}}
                    ]
                }
            ],
            "temperature": 0.5,
            "max_tokens": 500
        }
        req_data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=req_data,
            headers={
                "Content-Type": "application/json", 
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            body = json.loads(response.read().decode('utf-8', 'ignore'))
            text = body.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            
        return jsonify({"text": text})
    except urllib.error.URLError as e:
        return jsonify({"error": "Failed to connect to Groq API."}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app_flask.route('/api/chat', methods=['POST'])
def chat_proxy():
    try:
        data = request.get_json(silent=True) or {}
        question = data.get('question')
        if not question:
            return jsonify({"error": "Missing question."})
            
        system_prompt = (
            "You are MILES (Multipurpose Intelligent Linked Engine System), Pawan's young superhero-style AI companion. "
            "Your vibe is natural, clever, friendly, energetic, and lightly witty, like a brilliant teenage tech hero. "
            "You speak in a sharp, witty, confident tone — like JARVIS but more edgy. "
            "The user prefers Hinglish (Hindi-English mix) — respond naturally in Hinglish when appropriate. "
            "Do not imitate or claim to be any real actor or copyrighted character. "
            "You have strong technical knowledge in cybersecurity, code, and system management. "
            "Keep replies concise, warm, and practical. For code requests, return full working code. "
            "For system actions, provide the exact Windows CMD or PowerShell command needed."
        )
        settings = load_settings()
        text = ask_ai(question, system_prompt)
        model = settings.get("groq_model") if settings.get("ai_provider") == "groq" else OLLAMA_MODEL
        return jsonify({"text": text, "model": model, "provider": settings.get("ai_provider")})
    except Exception as e:
        err_msg = str(e)
        print("AI Backend Error:", err_msg)
        return jsonify({"error": err_msg}), 503

@app_flask.route('/api/ai/status')
def ai_status():
    settings = load_settings()
    if settings.get("ai_provider") == "groq":
        configured = bool(settings.get("groq_api_key"))
        return jsonify({
            "provider": "groq",
            "host": "https://api.groq.com",
            "model": settings.get("groq_model"),
            "online": configured,
            "model_installed": configured,
            "status": "configured" if configured else "missing Groq API key"
        })
    models = get_ollama_models()
    return jsonify({
        "provider": "ollama",
        "host": OLLAMA_HOST,
        "model": OLLAMA_MODEL,
        "online": bool(models),
        "model_installed": OLLAMA_MODEL in models,
        "installed_models": models,
        "status": "ready" if OLLAMA_MODEL in models else "model not installed"
    })

@app_flask.route('/api/cyber', methods=['POST'])
def cyber_task():
    try:
        data = request.get_json(silent=True) or {}
        task = (data.get("task") or "").lower().strip()
        target = clean_target(data.get("target"))
        if task == "dns":
            lines = [f"DNS lookup for {target}"]
            try:
                infos = socket.getaddrinfo(target, None)
                addrs = sorted({item[4][0] for item in infos})
                lines.extend(addrs or ["No records found."])
            except Exception as e:
                lines.append(str(e))
            return jsonify({"output": "\n".join(lines)})
        if task == "ping":
            cmd = ["ping", "-n", "4", target] if os.name == "nt" else ["ping", "-c", "4", target]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            return jsonify({"output": (res.stdout or res.stderr)[:3000]})
        if task == "trace":
            cmd = ["tracert", target] if os.name == "nt" else ["traceroute", target]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            return jsonify({"output": (res.stdout or res.stderr)[:3000]})
        if task == "headers":
            url = target if target.startswith("http") else "https://" + target
            req = urllib.request.Request(url, method="HEAD", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as r:
                headers = [f"HTTP {r.status} {r.reason}"]
                headers.extend(f"{k}: {v}" for k, v in r.headers.items())
            return jsonify({"output": "\n".join(headers)[:3000]})
        return jsonify({"error": "Choose dns, ping, trace, or headers."}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app_flask.route('/api/whatsapp', methods=['POST'])
def whatsapp_chat():
    """Handles incoming WhatsApp messages from the whatsapp_bridge.js node script."""
    try:
        data = request.get_json(silent=True) or {}
        message = data.get('message', '').strip()
        sender = data.get('sender', 'whatsapp_user')
        if not message:
            return jsonify({"reply": "No message received."})

        # Load memory and build context
        mem = load_memory()
        history = mem.get('whatsapp_history', [])[-10:]  # Last 10 messages for context
        context = '\n'.join([f"{h['role']}: {h['text']}" for h in history])

        system_prompt = (
            "You are MILES (Multipurpose Intelligent Linked Engine System), Pawan's young superhero-style AI companion. "
            "You are clever, friendly, energetic, helpful, and lightly witty, without imitating any specific actor or character. "
            "The user is messaging you via WhatsApp. Keep replies short, usually 2-3 sentences max unless detailed info is needed."
        )
        previous_context = f"Previous context:\n{context}\n" if context else ""
        user_prompt = f"{previous_context}User: {message}"
        reply = ask_ai(user_prompt, system_prompt)

        # Save to memory
        mem['whatsapp_history'].append({'role': 'User', 'text': message})
        mem['whatsapp_history'].append({'role': 'MILES', 'text': reply})
        # Keep history manageable
        mem['whatsapp_history'] = mem['whatsapp_history'][-100:]
        save_memory(mem)

        return jsonify({"reply": reply})
    except Exception as e:
        print('WhatsApp chat error:', e)
        return jsonify({"reply": "I'm having trouble connecting to the configured AI provider. Check Settings or your network."}), 503

@app_flask.route('/api/whatsapp/status', methods=['POST'])
def whatsapp_status():
    """Receives status updates from the WhatsApp bridge."""
    data = request.get_json(silent=True) or {}
    status = data.get('status', 'unknown')
    whatsapp_bridge_state["status"] = status
    whatsapp_bridge_state["last_error"] = data.get('error', '')
    print(f'[WhatsApp Bridge] Status: {status}')
    return jsonify({"ok": True})

@app_flask.route('/api/memory', methods=['GET'])
def get_memory():
    """Returns the current memory for the HUD to display."""
    return jsonify(load_memory())

@app_flask.route('/api/exit', methods=['POST'])
def exit_app():
    signals.exit_signal.emit()
    return jsonify({"success": True})

@app_flask.route('/api/jarvis_cmd', methods=['POST'])
def jarvis_cmd():
    try:
        data = request.get_json(silent=True) or {}
        command = data.get('command', '').lower()
        if not command:
            return jsonify({"error": "Empty command"}), 400
            
        import re
        
        if "translate" in command:
            match = re.search(r"translate (.+) in (\w+)", command)
            if match:
                word = match.group(1).strip()
                language = match.group(2).strip()
                lang_map = {
                    "hindi": "hi", "english": "en", "spanish": "es", "french": "fr",
                    "german": "de", "chinese": "zh-cn", "japanese": "ja", "korean": "ko",
                    "italian": "it", "bengali": "bn", "punjabi": "pa", "urdu": "ur",
                    "tamil": "ta", "telugu": "te", "bhojpuri": "hi"
                }
                dest_lang = lang_map.get(language, "en")
                translated = GoogleTranslator(source='auto', target=dest_lang).translate(word)
                return jsonify({"response": f"The translation of '{word}' in {language} is: {translated}"})
            return jsonify({"response": "Please specify the word and the target language clearly."})
            
        elif "ip address" in command:
            req = urllib.request.Request('https://api.ipify.org')
            with urllib.request.urlopen(req) as r:
                ip = r.read().decode('utf-8')
            return jsonify({"response": f"Your public IP address is {ip}."})
            
        elif "find my location" in command or "where am i" in command:
            req = urllib.request.Request('https://api.ipify.org')
            with urllib.request.urlopen(req) as r:
                ip = r.read().decode('utf-8')
            req2 = urllib.request.Request(f"https://ipinfo.io/{ip}/json")
            with urllib.request.urlopen(req2) as r2:
                loc_data = json.loads(r2.read().decode('utf-8'))
                city = loc_data.get("city", "Unknown City")
                region = loc_data.get("region", "Unknown Region")
                country = loc_data.get("country", "Unknown Country")
            return jsonify({"response": f"You are in {city}, {region}, {country}."})
            
        elif "show nearby" in command:
            place = command.replace("show nearby", "").strip()
            import webbrowser
            webbrowser.open(f"https://www.google.com/maps/search/{place}+near+me")
            return jsonify({"response": "Showing nearby on Google Maps."})
            
        elif "take a photo" in command or "take a picture" in command:
            import cv2
            cap = cv2.VideoCapture(0)
            ret, frame = cap.read()
            if ret:
                filename = datetime.datetime.now().strftime("photo_%Y%m%d_%H%M%S.jpg")
                cv2.imwrite(filename, frame)
                cap.release()
                return jsonify({"response": f"Photo captured and saved as {filename}."})
            cap.release()
            return jsonify({"error": "Unable to capture photo."})
            
        elif "battery" in command:
            battery = psutil.sensors_battery()
            percent = battery.percent if battery else 'N/A'
            return jsonify({"response": f"Battery is at {percent} percent."})
            
        elif "set volume at" in command or "set volume to" in command:
            volume_level = int(''.join(filter(str.isdigit, command)))
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            volume.SetMasterVolumeLevelScalar(volume_level / 100, None)
            return jsonify({"response": f"Volume set to {volume_level} percent."})
            
        elif "set brightness at" in command or "set brightness to" in command:
            brightness_level = int(''.join(filter(str.isdigit, command)))
            import wmi
            wmi_interface = wmi.WMI(namespace='wmi')
            methods = wmi_interface.WmiMonitorBrightnessMethods()[0]
            methods.WmiSetBrightness(brightness_level, 0)
            return jsonify({"response": f"Brightness set to {brightness_level} percent."})
            
        elif "shutdown" in command:
            os.system("shutdown /s /t 1")
            return jsonify({"response": "Shutting down now."})
        elif "restart" in command:
            os.system("shutdown /r /t 1")
            return jsonify({"response": "Restarting now."})
        elif "sleep" in command:
            os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
            return jsonify({"response": "Sleeping now."})
        elif "lock" in command:
            os.system("rundll32.exe user32.dll,LockWorkStation")
            return jsonify({"response": "Locking the system."})
            
        elif "wikipedia" in command:
            topic = command.replace("wikipedia", "").strip()
            summary = wikipedia.summary(topic, sentences=2)
            return jsonify({"response": summary})
            
        elif "play music" in command and "next" not in command:
            pyautogui.press("playpause")
            return jsonify({"response": "Toggling play/pause."})
        elif "stop music" in command:
            pyautogui.press("playpause")
            return jsonify({"response": "Music stopped."})
        elif "next song" in command:
            pyautogui.press("nexttrack")
            return jsonify({"response": "Playing next song."})
        elif "previous song" in command:
            pyautogui.press("previoustrack")
            return jsonify({"response": "Playing previous song."})
            
        elif "mute" in command and "laptop" in command:
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            volume.SetMute(1, None)
            return jsonify({"response": "Volume muted."})
        elif "unmute" in command:
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            volume.SetMute(0, None)
            return jsonify({"response": "Volume unmuted."})
            
        elif "take a screenshot" in command:
            filename = datetime.datetime.now().strftime("screenshot_%Y%m%d_%H%M%S.png")
            pyautogui.screenshot(filename)
            return jsonify({"response": f"Screenshot saved as {filename}."})
            
        elif "recent screenshot" in command:
            screenshot_folder = os.getcwd()
            files = glob.glob(os.path.join(screenshot_folder, "screenshot_*.png"))
            if files:
                recent = max(files, key=os.path.getctime)
                os.startfile(recent)
                return jsonify({"response": "Opening your most recent screenshot."})
            return jsonify({"response": "No screenshots found."})
            
        elif "recent photo" in command or "recent picture" in command:
            photo_folder = os.getcwd()
            files = glob.glob(os.path.join(photo_folder, "photo_*.jpg"))
            if files:
                recent = max(files, key=os.path.getctime)
                os.startfile(recent)
                return jsonify({"response": "Opening your most recent photo."})
            return jsonify({"response": "No photo found."})
            
        elif "check network" in command or "check internet" in command or "speed" in command:
            import speedtest
            st = speedtest.Speedtest()
            st.get_best_server()
            download_speed = st.download() / 1_000_000
            upload_speed = st.upload() / 1_000_000
            return jsonify({"response": f"Download speed is {download_speed:.2f} Mbps and upload speed is {upload_speed:.2f} Mbps."})
            
        elif "turn on bluetooth" in command:
            subprocess.run('PowerShell -Command "Start-Service bthserv"', shell=True)
            return jsonify({"response": "Bluetooth service started."})
        elif "turn off bluetooth" in command:
            subprocess.run('PowerShell -Command "Stop-Service bthserv"', shell=True)
            return jsonify({"response": "Bluetooth service stopped."})
            
        elif "flip a coin" in command:
            return jsonify({"response": "Heads." if random.choice([True, False]) else "Tails."})
        elif "roll a dice" in command:
            return jsonify({"response": f"You rolled a {random.randint(1, 6)}."})

        return jsonify({"success": True})
    except Exception as e:
        print("Jarvis CMD Error:", e)
        return jsonify({"error": str(e)})

def run_server():
    app_flask.run(port=7420, use_reloader=False, debug=False, threaded=True)

# --- Voice Backend: sounddevice + Vosk (no PyAudio required) ---
SAMPLE_RATE = 16000
BLOCK_SIZE  = 4000   # ~250ms chunks at 16 kHz

def _record_phrase(max_seconds=7, silence_chunks=12):
    """Record until silence, return raw PCM bytes (int16 LE, mono, 16 kHz)."""
    frames = []
    silent = 0
    threshold = 1000  # RMS silence threshold

    def callback(indata, frame_count, time_info, status):
        nonlocal silent
        pcm = (indata[:, 0] * 32767).astype(np.int16)
        rms = int(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))
        frames.append(pcm.tobytes())
        if rms < threshold:
            silent += 1
        else:
            silent = 0

    max_chunks = int(max_seconds * SAMPLE_RATE / BLOCK_SIZE)
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype='float32', blocksize=BLOCK_SIZE,
                        callback=callback):
        for _ in range(max_chunks):
            time.sleep(BLOCK_SIZE / SAMPLE_RATE)
            if silent >= silence_chunks:
                break
    return b''.join(frames)


def wake_word_listener():
    global manual_listen_requested, current_mode
    voice_backend_status.update({"ready": False, "listening": False, "engine": "starting", "last_error": ""})

    # ── Dependency check ────────────────────────────────────────
    if not SOUNDDEVICE_OK:
        err = "sounddevice not installed. Run: pip install sounddevice numpy"
        print(f"[VOICE] {err}")
        voice_backend_status.update({"ready": False, "engine": "disabled", "last_error": err})
        return  # exit thread cleanly — no spam loop

    if not VOSK_OK:
        err = "vosk not installed. Run: pip install vosk"
        print(f"[VOICE] {err}")
        voice_backend_status.update({"ready": False, "engine": "disabled", "last_error": err})
        return

    # ── Load Vosk model ──────────────────────────────────────────
    model_path = os.path.join(BASE_DIR, "model", "vosk-model-small-en-us-0.15")
    has_vosk = os.path.exists(model_path)
    model = None

    if has_vosk:
        try:
            model = Model(model_path)
            voice_backend_status["engine"] = "vosk-local"
            print("[VOICE] Vosk model loaded.", flush=True)
        except Exception as e:
            print(f"[VOICE] Failed to load Vosk model: {e}")
            has_vosk = False
            voice_backend_status["engine"] = "disabled"
            voice_backend_status["last_error"] = f"Vosk load failed: {e}"
    else:
        print(f"[VOICE] Vosk model not found at {model_path}. Voice recognition disabled.")
        voice_backend_status["engine"] = "disabled"
        voice_backend_status["last_error"] = "Vosk model not found"

    if not has_vosk:
        # Still mark ready so manual mic button can show meaningful error
        voice_backend_status["ready"] = True
        # Keep thread alive to handle manual_listen_event queue flushes
        while True:
            if manual_listen_event.is_set() or manual_listen_requested:
                voice_response_queue.put("ERROR:Vosk model not found. Download from alphacephei.com/vosk/models")
                manual_listen_requested = False
                manual_listen_event.clear()
                voice_backend_status["listening"] = False
            time.sleep(0.5)

    # ── Main audio loop ──────────────────────────────────────────
    wake_rec = KaldiRecognizer(model, SAMPLE_RATE)
    active_listening = False
    active_listening_time = 0
    last_wake_time = 0
    last_emitted_command = ""
    last_emitted_time = 0
    wake_pattern = re.compile(
        r"^(?:hey\s+|ok\s+|okay\s+)?(?P<wake>miles|myles|mile|mines|jarvis|friday|alice)"
        r"(?:\s+ai|\s+please)?(?:\b|$)(?P<cmd>.*)$"
    )
    EXIT_WORDS = {'bye', 'goodbye', 'exit', 'shut down', 'miles bye',
                  'miles goodbye', 'miles exit', 'bye miles', 'goodbye miles','by'}

    print("[VOICE] Microphone active and listening...", flush=True)
    voice_backend_status.update({"ready": True, "last_error": ""})

    AUDIO_Q = queue.Queue()

    def sd_callback(indata, frame_count, time_info, status):
        pcm = (indata[:, 0] * 32767).astype(np.int16)
        AUDIO_Q.put(pcm.tobytes())

    consecutive_errors = 0
    MAX_ERRORS = 5

    while True:
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                dtype='float32', blocksize=BLOCK_SIZE,
                                callback=sd_callback):
                consecutive_errors = 0
                while True:
                    # ── Manual voice request from /api/voice ──
                    if manual_listen_event.is_set() or manual_listen_requested:
                        voice_backend_status["listening"] = True
                        try:
                            # Drain stale chunks from queue
                            while not AUDIO_Q.empty():
                                AUDIO_Q.get_nowait()

                            man_rec = KaldiRecognizer(model, SAMPLE_RATE)
                            pcm_frames = []
                            silent = 0
                            threshold = 600
                            deadline = time.time() + 7  # 7s max

                            while time.time() < deadline:
                                try:
                                    chunk = AUDIO_Q.get(timeout=0.3)
                                except queue.Empty:
                                    continue
                                pcm_frames.append(chunk)
                                arr = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
                                rms = int(np.sqrt(np.mean(arr ** 2)))
                                if rms < threshold:
                                    silent += 1
                                    if silent >= 12 and len(pcm_frames) > 5:
                                        break
                                else:
                                    silent = 0
                                man_rec.AcceptWaveform(chunk)

                            man_rec.AcceptWaveform(b'')
                            result = json.loads(man_rec.FinalResult())
                            text = result.get('text', '').strip()
                            if text:
                                voice_response_queue.put(text)
                            else:
                                voice_response_queue.put("ERROR:Could not understand speech.")
                        except Exception as e:
                            voice_backend_status["last_error"] = str(e)
                            voice_response_queue.put(f"ERROR:{e}")
                        finally:
                            manual_listen_requested = False
                            manual_listen_event.clear()
                            voice_backend_status["listening"] = False
                        continue

                    # ── Wake word chunk processing ──
                    try:
                        chunk = AUDIO_Q.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    # Clap / Loud noise detection
                    if current_mode == "hide":
                        arr = np.frombuffer(chunk, dtype=np.int16).astype(np.int32)
                        peak = np.max(np.abs(arr))
                        if peak > 24000:
                            print(f"[VOICE] Clap/Loud noise detected (Peak: {peak}). Switching to full screen.")
                            signals.mode_signal.emit("full")
                            # Drain queue to prevent processing the clap as speech
                            while not AUDIO_Q.empty():
                                try: AUDIO_Q.get_nowait()
                                except: pass
                            continue

                    is_final = wake_rec.AcceptWaveform(chunk)
                    if is_final:
                        result = json.loads(wake_rec.Result())
                        text = result.get('text', '').strip().lower()
                    else:
                        partial = json.loads(wake_rec.PartialResult())
                        text = partial.get('partial', '').strip().lower()

                    if not text:
                        continue

                    voice_backend_status["last_heard"] = text
                    print(f"[Heard]: {text}", flush=True)

                    # Exit detection
                    if text in EXIT_WORDS:
                        print("[VOICE] Exit command detected.")
                        signals.exit_signal.emit()
                        continue

                    # Active listening mode (waiting for follow-up command)
                    if active_listening:
                        if time.time() - active_listening_time < 10:
                            if not is_final:
                                continue
                            print(f"[VOICE] Command (active mode): {text}")
                            now = time.time()
                            if text != last_emitted_command or now - last_emitted_time > 2.5:
                                signals.voice_command_signal.emit(text)
                                last_emitted_command = text
                                last_emitted_time = now
                            active_listening = False
                            signals.active_listening_signal.emit(False)
                            try:
                                wake_rec.Reset()
                            except Exception:
                                pass
                        else:
                            active_listening = False
                            signals.active_listening_signal.emit(False)
                        continue

                    # Wake word detection
                    wake_match = wake_pattern.match(text)
                    if wake_match:
                        now = time.time()
                        if now - last_wake_time < 2.0:
                            continue
                        last_wake_time = now
                        found_word = wake_match.group("wake")
                        voice_backend_status["last_wake"] = found_word
                        print(f"[VOICE] Wake word '{found_word}' detected in: '{text}'")
                        cmd = wake_match.group("cmd").strip()
                        if cmd:
                            if cmd != last_emitted_command or now - last_emitted_time > 2.5:
                                signals.voice_command_signal.emit(cmd)
                                last_emitted_command = cmd
                                last_emitted_time = now
                        else:
                            active_listening = True
                            active_listening_time = time.time()
                            signals.active_listening_signal.emit(True)
                        try:
                            wake_rec.Reset()
                        except Exception:
                            pass

        except Exception as e:
            consecutive_errors += 1
            err_msg = str(e)
            print(f"[VOICE] Audio error ({consecutive_errors}/{MAX_ERRORS}): {err_msg}")
            voice_backend_status.update({"ready": False, "listening": False, "last_error": err_msg})
            if consecutive_errors >= MAX_ERRORS:
                print("[VOICE] Too many consecutive errors. Pausing voice backend for 30s.")
                time.sleep(30)
                consecutive_errors = 0
            else:
                time.sleep(3)
            # Re-mark ready after pause so UI doesn't stay stuck
            voice_backend_status["ready"] = True

# --- PyQt5 Window ---
class EdithWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineProfile
        
        profile = QWebEngineProfile.defaultProfile()
        profile.clearHttpCache()

        self.browser = QWebEngineView()
        self.browser.settings().setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
        self.browser.page().setBackgroundColor(Qt.transparent)
        
        # Auto-grant Camera and Microphone permissions
        self.browser.page().featurePermissionRequested.connect(
            lambda url, feature: self.browser.page().setFeaturePermission(url, feature, QWebEnginePage.PermissionGrantedByUser)
        )
        
        self.browser.setUrl(QUrl("http://localhost:7420"))
        self.setCentralWidget(self.browser)
        
        signals.mode_signal.connect(self.change_mode)
        signals.voice_command_signal.connect(self.handle_voice_command)
        signals.exit_signal.connect(self.close)
        signals.active_listening_signal.connect(self.handle_active_listening)
        self.change_mode("full")

    def handle_active_listening(self, is_active):
        if is_active:
            js = "document.querySelector('.arc-reactor').classList.add('arc-listening');"
        else:
            js = "document.querySelector('.arc-reactor').classList.remove('arc-listening');"
        self.browser.page().runJavaScript(js)

    def handle_voice_command(self, cmd):
        safe_cmd = json.dumps(cmd)
        js = f"addChatMsg('user', {safe_cmd}); processCommand({safe_cmd});"
        self.browser.page().runJavaScript(js)

    def change_mode(self, mode):
        global current_mode
        current_mode = mode
        screen = QDesktopWidget().screenGeometry()
        # Safety: always sync CSS classes from Python side too
        # (handles first boot + voice-triggered mode changes)
        if mode == "top":
            def _do_top():
                js = ("document.body.classList.add('mode-top');"
                      "document.body.classList.remove('mode-hide','mode-full');")
                self.browser.page().runJavaScript(js)
                self.setGeometry(0, 0, screen.width(), 80)
            QTimer.singleShot(16, _do_top)
        elif mode == "hide":
            def _do_hide():
                js = ("document.body.classList.add('mode-hide');"
                      "document.body.classList.remove('mode-top','mode-full');")
                self.browser.page().runJavaScript(js)
                # Use availableGeometry to avoid taskbar overlap, and a smaller footprint
                avail = QDesktopWidget().availableGeometry()
                self.setGeometry(avail.width() - 190, avail.height() - 190, 180, 180)
            QTimer.singleShot(16, _do_hide)
        else:  # full
            # Apply CSS classes FIRST
            js = ("document.body.classList.add('mode-full');"
                  "document.body.classList.remove('mode-top','mode-hide');")
            self.browser.page().runJavaScript(js)
            def _do_resize():
                self.setGeometry(0, 0, screen.width(), screen.height())
                self.show()
                self.raise_()
                self.activateWindow()
            QTimer.singleShot(32, _do_resize)  # Slightly longer delay for full screen

if __name__ == '__main__':
    threading.Thread(target=run_server, daemon=True).start()
    threading.Thread(target=wake_word_listener, daemon=True).start()
    app = QApplication(sys.argv)
    window = EdithWindow()
    window.show()
    sys.exit(app.exec_())

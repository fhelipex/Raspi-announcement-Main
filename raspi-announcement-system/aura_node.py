"""
aura_node.py — Unified 2-Way WiFi Intercom Peer Node
=====================================================
Run this on BOTH devices (Raspberry Pi or Laptop).
Each node hosts its own Flask dashboard, handles TTS playback,
accepts text broadcasts from peers, and can forward broadcasts to its peer.

Usage:
    python aura_node.py                        # Use default config.json
    python aura_node.py --port 5001 --peer http://localhost:5000 --name "Node B"
"""

import os
import sys
import time
import json
import uuid
import queue
import argparse
import threading
import subprocess
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS

# Optional: speech_recognition for physical mic STT (Raspberry Pi USB mics)
try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except ImportError:
    SR_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# Argument Parsing (override config.json values from command line)
# ─────────────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Aura 2-Way Intercom Node")
parser.add_argument("--port",   type=int,   default=None, help="Port for this node to listen on")
parser.add_argument("--peer",   type=str,   default=None, help="Full URL of the peer node (e.g. http://192.168.1.10:5000)")
parser.add_argument("--name",   type=str,   default=None, help="Human-readable name for this node")
parser.add_argument("--engine", type=str,   default=None, help="TTS engine: pyttsx3, gtts, espeak")
parser.add_argument("--config", type=str,   default="config.json", help="Path to config.json file")
args, _ = parser.parse_known_args()

# ─────────────────────────────────────────────────────────────────────────────
# Load Configuration (config.json + CLI overrides)
# ─────────────────────────────────────────────────────────────────────────────

CONFIG_PATH = os.path.join(os.path.dirname(__file__), args.config)
cfg = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r") as f:
        cfg = json.load(f)

NODE_NAME   = args.name   or cfg.get("node_name",   "Aura Node")
PORT        = args.port   or cfg.get("port",         5000)
PEER_URL    = args.peer   or cfg.get("peer_url",     "http://localhost:5001")
TTS_ENGINE  = args.engine or cfg.get("tts_engine",  "pyttsx3")
VOLUME      = float(cfg.get("volume",  1.0))
SPEED       = int(cfg.get("speed",    150))
OFFLINE_FB  = bool(cfg.get("offline_fallback", True))
MIC_ENABLED = bool(cfg.get("mic_enabled", True))
DEFAULT_MODE = cfg.get("mode", "transmitting")

# Paths
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR  = os.path.join(BASE_DIR, "templates")
STATIC_DIR     = os.path.join(BASE_DIR, "static")
TEMP_AUDIO_DIR = os.path.join(BASE_DIR, "temp_audio")
for d in [TEMPLATES_DIR, STATIC_DIR, TEMP_AUDIO_DIR]:
    os.makedirs(d, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Flask App
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=STATIC_DIR)
CORS(app)  # Allow cross-origin requests from peer dashboards

# ─────────────────────────────────────────────────────────────────────────────
# Shared State
# ─────────────────────────────────────────────────────────────────────────────

announcement_queue  = queue.Queue()
announcements_log   = []          # Chronological list (newest first)
peer_status         = {           # Cached peer telemetry
    "url":        PEER_URL,
    "name":       "Peer",
    "online":     False,
    "last_seen":  None,
    "latency_ms": None,
}
node_settings = {
    "engine":           TTS_ENGINE,
    "volume":           VOLUME,
    "speed":            SPEED,
    "offline_fallback": OFFLINE_FB,
    "mode":             DEFAULT_MODE,
}
audio_lock  = threading.Lock()
start_time  = time.time()

# Physical mic state
mic_state = {
    "enabled":     MIC_ENABLED and SR_AVAILABLE,
    "listening":   False,
    "last_heard":  None,
    "last_text":   None,
    "error":       None,
    "sr_available": SR_AVAILABLE,
}

# ─────────────────────────────────────────────────────────────────────────────
# Audio Playback Helpers
# ─────────────────────────────────────────────────────────────────────────────

def play_audio_file(filepath):
    """Play an audio file (mp3/wav) cross-platform."""
    if sys.platform.startswith("win"):
        abs_path = os.path.abspath(filepath).replace("\\", "/")
        ps_cmd = (
            f"Add-Type -AssemblyName PresentationCore; "
            f"$p = New-Object System.Windows.Media.MediaPlayer; "
            f"$p.Open('{abs_path}'); $p.Play(); Start-Sleep -s 10; $p.Close()"
        )
        try:
            subprocess.run(
                ["powershell", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
        except Exception as e:
            print(f"[Aura] Windows audio error: {e}")
            return False
    else:
        for cmd in [["mpg123", filepath], ["mpg321", filepath],
                    ["aplay", filepath], ["cvlc", "--play-and-exit", filepath]]:
            try:
                if subprocess.call(["which", cmd[0]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
            except Exception:
                continue
        return False


# ─────────────────────────────────────────────────────────────────────────────
# TTS Engines
# ─────────────────────────────────────────────────────────────────────────────

def speak_gtts(text, volume, speed):
    """Google TTS (requires internet). Saves MP3 and plays it."""
    try:
        from gtts import gTTS
        path = os.path.join(TEMP_AUDIO_DIR, f"aura_{uuid.uuid4().hex}.mp3")
        gTTS(text=text, lang="en", slow=(speed < 120)).save(path)
        ok = play_audio_file(path)
        try:
            os.remove(path)
        except OSError:
            pass
        return ok
    except Exception as e:
        print(f"[Aura] gTTS error: {e}")
        return False


def speak_pyttsx3(text, volume, speed):
    """Offline TTS — native SAPI5 on Windows via PowerShell, pyttsx3 on Linux."""
    if sys.platform.startswith("win"):
        rate  = max(-10, min(10, int((speed - 150) / 10)))
        vol   = int(volume * 100)
        safe  = text.replace("'", "''")
        ps_cmd = (
            f"Add-Type -AssemblyName System.Speech; "
            f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Rate = {rate}; $s.Volume = {vol}; $s.Speak('{safe}')"
        )
        try:
            subprocess.run(
                ["powershell", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
        except Exception as e:
            print(f"[Aura] SAPI5 error: {e}")
            return False
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("volume", volume)
        engine.setProperty("rate",   speed)
        engine.say(text)
        engine.runAndWait()
        return True
    except Exception as e:
        print(f"[Aura] pyttsx3 error: {e}")
        return False


def speak_espeak(text, volume, speed):
    """Fallback: direct espeak CLI (Linux/Pi only)."""
    if sys.platform.startswith("win"):
        return False
    try:
        subprocess.run(
            ["espeak", "-a", str(int(volume * 200)), "-s", str(speed), text],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return True
    except Exception as e:
        print(f"[Aura] espeak error: {e}")
        return False


def process_tts(text, engine, volume, speed):
    """Route TTS through the chosen engine with fallback chain."""
    with audio_lock:
        print(f"[Aura TTS] Speaking: '{text}' via '{engine}'")
        if engine == "gtts":
            ok = speak_gtts(text, volume, speed)
            if not ok and node_settings["offline_fallback"]:
                ok = speak_pyttsx3(text, volume, speed) or speak_espeak(text, volume, speed)
        elif engine == "pyttsx3":
            ok = speak_pyttsx3(text, volume, speed) or speak_espeak(text, volume, speed)
        else:
            ok = speak_espeak(text, volume, speed) or speak_pyttsx3(text, volume, speed)
        if not ok:
            print(f"[Aura TTS] All engines failed for: '{text}'")
        return ok


# ─────────────────────────────────────────────────────────────────────────────
# Background Queue Worker
# ─────────────────────────────────────────────────────────────────────────────

def queue_worker():
    print(f"[Aura] TTS queue worker started on {NODE_NAME}:{PORT}")
    while True:
        try:
            item = announcement_queue.get()
            if item is None:
                break
            _update_log_status(item["id"], "Speaking")
            ok = process_tts(item["text"], item["engine"], item["volume"], item["speed"])
            _update_log_status(item["id"], "Completed" if ok else "Failed")
            announcement_queue.task_done()
        except Exception as e:
            print(f"[Aura] Queue worker error: {e}")


def _update_log_status(entry_id, status):
    for entry in announcements_log:
        if entry["id"] == entry_id:
            entry["status"] = status
            break


worker_thread = threading.Thread(target=queue_worker, daemon=True)
worker_thread.start()


# ─────────────────────────────────────────────────────────────────────────────
# Physical Mic STT Listener Thread
# ─────────────────────────────────────────────────────────────────────────────

def mic_listener_loop():
    """
    Continuously listens on the physical microphone using speech_recognition.
    When speech is detected, the transcribed text is automatically broadcast
    to the peer node (same as clicking 'Broadcast' in the dashboard).
    Only runs if speech_recognition is installed and mic_state['enabled'] is True.
    """
    if not SR_AVAILABLE:
        print("[Aura Mic] speech_recognition not installed — physical mic disabled.")
        print("[Aura Mic] Install with: pip install SpeechRecognition pyaudio")
        return

    import urllib.request

    recognizer = sr.Recognizer()
    recognizer.energy_threshold     = cfg.get("mic_energy_threshold", 300)
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold       = 0.8   # seconds of silence = end of phrase
    phrase_limit = cfg.get("mic_phrase_limit", 8)  # max seconds per phrase

    print(f"[Aura Mic] Physical mic listener started. Energy threshold: {recognizer.energy_threshold}")

    while True:
        if not mic_state["enabled"] or node_settings.get("mode") == "listening":
            time.sleep(1)
            continue

        try:
            with sr.Microphone() as source:
                mic_state["listening"] = True
                mic_state["error"]     = None
                # Adjust for ambient noise every 30 cycles
                recognizer.adjust_for_ambient_noise(source, duration=0.5)

                print("[Aura Mic] Listening…")
                try:
                    audio = recognizer.listen(
                        source,
                        timeout=10,
                        phrase_time_limit=phrase_limit
                    )
                except sr.WaitTimeoutError:
                    mic_state["listening"] = False
                    continue

            mic_state["listening"] = False

            # Transcribe
            try:
                text = recognizer.recognize_google(audio)
            except sr.UnknownValueError:
                print("[Aura Mic] Could not understand audio — skipping.")
                continue
            except sr.RequestError as e:
                # Google STT unavailable — try offline Sphinx fallback
                print(f"[Aura Mic] Google STT error: {e}. Trying offline Sphinx…")
                try:
                    text = recognizer.recognize_sphinx(audio)
                except Exception:
                    mic_state["error"] = "STT service unavailable"
                    time.sleep(2)
                    continue

            text = text.strip()
            if not text:
                continue

            print(f"[Aura Mic] Transcribed: '{text}'")
            mic_state["last_heard"] = datetime.now().strftime("%H:%M:%S")
            mic_state["last_text"]  = text

            # Auto-broadcast the transcribed text to peer (same as dashboard broadcast)
            payload = json.dumps({
                "text":   text,
                "source": f"🎤 Physical Mic @ {NODE_NAME}",
                "engine": node_settings["engine"],
                "volume": node_settings["volume"],
                "speed":  node_settings["speed"],
            }).encode()

            # Enqueue locally
            _enqueue(text, f"🎤 Physical Mic → {NODE_NAME}",
                     node_settings["engine"], node_settings["volume"], node_settings["speed"])

            # Forward to peer
            peer_url = PEER_URL.rstrip("/") + "/api/announce"
            try:
                req = urllib.request.Request(
                    peer_url, data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=5):
                    pass
                print(f"[Aura Mic] Forwarded to peer: {peer_url}")
            except Exception as e:
                print(f"[Aura Mic] Could not forward to peer: {e}")

        except OSError as e:
            mic_state["listening"] = False
            mic_state["error"]     = f"Mic hardware error: {e}"
            print(f"[Aura Mic] Hardware error: {e} — retrying in 5s")
            time.sleep(5)
        except Exception as e:
            mic_state["listening"] = False
            mic_state["error"]     = str(e)
            print(f"[Aura Mic] Unexpected error: {e}")
            time.sleep(2)


if MIC_ENABLED and SR_AVAILABLE:
    mic_thread = threading.Thread(target=mic_listener_loop, daemon=True)
    mic_thread.start()
else:
    if not SR_AVAILABLE:
        print("[Aura Mic] speech_recognition not found. Browser mic (Web Speech API) is still available.")
    else:
        print("[Aura Mic] Physical mic disabled in config.json (mic_enabled: false).")


# ─────────────────────────────────────────────────────────────────────────────
# Peer Ping Background Thread
# ─────────────────────────────────────────────────────────────────────────────

def peer_ping_loop():
    """Continuously pings the remote peer to track its online state."""
    import urllib.request
    import urllib.error

    while True:
        try:
            t0 = time.time()
            url = PEER_URL.rstrip("/") + "/api/peer_info"
            req = urllib.request.Request(
                url,
                data=json.dumps({"name": NODE_NAME, "port": PORT}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
            latency = round((time.time() - t0) * 1000)
            peer_status.update({
                "online":     True,
                "last_seen":  datetime.now().strftime("%H:%M:%S"),
                "latency_ms": latency,
                "name":       data.get("name", "Peer"),
                "uptime":     data.get("uptime", 0),
            })
        except Exception:
            peer_status["online"] = False
            peer_status["latency_ms"] = None
        time.sleep(5)


peer_ping_thread = threading.Thread(target=peer_ping_loop, daemon=True)
peer_ping_thread.start()


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Enqueue a new announcement
# ─────────────────────────────────────────────────────────────────────────────

def _enqueue(text, source, engine=None, volume=None, speed=None):
    engine  = engine  or node_settings["engine"]
    volume  = volume  if volume  is not None else node_settings["volume"]
    speed   = speed   if speed   is not None else node_settings["speed"]
    aid     = str(uuid.uuid4())
    entry   = {
        "id":        aid,
        "text":      text,
        "timestamp": datetime.now().strftime("%I:%M:%S %p"),
        "source":    source,
        "engine":    engine,
        "volume":    float(volume),
        "speed":     int(speed),
        "status":    "Queued",
    }
    announcements_log.insert(0, entry)
    if len(announcements_log) > 100:
        announcements_log.pop()
    announcement_queue.put(entry)
    return aid


# ─────────────────────────────────────────────────────────────────────────────
# Flask Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return render_template("index.html",
                           node_name=NODE_NAME,
                           port=PORT,
                           peer_url=PEER_URL)


@app.route("/api/peer_info", methods=["GET", "POST"])
def peer_info():
    """Called by the remote peer to announce itself and get our info back."""
    # If it's a POST, the peer is sending us its info
    if request.method == "POST":
        data = request.json or {}
        peer_status["name"] = data.get("name", "Peer")
    uptime = int(time.time() - start_time)
    return jsonify({
        "name":   NODE_NAME,
        "port":   PORT,
        "uptime": uptime,
        "status": "online",
    })


@app.route("/api/announce", methods=["POST"])
def announce():
    """
    Receive a text announcement and speak it locally.
    Payload: { "text": "...", "source": "...", "engine": "...", "volume": 1.0, "speed": 150 }
    """
    try:
        data   = request.json or {}
        text   = data.get("text", "").strip()
        if not text:
            return jsonify({"status": "error", "message": "Empty text"}), 400

        source = data.get("source", "Remote Peer")
        aid    = _enqueue(
            text, source,
            engine=data.get("engine"),
            volume=data.get("volume"),
            speed=data.get("speed"),
        )
        return jsonify({"status": "success", "id": aid, "queue": announcement_queue.qsize()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/broadcast", methods=["POST"])
def broadcast():
    """
    High-level send endpoint from the dashboard.
    Payload: { "text": "...", "target": "local" | "peer" | "both" }
    The server forwards to the peer if target is "peer" or "both".
    """
    import urllib.request
    import urllib.error

    try:
        data   = request.json or {}
        text   = data.get("text", "").strip()
        target = data.get("target", "both")
        engine = data.get("engine", node_settings["engine"])
        volume = float(data.get("volume", node_settings["volume"]))
        speed  = int(data.get("speed",  node_settings["speed"]))

        if not text:
            return jsonify({"status": "error", "message": "Empty text"}), 400

        results = {}

        # Play locally
        if target in ("local", "both"):
            aid = _enqueue(text, f"Local Console → {NODE_NAME}", engine, volume, speed)
            results["local"] = {"status": "queued", "id": aid}

        # Forward to peer
        if target in ("peer", "both"):
            payload = json.dumps({
                "text":   text,
                "source": f"{NODE_NAME} → Peer",
                "engine": engine,
                "volume": volume,
                "speed":  speed,
            }).encode()
            peer_announce_url = PEER_URL.rstrip("/") + "/api/announce"
            try:
                req = urllib.request.Request(
                    peer_announce_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    peer_resp = json.loads(resp.read())
                results["peer"] = {"status": "forwarded", "peer_response": peer_resp}
            except urllib.error.URLError as e:
                results["peer"] = {"status": "error", "message": f"Peer unreachable: {e}"}

        return jsonify({"status": "success", "results": results})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/status", methods=["GET"])
def status():
    """Returns this node's telemetry plus cached peer state."""
    cpu_pct = mem_pct = 0.0
    try:
        import psutil
        cpu_pct = psutil.cpu_percent(interval=None)
        mem_pct = psutil.virtual_memory().percent
    except ImportError:
        pass

    return jsonify({
        "node_name":    NODE_NAME,
        "port":         PORT,
        "peer_url":     PEER_URL,
        "uptime":       int(time.time() - start_time),
        "server_time":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "queue_length": announcement_queue.qsize(),
        "settings":     node_settings,
        "telemetry": {
            "cpu":    cpu_pct,
            "memory": mem_pct,
            "status": "Online",
        },
        "peer": peer_status,
    })


@app.route("/api/logs", methods=["GET"])
def logs():
    return jsonify(announcements_log[:50])


@app.route("/api/logs/clear", methods=["POST"])
def clear_logs():
    announcements_log.clear()
    return jsonify({"status": "success"})


@app.route("/api/transcribe", methods=["POST"])
def transcribe_audio():
    """
    Accepts an audio blob uploaded from the browser MediaRecorder fallback.
    Converts to WAV (via ffmpeg) then transcribes with speech_recognition.
    Returns: { "status": "success", "text": "..." }
    """
    if not SR_AVAILABLE:
        return jsonify({
            "status":  "error",
            "message": "speech_recognition not installed. Run: pip install SpeechRecognition"
        }), 503

    if "audio" not in request.files:
        return jsonify({"status": "error", "message": "No audio file in request"}), 400

    audio_file = request.files["audio"]
    orig_ext = os.path.splitext(audio_file.filename)[1].lower() or ".webm"
    fname    = os.path.join(TEMP_AUDIO_DIR, "upload_" + uuid.uuid4().hex + orig_ext)
    wav_path = fname if orig_ext == ".wav" else fname.replace(orig_ext, ".wav")

    try:
        audio_file.save(fname)
        converted = False
        if orig_ext == ".wav":
            converted = True
        else:
            try:
                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", fname,
                     "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15
                )
                if result.returncode == 0 and os.path.exists(wav_path):
                    converted = True
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        recognizer  = sr.Recognizer()
        source_path = wav_path if converted else fname

        with sr.AudioFile(source_path) as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.3)
            audio_data = recognizer.record(source)

        try:
            text = recognizer.recognize_google(audio_data)
        except sr.RequestError:
            try:
                text = recognizer.recognize_sphinx(audio_data)
            except Exception:
                return jsonify({
                    "status":  "error",
                    "message": "STT service unavailable. Check internet or install pocketsphinx."
                }), 503
        except sr.UnknownValueError:
            return jsonify({
                "status":  "error",
                "message": "Could not understand audio — please speak more clearly."
            }), 422

        print("[Aura Transcribe] Browser audio -> '" + text + "'")
        return jsonify({"status": "success", "text": text.strip()})

    except Exception as e:
        return jsonify({"status": "error", "message": "Audio processing error: " + str(e)}), 500
    finally:
        for p in [fname, wav_path]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


@app.route("/api/mic_status", methods=["GET"])
def mic_status():
    """Returns physical microphone listener status."""
    return jsonify(mic_state)


@app.route("/api/mic_control", methods=["POST"])
def mic_control():
    """
    Enable or disable the physical mic listener at runtime.
    Payload: { "enabled": true | false }
    """
    try:
        data = request.json or {}
        if "enabled" in data:
            if not SR_AVAILABLE:
                return jsonify({"status": "error", "message": "speech_recognition not installed"}), 400
            mic_state["enabled"] = bool(data["enabled"])
        return jsonify({"status": "success", "mic": mic_state})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/settings", methods=["POST"])
def update_settings():
    """Update TTS settings at runtime."""
    try:
        data = request.json or {}
        if "engine"   in data: node_settings["engine"]           = data["engine"]
        if "volume"   in data: node_settings["volume"]           = max(0.0, min(1.0, float(data["volume"])))
        if "speed"    in data: node_settings["speed"]            = max(50,  min(300,  int(data["speed"])))
        if "offline_fallback" in data:
            node_settings["offline_fallback"] = bool(data["offline_fallback"])
        if "mode" in data:
            if data["mode"] in ("transmitting", "listening"):
                node_settings["mode"] = data["mode"]
        return jsonify({"status": "success", "settings": node_settings})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mic_info = "Enabled (speech_recognition)" if (MIC_ENABLED and SR_AVAILABLE) \
               else ("Disabled (install SpeechRecognition+PyAudio)" if not SR_AVAILABLE else "Disabled in config")
    print(f"""
+----------------------------------------------------------+
|   AURA -- 2-Way WiFi Intercom Node                       |
+----------------------------------------------------------+
|  Node Name : {NODE_NAME:<44}|
|  Local URL : http://0.0.0.0:{PORT:<31}|
|  Peer URL  : {PEER_URL:<44}|
|  TTS Engine: {node_settings['engine']:<44}|
|  Phys. Mic : {mic_info:<44}|
|  Browser   : Web Speech API (Chrome/Edge)                |
+----------------------------------------------------------+
Open your browser at -> http://localhost:{PORT}
""")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)

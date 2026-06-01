import os
import sys
import time
import json
import uuid
import queue
import threading
import subprocess
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_from_directory

app = Flask(__name__)

# System State and Queues
announcement_queue = queue.Queue()
announcements_log = []
active_clients = {}
tts_settings = {
    "default_engine": "gtts",  # "gtts" or "pyttsx3" or "espeak"
    "volume": 1.0,             # 0.0 to 1.0
    "speed": 150,              # Speech rate (words per minute)
    "offline_fallback": True   # Fallback to offline engine if online gTTS fails
}

# Lock for playing audio sequentially
audio_lock = threading.Lock()

# Uptime tracker
start_time = time.time()

# Ensure directories exist
STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates')
TEMP_AUDIO_DIR = os.path.join(os.path.dirname(__file__), 'temp_audio')

for d in [STATIC_DIR, TEMPLATES_DIR, TEMP_AUDIO_DIR]:
    os.makedirs(d, exist_ok=True)

# ----------------- Audio Playing Utilities -----------------

def play_audio_file(filepath):
    """
    Plays an audio file (mp3 or wav) on Windows or Linux with zero third-party dependencies.
    """
    if sys.platform.startswith('win'):
        # On Windows, use a PowerShell script invoking PresentationCore MediaPlayer.
        # This has 100% reliable speaker access and plays MP3/WAV files in all environments.
        abs_path = os.path.abspath(filepath)
        ps_cmd = (
            f"Add-Type -AssemblyName PresentationCore; "
            f"$player = New-Object System.Windows.Media.MediaPlayer; "
            f"$player.Open('{abs_path}'); "
            f"$player.Play(); "
            f"Start-Sleep -s 8; "
            f"$player.Close()"
        )
        try:
            subprocess.run(["powershell", "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            print(f"Windows PresentationCore audio playback error: {e}")
            return False
    else:
        # On Linux / Raspberry Pi, try standard command line players.
        players = [
            ["mpg123", filepath],
            ["mpg321", filepath],
            ["aplay", filepath],  # mainly for WAV files
            ["cvlc", "--play-and-exit", filepath],
            ["play", filepath]    # Sox player
        ]
        
        for player in players:
            try:
                # Check if command exists
                cmd = player[0]
                if subprocess.call(["which", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
                    subprocess.run(player, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
            except Exception:
                continue
        print("Linux audio playback error: No suitable audio player (mpg123, cvlc, aplay) found.")
        return False

# ----------------- TTS Engines -----------------

def speak_gtts(text, volume, speed):
    """
    Converts text to speech using Google TTS (requires internet).
    Saves to a temporary file and plays it.
    """
    try:
        from gtts import gTTS
        
        filename = f"announcement_{uuid.uuid4().hex}.mp3"
        filepath = os.path.join(TEMP_AUDIO_DIR, filename)
        
        # Speed mapping: gtts supports standard or slow (boolean)
        slow = speed < 120
        
        tts = gTTS(text=text, lang='en', slow=slow)
        tts.save(filepath)
        
        # Note: volume adjustments for raw files can be made using ffmpeg, 
        # but playing at system default is the standard fallback.
        success = play_audio_file(filepath)
        
        # Clean up temp file
        try:
            os.remove(filepath)
        except OSError:
            pass
            
        return success
    except Exception as e:
        print(f"gTTS error: {e}")
        return False

def speak_pyttsx3(text, volume, speed):
    """
    Converts text to speech offline using pyttsx3 or native Windows engine.
    """
    if sys.platform.startswith('win'):
        # On Windows, call System.Speech directly via PowerShell to bypass COM multithreading bugs!
        rate = int((speed - 150) / 10)
        rate = max(-10, min(10, rate))
        vol = int(volume * 100)
        
        # Escape single quotes for PowerShell
        safe_text = text.replace("'", "''")
        ps_cmd = (
            f"Add-Type -AssemblyName System.Speech; "
            f"$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$synth.Rate = {rate}; "
            f"$synth.Volume = {vol}; "
            f"$synth.Speak('{safe_text}')"
        )
        try:
            subprocess.run(["powershell", "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            print(f"Windows native SpeechSynthesizer error: {e}")
            return False
            
    try:
        import pyttsx3
        
        engine = pyttsx3.init()
        engine.setProperty('volume', volume)
        engine.setProperty('rate', speed)
        
        # Say and block until finished
        engine.say(text)
        engine.runAndWait()
        return True
    except Exception as e:
        print(f"pyttsx3 offline error: {e}")
        return False

def speak_espeak_cli(text, volume, speed):
    """
    Direct system call to espeak (failsafe on Linux).
    """
    if sys.platform.startswith('win'):
        return False
        
    try:
        # volume: espeak -a (0 to 200, default 100) -> map 0.0-1.0 to 0-200
        a_val = int(volume * 200)
        # speed: espeak -s (words/min, default 175)
        s_val = speed
        
        subprocess.run(["espeak", "-a", str(a_val), "-s", str(s_val), text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        print(f"espeak CLI error: {e}")
        return False

def process_tts(text, engine_pref, volume, speed):
    """
    Route TTS request to appropriate engine with automatic offline fallback.
    """
    with audio_lock:
        print(f"[TTS Server] Speaking announcement: '{text}' using engine: '{engine_pref}'")
        
        success = False
        
        if engine_pref == "gtts":
            success = speak_gtts(text, volume, speed)
            if not success and tts_settings["offline_fallback"]:
                print("[TTS Server] gTTS failed, falling back to offline engines...")
                success = speak_pyttsx3(text, volume, speed)
                if not success:
                    success = speak_espeak_cli(text, volume, speed)
        elif engine_pref == "pyttsx3":
            success = speak_pyttsx3(text, volume, speed)
            if not success:
                success = speak_espeak_cli(text, volume, speed)
        elif engine_pref == "espeak":
            success = speak_espeak_cli(text, volume, speed)
            if not success:
                success = speak_pyttsx3(text, volume, speed)
                
        if not success:
            print(f"[TTS Server] Failed to play announcement: '{text}'")
        return success

# ----------------- Background Queue Worker -----------------

def queue_worker():
    """
    Background worker thread to pull text announcements from queue and play them.
    """
    print("[TTS Server] Announcement background queue worker started.")
    while True:
        try:
            # Blocks until an item is available
            announcement = announcement_queue.get()
            if announcement is None:
                break
                
            announcement_id = announcement["id"]
            
            # Update log status to "Speaking..."
            for item in announcements_log:
                if item["id"] == announcement_id:
                    item["status"] = "Speaking..."
                    break
            
            success = process_tts(
                announcement["text"],
                announcement["engine"],
                announcement["volume"],
                announcement["speed"]
            )
            
            # Update status to completed or failed
            for item in announcements_log:
                if item["id"] == announcement_id:
                    item["status"] = "Completed" if success else "Failed"
                    break
                    
            announcement_queue.task_done()
        except Exception as e:
            print(f"Queue worker exception: {e}")

# Start background queue thread
worker_thread = threading.Thread(target=queue_worker, daemon=True)
worker_thread.start()

# ----------------- Flask Routes -----------------

@app.route('/')
def home():
    """
    Renders the central system administration dashboard.
    """
    return render_template('index.html')

@app.route('/api/ping', methods=['POST'])
def client_ping():
    """
    Endpoint for VTT Client (Raspi 1) or browser to report active connection.
    JSON Payload: {"client_id": "raspi_vtt", "ip": "192.168.1.15", "details": "Linux..."}
    """
    try:
        data = request.json or {}
        client_id = data.get("client_id", "unknown_client")
        ip = data.get("ip", request.remote_addr)
        details = data.get("details", "")
        
        active_clients[client_id] = {
            "ip": ip,
            "details": details,
            "last_seen": time.time(),
            "status": "Online"
        }
        return jsonify({"status": "success", "message": f"Client {client_id} active."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/status', methods=['GET'])
def system_status():
    """
    Returns telemetry stats of both the TTS and VTT nodes.
    """
    # Clean up offline clients (no ping for 15 seconds)
    now = time.time()
    for cid, cinfo in list(active_clients.items()):
        if now - cinfo["last_seen"] > 15.0:
            cinfo["status"] = "Offline"

    # CPU/Memory reading helper
    cpu_percent = 0.0
    mem_percent = 0.0
    try:
        import psutil
        cpu_percent = psutil.cpu_percent()
        mem_percent = psutil.virtual_memory().percent
    except ImportError:
        # Fallback if psutil is not installed
        pass

    uptime = int(time.time() - start_time)
    
    return jsonify({
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "uptime": uptime,
        "queue_length": announcement_queue.qsize(),
        "active_engine": tts_settings["default_engine"],
        "offline_fallback": tts_settings["offline_fallback"],
        "tts_telemetry": {
            "cpu": cpu_percent,
            "memory": mem_percent,
            "status": "Online",
            "ip": request.host.split(':')[0]
        },
        "clients": active_clients
    })

@app.route('/api/announce', methods=['POST'])
def enqueue_announce():
    """
    Enqueues a new text announcement.
    JSON Payload: {
        "text": "The text to speak",
        "source": "VTT Client" / "Web Console",
        "engine": "gtts" / "pyttsx3" / "espeak" (optional),
        "volume": 0.0-1.0 (optional),
        "speed": 100-300 (optional)
    }
    """
    try:
        data = request.json or {}
        text = data.get("text", "").strip()
        
        if not text:
            return jsonify({"status": "error", "message": "Text payload is empty"}), 400
            
        source = data.get("source", "Remote API")
        engine = data.get("engine", tts_settings["default_engine"])
        volume = float(data.get("volume", tts_settings["volume"]))
        speed = int(data.get("speed", tts_settings["speed"]))
        
        announcement_id = str(uuid.uuid4())
        announcement_item = {
            "id": announcement_id,
            "text": text,
            "timestamp": datetime.now().strftime("%I:%M:%S %p"),
            "source": source,
            "engine": engine,
            "volume": volume,
            "speed": speed,
            "status": "Queued"
        }
        
        # Log it
        announcements_log.insert(0, announcement_item)
        if len(announcements_log) > 50:  # Cap log size
            announcements_log.pop()
            
        # Put into background queue
        announcement_queue.put(announcement_item)
        
        return jsonify({
            "status": "success",
            "message": "Announcement enqueued successfully",
            "id": announcement_id,
            "queue_position": announcement_queue.qsize()
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/logs', methods=['GET'])
def get_logs():
    """
    Returns lists of recent announcements and their play statuses.
    """
    return jsonify(announcements_log)

@app.route('/api/clear_logs', methods=['POST'])
def clear_logs():
    """
    Clears the log history.
    """
    announcements_log.clear()
    return jsonify({"status": "success", "message": "Logs cleared."})

@app.route('/api/settings', methods=['POST'])
def update_settings():
    """
    Updates server settings in real-time.
    """
    try:
        data = request.json or {}
        if "default_engine" in data:
            tts_settings["default_engine"] = data["default_engine"]
        if "volume" in data:
            tts_settings["volume"] = max(0.0, min(1.0, float(data["volume"])))
        if "speed" in data:
            tts_settings["speed"] = max(50, min(300, int(data["speed"])))
        if "offline_fallback" in data:
            tts_settings["offline_fallback"] = bool(data["offline_fallback"])
            
        return jsonify({"status": "success", "message": "Settings updated", "settings": tts_settings})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == '__main__':
    # Listen on all interfaces so Raspi 1 (or other network devices) can connect.
    print("[TTS Server] Starting Flask announcement receiver...")
    app.run(host='0.0.0.0', port=5000, debug=False)

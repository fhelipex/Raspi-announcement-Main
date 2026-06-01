import os
import sys
import time
import json
import socket
import threading
import requests

# Load Config
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

try:
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)
except Exception:
    config = {
        "server_url": "http://localhost:5000",
        "client_id": "raspi_vtt",
        "ping_interval": 5.0,
        "energy_threshold": 300,
        "dynamic_energy_threshold": true,
        "phrase_time_limit": 8.0,
        "language": "en-US"
    }

SERVER_URL = config.get("server_url", "http://localhost:5000")
CLIENT_ID = config.get("client_id", "raspi_vtt")
PING_INTERVAL = config.get("ping_interval", 5.0)
LANGUAGE = config.get("language", "en-US")

# Check if speech recognition and PyAudio are installed
SPEECH_REC_AVAILABLE = False
try:
    import speech_recognition as sr
    SPEECH_REC_AVAILABLE = True
except ImportError:
    pass

# Helper to get local IP address
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

LOCAL_IP = get_local_ip()

# ----------------- Status Ping Loop -----------------

def status_ping_worker():
    """
    Background worker that pings the TTS Server at regular intervals
    so the Server's Admin Dashboard knows we are Online.
    """
    print(f"[VTT Client] Status ping worker started. Registering with server at {SERVER_URL}...")
    ping_url = f"{SERVER_URL}/api/ping"
    
    while True:
        try:
            payload = {
                "client_id": CLIENT_ID,
                "ip": LOCAL_IP,
                "details": f"OS: {sys.platform} | Host: {socket.gethostname()}"
            }
            response = requests.post(ping_url, json=payload, timeout=2.0)
            if response.status_code != 200:
                print(f"[VTT Client] Ping warning: Server responded with status code {response.status_code}")
        except requests.exceptions.RequestException:
            # Silent failure during pings to avoid spamming the console when server is briefly down
            pass
        time.sleep(PING_INTERVAL)

# Start ping thread in background
ping_thread = threading.Thread(target=status_ping_worker, daemon=True)
ping_thread.start()

# ----------------- Transmission Helper -----------------

def transmit_text(text):
    """
    Transmits recognized text to the TTS Server.
    """
    announce_url = f"{SERVER_URL}/api/announce"
    payload = {
        "text": text,
        "source": "VTT Client",
        "volume": 1.0,
        "speed": 150
    }
    try:
        print(f"[VTT Client] Transmitting announcement: '{text}'")
        response = requests.post(announce_url, json=payload, timeout=3.0)
        if response.status_code == 200:
            print("[VTT Client] Server acknowledged transmission successfully.")
            return True
        else:
            print(f"[VTT Client] Server error: {response.json().get('message', 'Unknown')}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"[VTT Client] Connection error transmitting announcement: {e}")
        return False

# ----------------- Main Listen Loops -----------------

def listen_and_transcribe_mic():
    """
    Main Speech Recognition loop listening to physical microphone.
    """
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = config.get("energy_threshold", 300)
    recognizer.dynamic_energy_threshold = config.get("dynamic_energy_threshold", True)
    
    print("\n[VTT Client] Initializing physical audio input...")
    try:
        mic = sr.Microphone()
    except Exception as e:
        print(f"[VTT Client] CRITICAL AUDIO ERROR: Could not access microphone. {e}")
        print("[VTT Client] PyAudio may not be configured correctly, or no mic is plugged in.")
        return False

    with mic as source:
        print("[VTT Client] Calibrating for ambient noise... (Please keep quiet for 2s)")
        recognizer.adjust_for_ambient_noise(source, duration=2)
        print(f"[VTT Client] Calibration completed. Energy threshold set to: {recognizer.energy_threshold}")
        print("\n=== SYSTEM IS ACTIVE AND LISTENING FOR ANNOUNCEMENTS ===")
        print("Speak into your microphone now. Say something to test it...")
        
        while True:
            try:
                # Capture audio
                audio = recognizer.listen(
                    source,
                    phrase_time_limit=config.get("phrase_time_limit", 8.0)
                )
                
                print("[VTT Client] Processing audio input...")
                
                # Recognize voice (default using Google cloud)
                text = recognizer.recognize_google(audio, language=LANGUAGE)
                text = text.strip()
                
                if text:
                    print(f"[VTT Client] Recognized: \"{text}\"")
                    transmit_text(text)
                    
            except sr.UnknownValueError:
                # Recognizer could not understand the audio
                continue
            except sr.RequestError as e:
                print(f"[VTT Client] Google speech service error: {e}")
                print("[VTT Client] Attempting offline pocketsphinx fallback...")
                try:
                    text = recognizer.recognize_sphinx(audio, language=LANGUAGE)
                    if text.strip():
                        print(f"[VTT Client] Offline Recognized: \"{text}\"")
                        transmit_text(text)
                except Exception as sphinx_err:
                    print(f"[VTT Client] Offline pocketsphinx also failed: {sphinx_err}")
            except Exception as e:
                print(f"[VTT Client] Audio loop error: {e}")
                time.sleep(1)

def listen_and_transcribe_simulated():
    """
    Failsafe keyboard simulation loop in case PyAudio or Mic drivers are missing.
    """
    print("\n" + "="*60)
    print("  KEYBOARD SIMULATION MODE ENABLED  ")
    print("  (Audio drivers or microphone were not detected)  ")
    print("="*60)
    print("\nYou can simulate announcements by typing them below.")
    print("Type your message and press ENTER to transmit it to Raspi 2 (TTS).")
    print("Type 'exit' to quit.\n")
    
    while True:
        try:
            text = input("Simulated Speech Input > ").strip()
            if not text:
                continue
            if text.lower() == 'exit':
                print("[VTT Client] Exiting...")
                break
                
            transmit_text(text)
        except (KeyboardInterrupt, EOFError):
            print("\n[VTT Client] Exiting...")
            break

def main():
    print("="*50)
    print("   RASPBERRY PI ANNOUNCEMENT SYSTEM - VTT CLIENT  ")
    print("="*50)
    print(f"Local Client IP: {LOCAL_IP}")
    print(f"Target TTS Server: {SERVER_URL}")
    print(f"Active Language: {LANGUAGE}")
    
    # Check if speech recognition is available, otherwise run simulated
    if not SPEECH_REC_AVAILABLE:
        print("\n[VTT Client] 'SpeechRecognition' package was not found.")
        print("[VTT Client] To listen to your mic, install it using: pip install SpeechRecognition")
        listen_and_transcribe_simulated()
        return

    # Attempt physical mic, fall back to simulated keyboard on driver/mic exceptions
    success = listen_and_transcribe_mic()
    if not success:
        print("[VTT Client] Falling back to Keyboard Simulation Mode...")
        listen_and_transcribe_simulated()

if __name__ == '__main__':
    main()

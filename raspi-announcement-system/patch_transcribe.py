"""patch_transcribe.py — inserts /api/transcribe route into aura_node.py"""

TRANSCRIBE_ROUTE = r'''
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
    fname    = os.path.join(TEMP_AUDIO_DIR, "upload_" + uuid.uuid4().hex + ".webm")
    wav_path = fname.replace(".webm", ".wav")

    try:
        audio_file.save(fname)
        converted = False
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

'''

import os

TARGET = os.path.join(os.path.dirname(__file__), "aura_node.py")
MARKER = '@app.route("/api/mic_status"'

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

if "/api/transcribe" in content:
    print("Transcribe route already present — skipping.")
else:
    if MARKER not in content:
        print("ERROR: Could not find insertion marker in aura_node.py")
    else:
        content = content.replace(MARKER, TRANSCRIBE_ROUTE + MARKER, 1)
        with open(TARGET, "w", encoding="utf-8") as f:
            f.write(content)
        print("Done — /api/transcribe route inserted successfully.")

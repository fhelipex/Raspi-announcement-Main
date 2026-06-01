# Aura Announcement System - Server Node (TTS-2)

This module runs on **Raspberry Pi 2**. It hosts a Flask-based web server, handles a sequential announcement queue, converts incoming text requests to speech, and serves a modern Web Control Dashboard.

## Hardware Requirements
- **Raspberry Pi** (with built-in or external WiFi connected to the same network as Pi 1).
- **Speakers or Audio Output Device** (plugged into the Pi's 3.5mm jack, HDMI port, or Bluetooth audio).

---

## Installation & Setup

Before running the server, you should configure the audio routing and install the offline speech synthesizer engine:

### 1. Install System-Level Dependencies
Run the following commands on your Raspberry Pi:
```bash
sudo apt-get update
# Install Espeak offline voice synthesizer engine
sudo apt-get install espeak espeak-data alsa-utils -y
# Install command-line MP3 player for cloud voice playback
sudo apt-get install mpg123 -y
```

### 2. Configure Pi Audio Output (Optional)
If sound does not play out of your desired port (e.g. 3.5mm jack or HDMI), configure the audio route using:
```bash
sudo raspi-config
# Navigate to: System Options -> Audio -> Select your preferred audio route (3.5mm jack or HDMI)
```

### 3. Install Python Dependencies
```bash
pip3 install -r requirements.txt
```

---

## Execution

Launch the Server script:
```bash
python3 tts_server.py
```

The server will startup on **port 5000** and listen on all network interfaces (`0.0.0.0`).

---

## Web Control Dashboard

Once the server is running, open any device (PC, phone, tablet) connected to the same WiFi network and go to:
```
http://<RASPI_2_IP_ADDRESS>:5000
```
This serves a gorgeous Web Control Console enabling:
- Real-time VTT-1 (Client) and TTS-2 (Server) connectivity monitoring.
- Telemetry stats (CPU, Memory, Uptime, Queue Length).
- Manual broadcast panel (type text, select cloud vs local engine, configure voice speed and volume).
- Live broadcast log feed showing transcripts, engine type, source, and speaker state.

---

## REST API Reference

The server exposes endpoints that can be integrated with other systems:

### 1. `POST /api/announce`
Enqueues a text announcement.
- **Headers**: `Content-Type: application/json`
- **Payload**:
```json
{
    "text": "Attention! Announcement system active.",
    "source": "API Client",
    "engine": "gtts",
    "volume": 1.0,
    "speed": 150
}
```
- **Response**: `{"status": "success", "id": "<announcement-uuid>"}`

### 2. `GET /api/status`
Fetches active connection health, queue size, and system telemetry metrics.
- **Response**: Telemetry JSON payload.

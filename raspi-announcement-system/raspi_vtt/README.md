# Aura Announcement System - Client Node (VTT-1)

This module runs on **Raspberry Pi 1**. It is responsible for continuously capturing sound from a microphone, converting recognized speech into text, and transmitting it over the local network to **Raspberry Pi 2 (TTS-2)**.

## Hardware Requirements
- **Raspberry Pi** (with built-in or external WiFi connected to the same network as Pi 2).
- **USB Microphone** or **Audio Input HAT** (like ReSpeaker). 
  *Note: Raspberry Pi does not have a native microphone/line-in port.*

---

## Installation & Setup

Before running the script, you must install several audio development drivers on the Linux system:

### 1. Install System-Level Dependencies
Run the following commands on your Raspberry Pi:
```bash
sudo apt-get update
# Install PortAudio (required for PyAudio library to bind to microphone streams)
sudo apt-get install portaudio19-dev python3-pyaudio -y
# (Optional) Install SWIG and Pocketsphinx development headers for offline speech fallback
sudo apt-get install swig libpulse-dev pocketsphinx -y
```

### 2. Install Python Dependencies
Install the required packages using pip:
```bash
pip3 install -r requirements.txt
```

---

## Configuration

Edit `config.json` before launching the script to point to the server:

```json
{
    "server_url": "http://<RASPI_2_IP_ADDRESS>:5000",
    "client_id": "raspi_vtt",
    "ping_interval": 5.0,
    "energy_threshold": 300,
    "dynamic_energy_threshold": true,
    "phrase_time_limit": 8.0,
    "language": "en-US"
}
```

- **`server_url`**: Change `<RASPI_2_IP_ADDRESS>` to the local IP address of your second Raspberry Pi running the TTS Server.
- **`energy_threshold`**: Starting mic sensitivity. If the room is loud, increase this (e.g., 400 or 500).
- **`dynamic_energy_threshold`**: When `true`, it automatically adjusts to ambient room noise dynamically.
- **`language`**: Supported BCP 47 language code (e.g., `en-US`, `es-ES`, `fr-FR`).

---

## Execution

Launch the Client script:
```bash
python3 vtt_client.py
```

### Failsafe Keyboard Simulation Mode
If the client script does not detect a working microphone or fails to load audio drivers, it will **automatically fall back to Keyboard Simulation Mode**. This allows you to type announcements directly in the console and hit Enter to transmit them to Pi 2, allowing for easy testing without audio hardware!

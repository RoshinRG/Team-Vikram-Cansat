# 🚀 CAN-7USAT Ground Station Flight Telemetry Dashboard

A complete ground station telemetry system for CAN-7USAT India 2026. This single README now contains the consolidated setup, requirements, feature list, verification guide, implementation summary, and API documentation from the project’s previous markdown files.

---

## Overview

The system receives CanSat telemetry over LoRa 433 MHz through an ESP32-based ground receiver and delivers it to a Flask web dashboard.

Architecture:

```
[CanSat TX] → LoRa 433 MHz → [ESP32 RX Ground Station] → USB/Serial → [Flask Backend] → [Web Dashboard]
```

### What this dashboard includes

- Real-time telemetry charts and metrics
- 3D rocket visualization using Three.js
- GPS tracking and live link diagnostics
- Mission event logging and command history
- Persistent SQLite storage with session/export support
- AI analysis and anomaly history support
- Mock-data mode for development without hardware

---

## Features Summary

- Live altitude, accelerometer, gyroscope, temperature, pressure, humidity, GPS, battery voltage, RSSI, and SNR
- Auto-refresh dashboard updates at ≥ 1 Hz
- Flight phase detection with phase transition logging
- Mission event log and manual event creation
- Command history and uplink command support
- Per-session CSV export with metadata
- Serial port switching and connection status
- Anomaly detection and persistent anomaly history
- Offline-friendly frontend using vanilla JS, Leaflet, and Chart.js

---

## Requirements & Upgrade Goals

### Core PDR Requirements

Must-have dashboard capabilities:

- Live altitude and charts
- Accelerometer and gyroscope plotting
- Temperature, pressure, humidity monitoring
- GPS position and map display
- Battery voltage, RSSI and SNR
- Packet counter and mission elapsed time
- Phase transition logging
- Mission event logging
- Command control and acknowledgement display
- AI analysis and anomaly timeline
- SQLite storage with session support and export
- Auto-detect COM ports and show connection health

### Key Upgrade Priorities

Critical upgrades:

- Expand telemetry packet fields to include temp, pressure, humidity, lat/lon, batt_v, rssi, snr, packet_id, mission_time
- Add GPS map panel and live ground track
- Add battery voltage gauge and link diagnostics
- Add mission session grouping and packet loss detection
- Add pressure-based apogee detection and descent rate validation
- Add phase transitions logged to the database

High priority upgrades:

- Add temperature vs time and pressure vs altitude charts
- Add descent rate plot and parachute deployment markers
- Add uplink command panel with ARM/DEPLOY/ABORT/SIM controls
- Add dynamic COM port selector
- Add per-session CSV export with metadata

Standard polish:

- Improve AI prompt with temperature, GPS, pressure
- Add anomaly history timeline
- Detect CanSat resets and show boot time
- Add mission clock, dark mode, audio alerts, and responsive UI
- Add link budget and packet success metrics

---

## Hardware Requirements

### Ground Station Receiver (RX)

| Component | Pin (ESP32) | Connection |
| --------- | ----------- | ---------- |
| LoRa SCK  | 18          | LoRa SCK   |
| LoRa MISO | 19          | LoRa MISO  |
| LoRa MOSI | 23          | LoRa MOSI  |
| LoRa CS   | 5           | LoRa NSS   |
| LoRa RST  | 14          | LoRa RST   |
| LoRa IRQ  | 26          | LoRa DIO0  |
| USB       | Micro-USB   | PC (COM3)  |

### Rocket Transmitter (TX)

| Component          | Pin (ESP32) | Connection |
| ------------------ | ----------- | ---------- |
| LoRa SCK           | 18          | LoRa SCK   |
| LoRa MISO          | 19          | LoRa MISO  |
| LoRa MOSI          | 23          | LoRa MOSI  |
| LoRa CS            | 5           | LoRa NSS   |
| LoRa RST           | 14          | LoRa RST   |
| LoRa IRQ           | 26          | LoRa DIO0  |
| MPU6050/BMP180 SDA | 21          | I2C SDA    |
| MPU6050/BMP180 SCL | 22          | I2C SCL    |
| Ejection Servo     | 25          | Servo PWM  |

### Radio Configuration

- Frequency: 433 MHz
- Spreading Factor: 7
- Bandwidth: 125 kHz
- Coding Rate: 4/5
- CRC enabled

---

## Telemetry Packet Format

Expected JSON from the ground station:

```json
{
  "alt": 0,
  "ax": 0.78,
  "ay": -0.18,
  "az": 0.25,
  "gx": 0.54,
  "gy": 0.52,
  "gz": -2.33,
  "launched": false,
  "ejected": false,
  "sim": true,
  "ts": 1120641
}
```

| Field | Description |
| ----- | ----------- |
| `alt` | Altitude in meters |
| `ax`,`ay`,`az` | Accelerometer values in g |
| `gx`,`gy`,`gz` | Gyroscope values in °/s |
| `launched` | Launch detection flag |
| `ejected` | Parachute ejection flag |
| `sim` | Simulation mode flag |
| `ts` | Timestamp in milliseconds |

---

## Installation

### Prerequisites

- Python 3.8+
- pip

### Setup

1. Open a terminal in `d:\team vikram\telemetry_vikram-main`
2. Create the virtual environment:

```powershell
python -m venv .venv
```

3. Activate the environment:

```powershell
# Windows
.\.venv\Scripts\activate

# Linux/Mac
source .venv/bin/activate
```

> If PowerShell blocks execution, run:
>
> ```powershell
> Set-ExecutionPolicy Unrestricted -Scope CurrentUser
> ```

4. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

---

## Configuration

Edit `app.py` to set the correct COM port and baud rate:

```python
SERIAL_PORT = "COM3"
BAUD_RATE = 115200
```

### Finding the serial port

**Windows:** Open Device Manager, expand "Ports (COM & LPT)", and find the USB-serial device.

**Linux/Mac:**

```bash
ls /dev/tty*
```

---

## Run the Program and Server

### 1. Activate the virtual environment

```powershell
# Windows
.\.venv\Scripts\activate

# Linux/Mac
source .venv/bin/activate
```

### 2. Start the Flask server

```powershell
python app.py
```

### Optional: Run the website using npm

If you want to serve only the frontend as a static site, you can use an npm static server.

```powershell
# Install a simple static server globally
npm install -g http-server

# Serve the frontend from the template folder
cd template
http-server -p 8080
```

Then open:

- `http://localhost:8080`

> Note: The backend API is still served by Flask at `http://localhost:5000`.

### 3. Access the dashboard

Open your browser and go to:

- `http://localhost:5000`
- or `http://<your-ip>:5000` for network access

### 4. Verify the server is running

The terminal should show startup messages such as:

- `Database initialised:`
- `Serial thread started`
- `Dashboard  : http://localhost:5000`

---

## Running the Dashboard

1. Connect the ground receiver by USB.
2. Start the server:

```powershell
.\.venv\Scripts\python.exe app.py
```

3. Open the dashboard at:

- `http://localhost:5000`
- or `http://<your-ip>:5000` for network access

---

## API Endpoints

| Endpoint | Description |
| --- | --- |
| `GET /` | Dashboard page |
| `GET /telemetry` | Latest telemetry packet |
| `GET /telemetry/all` | All stored telemetry data |
| `GET /status` | Connection and AI status |
| `GET /ports` | Available COM ports |
| `GET /test-data` | Inject mock telemetry data |
| `GET /api/model-stl` | Serve 3D rocket model |
| `GET /db/stats` | Database statistics |
| `POST /db/clear` | Clear database data |
| `GET /api/commands/log` | Command history log |
| `GET /api/events` | Mission event log |
| `POST /api/events` | Create mission event |
| `GET /api/sessions` | List flight sessions |
| `GET /api/sessions/<id>/export` | Export session CSV |
| `GET /api/anomalies` | Anomaly history |
| `GET /api/link-margin` | Link margin metrics |
| `POST /api/port` | Switch active serial port |
---

## Verification Checklist

### Pre-flight

- Activate `.venv`
- Run `python app.py`
- Confirm backend starts and database initializes

### Data verification

- Use `GET /test-data` if no hardware is connected
- Confirm the dashboard updates charts and telemetry values
- Confirm packet count increments

### Hardware verification

- Rocket TX powered and transmitting
- Receiver RX powered and connected by USB
- Serial logs show JSON packets
- Dashboard status shows connected

### API verification

Use these commands:

```bash
curl http://localhost:5000/telemetry
curl http://localhost:5000/telemetry/all
curl http://localhost:5000/status
curl http://localhost:5000/db/stats
curl http://localhost:5000/ports
```

Expected results:

- `connected: true`
- `serial_connected: true`
- `packets_received` increasing
- `flight_phase` detected

### Data export

```bash
curl http://localhost:5000/db/export -o telemetry_export.csv
```

### Troubleshooting

- If no data appears, verify serial port and JSON packet format.
- If the dashboard shows disconnected, check COM port and baud rate.
- If PowerShell blocks activation, run `Set-ExecutionPolicy Unrestricted -Scope CurrentUser`.
- If the 3D model fails, use the procedural fallback or add `Retro_Rocket.STL` to the project root.

---

## Implementation Summary

The project now includes:

- New backend endpoints for command log, mission events, sessions, anomalies, link metrics, and port switching
- Database schema upgrades for mission events, anomaly events, phase transitions, session tracking, and enhanced commands
- Flight detection improvements for pressure-based apogee, descent rate validation, and reset detection
- Persistent anomaly logging and automatic event persistence
- Frontend updates for command history, event log, anomaly timeline, and live link metrics

---

## Project Structure

```
telemetry_vikram-main/
├── app.py
├── requirements.txt
├── telemetry.db
├── telemetry_export.csv
├── .env
├── README.md
├── README_SETUP.md
├── PDR_REQUIREMENTS.md
├── PDR_UPGRADE_LIST.md
├── template/
│   └── index.html
├── Retro_Rocket.STL
├── hardware code/
├── image/
├── simulated output/
└── .venv/
```

> Note: The above file list includes legacy documentation files that have been consolidated into this README.

# CAN-7USAT Ground Station Telemetry Dashboard

**Team Vikram · CanSat India 2026**

A complete ground station telemetry system for CAN-7USAT. The system receives flight data over LoRa 433 MHz via an ESP32-based ground receiver and streams it to a live web dashboard backed by Flask and SQLite.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Features](#features)
3. [Hardware Setup](#hardware-setup)
   - [Ground Station Receiver (RX)](#ground-station-receiver-rx)
   - [Rocket Transmitter (TX)](#rocket-transmitter-tx)
   - [Radio Configuration](#radio-configuration)
4. [Telemetry Packet Format](#telemetry-packet-format)
5. [Installation](#installation)
6. [Configuration](#configuration)
7. [Running the Server](#running-the-server)
8. [Dashboard Features](#dashboard-features)
9. [API Reference](#api-reference)
10. [Verification & Troubleshooting](#verification--troubleshooting)
11. [Project Structure](#project-structure)

---

## Architecture

```
[CanSat TX]  ──LoRa 433 MHz──►  [ESP32 RX Ground Station]  ──USB/Serial──►  [Flask Backend]  ──HTTP──►  [Web Dashboard]
```

The Flask backend reads JSON telemetry packets from the serial port, persists them to SQLite, and exposes a REST API consumed by the frontend. The frontend uses vanilla JavaScript with Chart.js, Three.js, and Leaflet — no build step required.

---

## Features

### Telemetry & Charts
- Live altitude, temperature, pressure, and humidity plots
- Accelerometer (ax, ay, az) and gyroscope (gx, gy, gz) charts
- Descent rate plot with parachute deployment markers
- Temperature vs time and pressure vs altitude overlays
- Dashboard auto-refreshes at ≥ 1 Hz

### Navigation & Link
- GPS map panel with live ground track (Leaflet.js)
- Battery voltage gauge
- RSSI and SNR link quality indicators
- Link budget and packet success rate metrics
- Dynamic COM port selector with connection health display

### Mission Management
- Flight phase detection (pre-launch → boost → apogee → descent → recovery)
- Phase transition logging to database
- Mission event log with manual event creation
- Packet loss detection and CanSat reset detection
- Mission elapsed time clock and session grouping

### 3D Visualization
- Three.js rocket model (STL or procedural fallback)
- Live orientation from gyroscope data

### AI & Anomaly System
- AI-assisted telemetry analysis (Claude API)
- Anomaly detection with persistent anomaly history timeline
- Pressure-based apogee detection and descent rate validation

### Data & Control
- SQLite persistent storage with per-session grouping
- Per-session CSV export with metadata
- Uplink command panel: ARM / DEPLOY / ABORT / SIM
- Command acknowledgement display and command history log
- Mock-data mode for development without hardware

---

## Hardware Setup

### Ground Station Receiver (RX)

| Signal | ESP32 Pin | LoRa Module Pin |
|--------|-----------|-----------------|
| SCK    | 18        | SCK             |
| MISO   | 19        | MISO            |
| MOSI   | 23        | MOSI            |
| CS     | 5         | NSS             |
| RESET  | 14        | RST             |
| IRQ    | 26        | DIO0            |
| Power  | 3.3 V     | VCC             |
| Ground | GND       | GND             |

Connect the ESP32 to your PC via Micro-USB. The module enumerates as a USB-serial device (e.g., `COM3` on Windows, `/dev/ttyUSB0` on Linux).

### Rocket Transmitter (TX)

| Signal             | ESP32 Pin | Peripheral     |
|--------------------|-----------|----------------|
| LoRa SCK           | 18        | LoRa SCK       |
| LoRa MISO          | 19        | LoRa MISO      |
| LoRa MOSI          | 23        | LoRa MOSI      |
| LoRa CS            | 5         | LoRa NSS       |
| LoRa RST           | 14        | LoRa RST       |
| LoRa IRQ           | 26        | LoRa DIO0      |
| I2C SDA            | 21        | MPU6050 / BMP180 SDA |
| I2C SCL            | 22        | MPU6050 / BMP180 SCL |
| Ejection Servo PWM | 25        | Servo Signal   |

### Radio Configuration

| Parameter       | Value   |
|-----------------|---------|
| Frequency       | 433 MHz |
| Spreading Factor| 7       |
| Bandwidth       | 125 kHz |
| Coding Rate     | 4/5     |
| CRC             | Enabled |

> Both the TX and RX modules **must** use identical radio parameters or packets will be dropped silently.

---

## Telemetry Packet Format

The ground station transmits one JSON object per packet over serial. The current base schema is below; the expanded schema (v2) adds all PDR-required fields.

### Base Schema (v1)

```json
{
  "alt":      142.3,
  "ax":       0.78,
  "ay":      -0.18,
  "az":       0.25,
  "gx":       0.54,
  "gy":       0.52,
  "gz":      -2.33,
  "launched": false,
  "ejected":  false,
  "sim":      true,
  "ts":       1120641
}
```

### Expanded Schema (v2 — recommended)

```json
{
  "packet_id":    42,
  "mission_time": 97430,
  "ts":           1120641,

  "alt":   142.3,
  "temp":  28.5,
  "pres":  99812.4,
  "hum":   54.2,

  "ax":  0.78,  "ay": -0.18,  "az": 0.25,
  "gx":  0.54,  "gy":  0.52,  "gz": -2.33,

  "lat":  12.9716,
  "lon":  80.2437,

  "batt_v":  3.91,
  "rssi":   -87,
  "snr":      9.25,

  "launched": false,
  "ejected":  false,
  "sim":      true
}
```

| Field         | Type    | Unit      | Description                          |
|---------------|---------|-----------|--------------------------------------|
| `packet_id`   | int     | —         | Monotonically increasing packet counter |
| `mission_time`| int     | ms        | Time since mission arm                |
| `ts`          | int     | ms        | ESP32 millis() timestamp             |
| `alt`         | float   | m         | Altitude (BMP180/BMP280 barometric)  |
| `temp`        | float   | °C        | Ambient temperature                  |
| `pres`        | float   | Pa        | Barometric pressure                  |
| `hum`         | float   | %RH       | Relative humidity (DHT11/SHT31)      |
| `ax/ay/az`    | float   | g         | Accelerometer axes                   |
| `gx/gy/gz`    | float   | °/s       | Gyroscope axes                       |
| `lat` / `lon` | float   | °         | GPS coordinates (NEO-7M)             |
| `batt_v`      | float   | V         | Battery voltage (INA219)             |
| `rssi`        | int     | dBm       | Received Signal Strength Indicator   |
| `snr`         | float   | dB        | Signal-to-Noise Ratio                |
| `launched`    | bool    | —         | Launch detection flag                |
| `ejected`     | bool    | —         | Parachute ejection flag              |
| `sim`         | bool    | —         | Simulation mode active               |

---

## Installation

### Prerequisites

- Python 3.8 or newer
- pip
- (Optional) Node.js + npm for the static frontend server

### Step 1 — Clone / Navigate to the project

```powershell
cd "d:\team vikram\telemetry_vikram-main"
```

### Step 2 — Create a virtual environment

```powershell
python -m venv .venv
```

### Step 3 — Activate the virtual environment

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# Windows CMD
.\.venv\Scripts\activate.bat

# Linux / macOS
source .venv/bin/activate
```

> **PowerShell execution policy error?** Run this once, then retry:
> ```powershell
> Set-ExecutionPolicy Unrestricted -Scope CurrentUser
> ```

### Step 4 — Install dependencies

```powershell
python -m pip install -r requirements.txt
```

Your prompt should now show `(.venv)` to confirm activation.

---

## Configuration

Open `app.py` and set the serial port and baud rate for your ground receiver:

```python
SERIAL_PORT = "COM3"    # Windows example — adjust to your port
BAUD_RATE   = 115200
```

### Finding the correct serial port

**Windows:** Open Device Manager → Ports (COM & LPT) → identify the USB-to-serial adapter.

**Linux / macOS:**
```bash
ls /dev/tty*
# Common values: /dev/ttyUSB0, /dev/ttyACM0
```

You can also use the `/ports` API endpoint once the server is running to list available ports programmatically.

---

## Running the Server

### 1. Activate the virtual environment (if not already active)

```powershell
.\.venv\Scripts\Activate.ps1
```

### 2. Start Flask

```powershell
python app.py
```

Expected terminal output on successful start:

```
Database initialised: telemetry.db
Serial thread started on COM3 @ 115200
Dashboard  : http://localhost:5000
Network    : http://<your-ip>:5000
```

### 3. Open the dashboard

Navigate to `http://localhost:5000` in your browser. For network access from another device on the same LAN, use `http://<your-ip>:5000`.

### Optional — Serve the frontend with npm

If you want to run only the static frontend (no backend):

```powershell
npm install -g http-server
cd template
http-server -p 8080
```

Then open `http://localhost:8080`. Note: all API calls still require the Flask backend at port 5000.

---

## Dashboard Features

### Panels

| Panel | Description |
|-------|-------------|
| **Telemetry Overview** | Live values for altitude, temperature, pressure, humidity, battery, RSSI, SNR |
| **IMU Charts** | Real-time accelerometer and gyroscope plots |
| **Altitude / Descent** | Altitude over time with descent rate and parachute marker |
| **GPS Map** | Live ground track on a Leaflet map with GPS fix status |
| **3D Model** | Rocket orientation rendered from live gyro data (Three.js) |
| **Link Diagnostics** | RSSI, SNR, packet loss %, link budget estimation |
| **Mission Events** | Timestamped event log; supports manual event creation |
| **Command History** | Log of all uplink commands and their acknowledgements |
| **Anomaly Timeline** | Persistent history of detected telemetry anomalies |
| **Session Manager** | List flight sessions, view statistics, export CSV |

### Uplink Commands

Send commands to the CanSat via the command panel:

| Command  | Description                         |
|----------|-------------------------------------|
| `ARM`    | Arm the ejection mechanism          |
| `DEPLOY` | Manually trigger parachute ejection |
| `ABORT`  | Safe all systems                    |
| `SIM`    | Toggle simulation mode              |

---

## API Reference

All endpoints are served by Flask at `http://localhost:5000`.

---

### `GET /`

Serves the main dashboard HTML page.

---

### `GET /telemetry`

Returns the most recent telemetry packet.

**Response:**
```json
{
  "packet_id": 204,
  "mission_time": 97430,
  "alt": 142.3,
  "temp": 28.5,
  "pres": 99812.4,
  "hum": 54.2,
  "ax": 0.78, "ay": -0.18, "az": 0.25,
  "gx": 0.54, "gy": 0.52, "gz": -2.33,
  "lat": 12.9716, "lon": 80.2437,
  "batt_v": 3.91,
  "rssi": -87, "snr": 9.25,
  "launched": true,
  "ejected": false,
  "sim": false,
  "ts": 1120641
}
```

---

### `GET /telemetry/all`

Returns all stored telemetry packets for the current session as a JSON array.

---

### `GET /status`

Returns backend and connection health.

**Response:**
```json
{
  "connected": true,
  "serial_connected": true,
  "serial_port": "COM3",
  "baud_rate": 115200,
  "packets_received": 204,
  "packets_lost": 2,
  "flight_phase": "descent",
  "ai_enabled": true,
  "uptime_s": 312
}
```

---

### `GET /ports`

Returns a list of available serial ports on the host machine.

**Response:**
```json
{
  "ports": ["COM3", "COM7"],
  "active": "COM3"
}
```

---

### `POST /api/port`

Switches the active serial port without restarting the server.

**Request body:**
```json
{ "port": "COM7" }
```

**Response:**
```json
{ "success": true, "port": "COM7" }
```

---

### `GET /test-data`

Injects a single mock telemetry packet. Use this during development when no hardware is connected. Returns the injected packet.

---

### `GET /api/model-stl`

Serves the `Retro_Rocket.STL` file for Three.js rendering. Falls back to procedural geometry if the file is absent.

---

### `GET /db/stats`

Returns database statistics.

**Response:**
```json
{
  "total_packets": 1024,
  "sessions": 3,
  "anomalies": 7,
  "events": 12,
  "db_size_kb": 248
}
```

---

### `POST /db/clear`

Clears all telemetry data from the database. Prompts confirmation in the dashboard before calling this endpoint.

**Response:**
```json
{ "success": true, "rows_deleted": 1024 }
```

---

### `GET /api/commands/log`

Returns the full uplink command history.

**Response:**
```json
[
  {
    "id": 1,
    "command": "ARM",
    "sent_at": "2026-06-01T09:14:22Z",
    "ack": true,
    "ack_at": "2026-06-01T09:14:23Z"
  }
]
```

---

### `GET /api/events`

Returns all mission events.

**Response:**
```json
[
  {
    "id": 1,
    "type": "PHASE_TRANSITION",
    "description": "Entered descent phase",
    "mission_time": 97430,
    "created_at": "2026-06-01T09:16:04Z"
  }
]
```

---

### `POST /api/events`

Creates a manual mission event.

**Request body:**
```json
{
  "type": "NOTE",
  "description": "Visual parachute sighted",
  "mission_time": 105200
}
```

**Response:**
```json
{ "success": true, "id": 8 }
```

---

### `GET /api/sessions`

Returns all recorded flight sessions with summary statistics.

**Response:**
```json
[
  {
    "id": 1,
    "started_at": "2026-06-01T09:10:00Z",
    "ended_at": "2026-06-01T09:22:14Z",
    "packets": 720,
    "max_alt_m": 312.4,
    "landed": true
  }
]
```

---

### `GET /api/sessions/<id>/export`

Downloads a CSV file of all telemetry for the given session, including metadata header rows.

**Example:**
```
GET /api/sessions/1/export
→ Content-Disposition: attachment; filename="session_1_20260601.csv"
```

---

### `GET /api/anomalies`

Returns the persistent anomaly history.

**Response:**
```json
[
  {
    "id": 3,
    "field": "batt_v",
    "value": 3.1,
    "threshold": 3.3,
    "severity": "warning",
    "mission_time": 87210,
    "detected_at": "2026-06-01T09:15:41Z"
  }
]
```

---

### `GET /api/link-margin`

Returns current link budget and packet success metrics.

**Response:**
```json
{
  "rssi_dbm": -87,
  "snr_db": 9.25,
  "link_margin_db": 12.5,
  "packet_success_rate": 0.991,
  "packets_received": 204,
  "packets_expected": 206
}
```

---

## Verification & Troubleshooting

### Pre-flight Checklist

- [ ] Virtual environment is active (`(.venv)` visible in terminal)
- [ ] `python app.py` starts without errors
- [ ] Terminal shows `Database initialised` and `Serial thread started`
- [ ] Dashboard loads at `http://localhost:5000`
- [ ] Packet counter increments (or use `/test-data` to confirm)

### Quick API Checks

Run these from another terminal to verify the backend is responding:

```bash
curl http://localhost:5000/status
curl http://localhost:5000/telemetry
curl http://localhost:5000/db/stats
curl http://localhost:5000/ports
```

Inject test data if no hardware is connected:

```bash
curl http://localhost:5000/test-data
```

Export telemetry to CSV:

```bash
curl http://localhost:5000/api/sessions/1/export -o session_1.csv
```

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Dashboard shows "Disconnected" | Wrong COM port or baud rate | Update `SERIAL_PORT` in `app.py`; verify in Device Manager |
| No data appears but server starts | Packet format mismatch | Check ESP32 firmware outputs valid JSON; use `/test-data` to confirm dashboard works |
| PowerShell blocks `.venv` activation | Execution policy restricted | Run `Set-ExecutionPolicy Unrestricted -Scope CurrentUser` |
| 3D model does not load | `Retro_Rocket.STL` missing | Place the STL in the project root, or the dashboard falls back to a procedural model |
| `packets_lost` increases rapidly | LoRa interference or range | Check antenna connections; reduce spreading factor or increase TX power |
| GPS shows no fix | Insufficient satellite lock | Ensure the CanSat is outdoors with clear sky view before arming |

---

## Project Structure

```
telemetry_vikram-main/
├── app.py                   # Flask backend — serial, DB, API
├── requirements.txt         # Python dependencies
├── .env                     # Environment variables (API keys etc.)
├── telemetry.db             # SQLite database (auto-created)
├── telemetry_export.csv     # Last manual export
├── Retro_Rocket.STL         # 3D model for Three.js
├── README.md                # This file
├── template/
│   └── index.html           # Single-page dashboard (vanilla JS)
├── hardware code/           # ESP32 firmware (TX and RX)
├── image/                   # Screenshots and assets
├── simulated output/        # Sample telemetry recordings
└── .venv/                   # Python virtual environment (not committed)
```

---

## Dependencies

### Python (`requirements.txt`)

| Package       | Purpose                          |
|---------------|----------------------------------|
| `flask`       | Web server and REST API          |
| `pyserial`    | Serial port communication        |
| `anthropic`   | AI telemetry analysis (optional) |

### Frontend (CDN — no install required)

| Library      | Version | Purpose                        |
|--------------|---------|--------------------------------|
| Chart.js     | 4.x     | Real-time telemetry charts     |
| Three.js     | r128    | 3D rocket visualization        |
| Leaflet.js   | 1.9.x   | GPS map and ground track       |

---

*CAN-7USAT · Team Vikram · CanSat India 2026*

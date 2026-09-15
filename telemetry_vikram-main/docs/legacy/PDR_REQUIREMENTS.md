# 📋 CAN-7USAT PDR — Website Requirements Document

> **Project:** CAN-7USAT India 2026 | **Review Stage:** Preliminary Design Review (PDR)
> **Document:** Complete requirements for the Ground Station Web Application
> **Version:** 2.0 | **Last Updated:** 2026-05-12

---

## 1. SYSTEM OVERVIEW

The CAN-7USAT Ground Station Web Application is a **real-time telemetry monitoring and command interface** that runs on a laptop/PC at the launch site. It receives data from the CanSat via LoRa (433 MHz) through a USB-connected ground station receiver (ESP32 + LoRa).

### Architecture
```
[CanSat TX] → LoRa 433 MHz → [ESP32 RX Ground Station] → USB/Serial → [Flask Backend] → [Web Dashboard]
```

---

## 2. FUNCTIONAL REQUIREMENTS

### 2.1 Real-Time Telemetry Display

| Req ID | Requirement | Priority |
|--------|-------------|----------|
| FR-01 | Display live altitude (meters) with chart | MUST |
| FR-02 | Display accelerometer (ax, ay, az in g) with chart | MUST |
| FR-03 | Display gyroscope (gx, gy, gz in °/s) with chart | MUST |
| FR-04 | Display temperature (°C) with chart | MUST |
| FR-05 | Display atmospheric pressure (hPa) with chart | MUST |
| FR-06 | Display humidity (%) | SHOULD |
| FR-07 | Display GPS latitude and longitude | MUST |
| FR-08 | Display battery voltage (V) | MUST |
| FR-09 | Display RSSI (dBm) and SNR (dB) from LoRa link | MUST |
| FR-10 | Display packet counter and mission elapsed time | MUST |
| FR-11 | All values auto-refresh at ≥ 1 Hz without page reload | MUST |

### 2.2 Live GPS Map

| Req ID | Requirement | Priority |
|--------|-------------|----------|
| FR-12 | Display real-time CanSat position on a map (Leaflet.js + OpenStreetMap) | MUST |
| FR-13 | Draw ground track path as CanSat moves | MUST |
| FR-14 | Mark launch point, apogee point, and landing point on map | MUST |
| FR-15 | Display distance from ground station on map | SHOULD |

### 2.3 Real-Time Charts / Plots

| Req ID | Requirement | Priority |
|--------|-------------|----------|
| FR-16 | Altitude vs Time chart (scrolling, last N seconds) | MUST |
| FR-17 | Vertical velocity / descent rate vs Time chart | MUST |
| FR-18 | 3-axis accelerometer chart | MUST |
| FR-19 | 3-axis gyroscope chart | MUST |
| FR-20 | Temperature vs Time chart | MUST |
| FR-21 | Pressure vs Altitude dual-axis chart | SHOULD |
| FR-22 | RSSI vs Time chart | SHOULD |
| FR-23 | Highlight phase transition events on charts | MUST |
| FR-24 | Highlight parachute deployment event on altitude chart | MUST |

### 2.4 Flight Phase & State Machine Display

| Req ID | Requirement | Priority |
|--------|-------------|----------|
| FR-25 | Display current flight phase (PRE-LAUNCH, IGNITION, POWERED_ASCENT, COAST, APOGEE, DESCENT, RECOVERY) | MUST |
| FR-26 | Display flight software FSM state from CanSat | MUST |
| FR-27 | Log and display phase transition events with timestamps | MUST |
| FR-28 | Display CanSat boot time and uptime | SHOULD |
| FR-29 | Detect and alert on CanSat reset events | SHOULD |

### 2.5 Mission Event Log

| Req ID | Requirement | Priority |
|--------|-------------|----------|
| FR-30 | Display scrollable mission event log panel | MUST |
| FR-31 | Auto-log timestamped events: Connect, Launch, Apogee, Ejection, Land | MUST |
| FR-32 | Operator can manually add event notes | SHOULD |

### 2.6 Command & Control (Uplink)

| Req ID | Requirement | Priority |
|--------|-------------|----------|
| FR-33 | ARM command button (enable CanSat flight mode) | MUST |
| FR-34 | DEPLOY command button (trigger parachute manually) | MUST |
| FR-35 | SIM-MODE toggle command | SHOULD |
| FR-36 | ABORT command with confirmation dialog | MUST |
| FR-37 | Display command acknowledgement from CanSat | MUST |
| FR-38 | Log all uplink commands with timestamps | MUST |

### 2.7 AI Analysis Panel

| Req ID | Requirement | Priority |
|--------|-------------|----------|
| FR-39 | Display auto-refreshing AI (Gemini) flight analysis | MUST |
| FR-40 | Show AI analysis phase, anomaly count, and provider | MUST |
| FR-41 | Display anomaly timeline — all past anomalies with timestamps | MUST |
| FR-42 | AI prompt includes temperature, pressure, GPS data | MUST |
| FR-43 | Offline fallback analysis when Gemini unavailable | MUST |

### 2.8 Data Storage & Export

| Req ID | Requirement | Priority |
|--------|-------------|----------|
| FR-44 | Store all telemetry to SQLite DB with session_id | MUST |
| FR-45 | Export full flight as CSV with metadata header | MUST |
| FR-46 | Auto-trigger CSV export when ejection event detected | SHOULD |
| FR-47 | Support per-session export (filter by flight session) | SHOULD |
| FR-48 | Support time-range query for partial export | SHOULD |

### 2.9 Connection Management

| Req ID | Requirement | Priority |
|--------|-------------|----------|
| FR-49 | Auto-detect and list available COM ports | MUST |
| FR-50 | Dynamic COM port selector in the UI | MUST |
| FR-51 | Display connection status with color indicator | MUST |
| FR-52 | Display packet loss percentage in real time | MUST |
| FR-53 | Auto-reconnect on serial disconnect | MUST |

### 2.10 Link Margin & Communication Diagnostics

| Req ID | Requirement | Priority |
|--------|-------------|----------|
| FR-54 | Display RSSI, SNR values live | MUST |
| FR-55 | Calculate and display link margin (dB) | MUST |
| FR-56 | Log RSSI/SNR vs distance for range test mode | SHOULD |
| FR-57 | Display packet success rate (%) | MUST |

---

## 3. NON-FUNCTIONAL REQUIREMENTS

| Req ID | Requirement |
|--------|-------------|
| NFR-01 | Dashboard must refresh data at **≥ 1 Hz** (real-time feel) |
| NFR-02 | UI must load within **< 3 seconds** on localhost |
| NFR-03 | Must work in **offline mode** (no internet required at launch site) |
| NFR-04 | Must run on **Windows 10/11** (team laptops) |
| NFR-05 | Must support screen resolution **1280×720 minimum** |
| NFR-06 | Dark-themed UI for **sunlight readability** at outdoor launch site |
| NFR-07 | All API endpoints must respond within **500ms** |
| NFR-08 | Database must not grow beyond **500 MB** per session |
| NFR-09 | Rate limiting applied to AI endpoint (max 10 req/min per IP) |
| NFR-10 | Must be **responsive** for tablet/mobile viewing |

---

## 4. WEBSITE TECHNOLOGY STACK

| Layer | Technology | Reason |
|-------|-----------|--------|
| Backend | Python Flask | Existing, lightweight, serial I/O support |
| Frontend | HTML5, Vanilla CSS, Vanilla JS | No framework dependency, offline-safe |
| Charts | Chart.js | Lightweight, real-time capable |
| 3D Model | Three.js (STL loader) | Existing 3D rocket visualization |
| Maps | Leaflet.js + OpenStreetMap | Offline tiles, no API key needed |
| Database | SQLite3 | Zero-dependency, file-based |
| AI | Google Gemini 2.0 Flash | Existing integration |
| Serial | PySerial | Existing |

---

## 5. API ENDPOINTS — REQUIRED

### Existing (Must Keep)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Main dashboard HTML |
| GET | `/telemetry` | Latest packet (JSON) |
| GET | `/telemetry/all` | All in-memory packets |
| GET | `/telemetry/range` | Time-range query |
| GET | `/status` | Connection + AI status |
| GET | `/ports` | List COM ports |
| GET | `/test-data` | Inject test packets |
| GET | `/db/stats` | DB statistics |
| GET | `/db/export` | Download CSV |
| POST | `/db/clear` | Clear database |
| GET | `/api/ai-analysis` | On-demand AI (rate limited) |
| GET | `/api/ai-analysis/latest` | Cached AI result |
| GET | `/api/model-stl` | Serve 3D model |

### New (Must Add for PDR v2)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/command` | Send uplink command to CanSat |
| GET | `/api/commands/log` | Command history log |
| GET | `/api/events` | Mission event log |
| POST | `/api/events` | Add manual mission event |
| GET | `/api/sessions` | List all flight sessions |
| GET | `/api/sessions/<id>/export` | Export specific session CSV |
| GET | `/api/anomalies` | Full anomaly history |
| GET | `/api/link-margin` | RSSI/SNR/link margin stats |
| POST | `/api/port` | Change active serial port |

---

## 6. DATABASE SCHEMA — REQUIRED (v2)

```sql
CREATE TABLE telemetry (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT,
    packet_id    INTEGER,
    timestamp    INTEGER,        -- CanSat ms timestamp
    mission_time REAL,           -- seconds since launch
    altitude     REAL,           -- meters
    ax REAL, ay REAL, az REAL,   -- accel in g
    gx REAL, gy REAL, gz REAL,   -- gyro in °/s
    temperature  REAL,           -- °C
    pressure     REAL,           -- hPa
    humidity     REAL,           -- %
    lat          REAL,           -- GPS latitude
    lon          REAL,           -- GPS longitude
    batt_v       REAL,           -- battery voltage V
    rssi         REAL,           -- dBm
    snr          REAL,           -- dB
    flight_phase TEXT,           -- detected phase
    launched     BOOLEAN,
    ejected      BOOLEAN,
    sim          BOOLEAN,
    raw_json     TEXT,
    received_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE mission_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    event_type  TEXT,           -- CONNECT, LAUNCH, APOGEE, EJECTION, LAND, MANUAL
    event_note  TEXT,
    packet_id   INTEGER,
    altitude    REAL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    command     TEXT,           -- ARM, DEPLOY, ABORT, SIM_ON, SIM_OFF
    status      TEXT,           -- SENT, ACKNOWLEDGED, FAILED
    sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    acked_at    DATETIME
);
```

---

## 7. TELEMETRY PACKET FORMAT — REQUIRED (v2)

```json
{
  "packet_id": 1042,
  "ts": 858900,
  "alt": 120.5,
  "ax": 0.03, "ay": 0.008, "az": 1.10,
  "gx": 0.12, "gy": 0.22, "gz": 0.04,
  "temp": 28.4,
  "pressure": 989.2,
  "humidity": 45.1,
  "lat": 12.9716,
  "lon": 77.5946,
  "batt_v": 3.85,
  "rssi": -78,
  "snr": 9.5,
  "launched": true,
  "ejected": false,
  "sim": false
}
```

---

## 8. DASHBOARD UI PANELS — REQUIRED LAYOUT

```
┌─────────────────────────────────────────────────────────────┐
│  HEADER: Mission Name | Phase Badge | Clock | Status Bar     │
├──────────────┬──────────────────┬────────────────────────────┤
│  SENSOR GRID │  ALTITUDE CHART  │  GPS MAP                   │
│  alt / temp  │  (scrolling)     │  (Leaflet live track)      │
│  pres / hum  │                  │                            │
│  batt / rssi ├──────────────────┤                            │
│  GPS lat/lon │  DESCENT RATE    │                            │
├──────────────┼──────────────────┼────────────────────────────┤
│  ACCEL CHART │  GYRO CHART      │  3D ROCKET MODEL           │
│              │                  │  (Three.js STL)            │
├──────────────┼──────────────────┼────────────────────────────┤
│  FSM STATE   │  AI ANALYSIS     │  MISSION EVENT LOG         │
│  PANEL       │  PANEL           │  (scrollable)              │
├──────────────┴──────────────────┴────────────────────────────┤
│  COMMAND BAR: [ARM] [DEPLOY] [ABORT] [SIM] [COM PORT ▼]      │
│  STATUS BAR: Packets | Loss% | Link Margin | Session Time     │
└─────────────────────────────────────────────────────────────┘
```

---

## 9. COMMUNICATION TEST PLAN — WEBSITE REQUIREMENTS

| Test | What to Log | Where to Display |
|------|-------------|-----------------|
| Range test | RSSI, SNR, distance, packet_id | Link Margin panel |
| Packet loss | Missing packet_ids | Status bar + chart |
| Boot time | First `ts` received after power-on | Mission event log |
| Reconnect | Serial disconnect/reconnect events | Mission event log |
| Telemetry latency | Received_at vs ts timestamp delta | Status bar |

---

## 10. GROUND STATION SETUP REQUIREMENTS

| Req ID | Requirement |
|--------|-------------|
| GS-01 | Application must auto-detect COM ports on launch |
| GS-02 | Must work without internet (offline maps, no CDN) |
| GS-03 | Must support **elevated antenna mount** (USB cable extension ≥ 5m) |
| GS-04 | Must support LAN streaming so a second laptop can monitor |
| GS-05 | Must log to local disk in case of browser crash |
| GS-06 | Must display **contact lost** alert if no packet received in 5 seconds |

---

## 11. TESTING REQUIREMENTS

| Test ID | Test | Expected Result |
|---------|------|----------------|
| T-01 | Load `/test-data` then verify all UI panels update | All 14+ sensor fields show values |
| T-02 | Verify altitude chart shows correct flight profile | Chart matches injected test packets |
| T-03 | Send ARM command and verify event logged | Command appears in commands log |
| T-04 | Disconnect serial, verify alert appears within 5s | "Contact Lost" alert shown |
| T-05 | Export CSV and verify all columns present | 20+ column CSV downloaded |
| T-06 | Run for 30 min, verify DB < 500 MB | DB size within limits |
| T-07 | Inject anomaly packet (lateral G > 2), verify AI flags it | Anomaly appears in log |
| T-08 | Verify GPS map updates in real time | Map marker moves with test coordinates |

---

*Document generated for CAN-7USAT PDR — Ground Station Web Application v2.0*
*Team Vikram | India 2026 CanSat Competition*

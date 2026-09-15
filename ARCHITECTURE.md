# CanSat Telemetry System Architecture

## System Overview
End-to-end data pipeline from rocket transmitter → ground station receiver → Flask backend → web dashboard.

---

## 1. ROCKET TRANSMITTER (Hardware)
**File:** `hardware code/rocket_tx/rocket_tx.ino`

### Sensors
- **BMP180**: Barometric pressure sensor (altitude)
- **MPU6050**: 6-axis IMU (3-axis accelerometer + 3-axis gyroscope)
- **Servo**: Ejection charge control

### Data Collection (300ms intervals)
```
- Altitude (m) from barometric pressure
- Acceleration (g): ax, ay, az
- Rotation rate (°/s): gx, gy, gz
- Flight state: launched, ejected flags
- Timestamp: millis() in ms
- Sim flag: simulation mode indicator
```

### Flight Logic
1. **Launch Detection**: Net acceleration > 3.0g → `launched = true`
2. **Apogee Detection**: Altitude decreasing for 3 consecutive packets → triggers ejection
3. **Ejection**: Servo rotates to 90° to fire parachute charge

### Data Format (JSON)
```json
{
  "alt": 123.45,    // meters AGL
  "ax": 0.050,      // g (gravity units)
  "ay": -0.020,     
  "az": 2.500,      
  "gx": 45.6,       // °/s (degrees per second)
  "gy": -12.3,
  "gz": 8.9,
  "launched": true,
  "ejected": false,
  "sim": false,     // false = real BMP180, true = simulated
  "ts": 15234       // milliseconds since boot
}
```

### LoRa Transmission
- **Frequency**: 433 MHz
- **Frame Format**: `<{json}>`
- **Parameters**: SF7, BW=125kHz, CR=4/5, CRC enabled
- **Interval**: 300ms

---

## 2. BASE STATION RECEIVER (Hardware)
**File:** `hardware code/recever(base station)/base_station/base_station.ino`

### LoRa Receiver
- Same LoRa pins and parameters as transmitter
- Continuously listens for packets
- Parses incoming data every ~300ms

### Frame Processing
```
Receive:   <{"alt":123.45,...}>
Extract:   {"alt":123.45,...}     (strip < >)
Print:     {"alt":123.45,...}     (to Serial @ 115200 baud)
```

### Serial Output (115200 baud)
- **JSON telemetry**: `{"alt":123.45,"ax":0.05,...}`
- **Signal quality**: `RSSI: -89 dBm | SNR: 8.5 dB`

---

## 3. FLASK BACKEND (Data Pipeline)
**File:** `app.py`

### Serial Connection
```python
Port:     COM3 (configurable via SERIAL_PORT env var)
Baud:     115200
Timeout:  1 second
```

### Data Reception (Daemon Thread)
```
serial_reader() thread:
  1. Waits for serial data
  2. Extracts JSON with regex: r'(\{.*\})'
  3. Validates packet structure
  4. Sanitizes floats (removes NaN, ±∞)
  5. Stores in-memory: flight_data (deque, max 5000 packets)
  6. Stores persistent: telemetry.db (SQLite)
```

### Packet Sanitization
- Float validation: NaN → 0.0, |value| > 1e9 → default
- Type checking: launched/ejected → boolean
- Range checking: altitude, accelerations within expected bounds

### Database Schema
```sql
CREATE TABLE telemetry (
    id          INTEGER PRIMARY KEY,
    timestamp   INTEGER,          -- ts from packet (ms)
    altitude    REAL,            -- alt (m)
    ax REAL, ay REAL, az REAL,   -- accelerations (g)
    gx REAL, gy REAL, gz REAL,   -- rotations (°/s)
    launched    BOOLEAN,
    ejected     BOOLEAN,
    sim         BOOLEAN,
    raw_json    TEXT,
    received_at DATETIME          -- server time
);

Indexes: timestamp, received_at, altitude
```

### Flight State Analysis
```python
_detect_flight_phase(recent_packets):
  Returns: PRE-LAUNCH | IGNITION | POWERED_ASCENT 
           | COAST | APOGEE | DESCENT | RECOVERY

_detect_anomalies(recent_packets):
  Returns: List of anomalies
    • HIGH LATERAL G: > 2.0g
    • UNEXPECTED ALTITUDE DROP: > 5m decrease
    • GYRO SPIKE: > 500°/s on any axis
    • AZ ≈ 0: free-fall or sensor failure
```

### AI Analysis Thread
- Runs every 15 seconds (configurable)
- Requires minimum 10 packets before running
- Uses Google Gemini 2.0 Flash for real-time analysis
- Falls back to rule-based analysis if API unavailable
- Caches result (30 second TTL)

### REST API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Serve dashboard (index.html) |
| `/telemetry` | GET | Latest packet |
| `/telemetry/all` | GET | All packets in memory |
| `/telemetry/range` | GET | Query by time range |
| `/status` | GET | System status (connected, packet count, port) |
| `/api/ai-analysis` | GET | On-demand AI analysis (rate limited) |
| `/api/ai-analysis/latest` | GET | Cached AI result |
| `/db/stats` | GET | Database statistics |
| `/db/export` | GET | Download CSV of all telemetry |
| `/db/clear` | POST | Clear database |
| `/ports` | GET | Available serial ports |
| `/test-data` | GET | Load 7 synthetic packets for testing |

### Rate Limiting
- 10 requests per 60 seconds per IP address
- Applies to `/api/ai-analysis` endpoint

---

## 4. WEB DASHBOARD (Frontend)
**File:** `template/index.html`

### Technology Stack
- **CSS Grid**: Responsive 3-column layout
- **Chart.js**: Real-time telemetry graphs
- **Three.js**: 3D rocket orientation visualization
- **Leaflet**: Interactive map with GPS trail
- **Vanilla JavaScript**: Event handling, AJAX requests

### Data Flow (Client-Side)
```
JavaScript Timer (every 1-2 seconds)
  ↓
Fetch /telemetry (latest packet)
  ↓
Update metrics, charts, map, 3D model
```

### Dashboard Tabs

#### 📊 Dashboard
- **Real-time metrics**: Altitude, velocity, acceleration, G-forces
- **Chart 1**: Acceleration magnitude (g) vs time
- **Chart 2**: Altitude (m) vs time
- **Chart 3**: Flight trajectory (3D path)
- **Status**: Serial connected, packets received, flight phase
- **Components health**: Sensor status, battery, comms quality

#### 🗺 Live Location
- **Map**: OpenStreetMap (Leaflet)
- **Marker**: Current GPS-interpolated position
- **Trail**: Dashed cyan polyline of flight path
- **Compass**: Heading indicator
- **Stats**: Lat/Lon, altitude, speed, satellites, HDOP
- **Distance**: Total flight distance

#### 🛰 3D Orientation
- **3D Model**: Rocket mesh (STL file)
- **Rotation**: Live pitch/roll/yaw from accelerometer
- **Reference frame**: Cardinal directions overlay

#### 🤖 AI Analysis
- **Cached result**: Latest AI analysis (Gemini or fallback)
- **Flight phase**: Detected phase with confidence
- **Anomalies**: List of flagged issues
- **Recommendations**: Action items for ground crew

#### 🔩 Components
- **Sensor grid**: Real-time status of all hardware
  - BMP180 (altitude)
  - MPU6050 (IMU)
  - Servo (ejection)
  - Comms (RSSI/SNR)

#### 📈 Simulation
- **Synthetic data loader**: Test dashboard without hardware
- **Images**: Pre-computed simulation frames (if available)

### API Calls From Frontend
```javascript
// Every 1 second:
fetch('/telemetry')
  .then(r => r.json())
  .then(data => {
    updateAltitude(data.alt);
    updateAccel(data.ax, data.ay, data.az);
    updateMap(data.lat, data.lon);
    updateOrientation(data.gx, data.gy, data.gz);
  });

// On-demand:
fetch('/api/ai-analysis')         // Fresh analysis
fetch('/api/ai-analysis/latest')  // Cached result
fetch('/db/stats')                // Database info
```

### Map Simulation
- Generates synthetic GPS coordinates
- Simulates altitude changes
- Animates flight path in real-time
- For testing without LoRa hardware

---

## 5. COMPLETE DATA FLOW DIAGRAM

```
ROCKET (Transmitter)
  ↓ LoRa 433MHz
  │ {"alt":123.45, "ax":0.05, ...}
  │ wrapped as: <{...}>
  ↓
BASE STATION (Receiver)
  ↓ Serial 115200 baud
  │ <{...}> → strip → {...}
  ↓
FLASK APP (Backend)
  ↓ Thread: read_serial()
  │ regex extract: (\{.*\})
  │ sanitize
  │ store to flight_data (in-memory)
  │ save to telemetry.db (persistent)
  ↓
REST API
  ├─ /telemetry (latest)
  ├─ /telemetry/all (all packets)
  ├─ /telemetry/range (by time)
  ├─ /api/ai-analysis (on-demand)
  ├─ /db/export (CSV download)
  └─ /status (system health)
  ↓
HTML DASHBOARD (Frontend)
  ├─ Charts (Chart.js)
  ├─ Map (Leaflet)
  ├─ 3D Model (Three.js)
  ├─ Metrics Display
  └─ AI Analysis Panel
```

---

## 6. ENVIRONMENT CONFIGURATION

### Required `.env` variables
```
# Serial Connection
SERIAL_PORT=COM3
BAUD_RATE=115200

# Database
MAX_FLIGHT_PACKETS=5000

# AI Analysis
GEMINI_API_KEY=your-api-key-here
AI_ANALYSIS_INTERVAL=15
AI_MIN_PACKETS=10

# Rate Limiting
RATE_LIMIT_MAX=10
RATE_LIMIT_WINDOW=60
```

### System Requirements
- Python 3.8+
- Flask, Flask-CORS
- PySerial
- SQLite3
- Google Generative AI SDK (Gemini)
- STM32/ESP32 boards with:
  - LoRa module (SX1278 @ 433MHz)
  - BMP180 barometer
  - MPU6050 IMU
  - Servo controller

---

## 7. STARTUP SEQUENCE

### Flask Backend
```bash
cd "d:\team vikram\telemetry_vikram-main"
source .venv/Scripts/Activate.ps1
python app.py
```

Expected console output:
```
✅ Database initialised
   Loaded X packets from DB
🔄 Serial thread started
📡 Continuous AI thread started
Dashboard  : http://localhost:5000
```

### Rocket Hardware
1. Power up ESP32 (rocket)
2. LoRa initializes, transmits test packet
3. Serial output: `🚀 ROCKET READY`

### Base Station Hardware
1. Power up ESP32 (ground station)
2. LoRa initializes
3. Serial output: `📡 Ground Station READY`
4. Connect via USB to PC

### Web Dashboard
1. Open browser: `http://localhost:5000`
2. Should show dashboard with connection status
3. Once receiver connects, real-time data appears

---

## 8. TROUBLESHOOTING

### No Data Appearing
1. **Check serial port**: Navigate to `/ports` endpoint
2. **Verify baud rate**: Should be 115200
3. **Check LoRa connection**: Look for RSSI/SNR output
4. **Test with synthetic data**: `http://localhost:5000/test-data`

### Map Not Updating
- JavaScript checks `mapReady` flag
- Requires valid lat/lon in telemetry
- Falls back to Sriperumbudur (12.9626, 79.9541) as default

### AI Analysis Not Running
- Check `GEMINI_API_KEY` in `.env`
- Check database has at least 10 packets
- Verify Gemini API account is active

### 3D Model Not Showing
- Ensure STL file exists: `Retro_Rocket.STL`
- Check `/api/model-stl` endpoint returns file
- Three.js rendering requires WebGL support

---

## 9. FILES SUMMARY

```
telemetry_vikram-main/
├── app.py                           ← Main Flask backend
├── template/
│   └── index.html                   ← Web dashboard (all JS, HTML, CSS)
├── hardware code/
│   ├── rocket_tx/
│   │   └── rocket_tx.ino           ← Rocket transmitter (sensor + LoRa)
│   └── recever(base station)/
│       └── base_station/
│           └── base_station.ino    ← Ground receiver (LoRa + Serial)
├── requirements.txt                 ← Python dependencies
├── telemetry.db                     ← SQLite database (auto-created)
├── .env                             ← Configuration (not in repo)
└── ARCHITECTURE.md                  ← This file
```

---

## 10. KEY DESIGN DECISIONS

| Component | Design Choice | Why |
|-----------|---------------|----|
| LoRa 433MHz | Frequency | License-free, long range (5+ km), low power |
| SF7 | Spreading factor | Balance between range and data rate |
| JSON | Data format | Human-readable, easy parsing, extensible |
| SQLite | Database | Lightweight, persistent, no server needed |
| Flask | Backend | Minimal dependency, perfect for embedded dashboards |
| Vanilla JS | Frontend | No build step, fast iteration, all in one HTML file |
| Gemini AI | Analysis | Real-time anomaly detection, adaptive recommendations |
| Three.js | 3D | WebGL rendering, easy model import from STL |

---

**Last Updated**: May 2026
**System Status**: ✅ All components linked and operational

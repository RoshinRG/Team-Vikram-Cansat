import pkgutil
import importlib.util
# pyrefly: ignore [missing-import]
from flask import Flask, jsonify, render_template, send_file, request, Response, stream_with_context, redirect
from flask_cors import CORS
import serial
import serial.tools.list_ports
import threading
import json
import math
import time
import os
import sqlite3
import csv
import io
import html as html_lib
from collections import deque
from functools import wraps
from dotenv import load_dotenv

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from flight_analysis import (
        PROVIDER as FLIGHT_ML_PROVIDER,
        build_report as build_flight_report,
        generate_pdf_bytes as build_flight_pdf,
        save_pdf as save_flight_pdf,
        FPDF as _FPDF,
    )
except ImportError:
    FLIGHT_ML_PROVIDER = "vikram-flight-ml"
    build_flight_report = None
    build_flight_pdf = None
    save_flight_pdf = None
    _FPDF = None

# ── Video-streaming deps (optional — server still runs without them) ──
try:
    import cv2 as _cv2
except ImportError:
    _cv2 = None

try:
    import requests as _requests
except ImportError:
    _requests = None

if not hasattr(pkgutil, "get_loader"):
    def _get_loader_compat(name):
        try:
            spec = importlib.util.find_spec(name)
            return spec.loader if spec is not None else None
        except (ValueError, AttributeError):
            # Handle __main__.__spec__ is None or other spec lookup errors
            return None
    pkgutil.get_loader = _get_loader_compat

# Load environment variables
load_dotenv()

# ================= CONFIG =================
SERIAL_PORT        = os.getenv("SERIAL_PORT", "COM3")
BAUD_RATE          = int(os.getenv("BAUD_RATE", "115200"))
DB_PATH            = os.path.join(os.path.dirname(__file__), "telemetry.db")
MAX_FLIGHT_PACKETS = int(os.getenv("MAX_FLIGHT_PACKETS", "5000"))

# Continuous AI analysis interval (seconds)
AI_ANALYSIS_INTERVAL = int(os.getenv("AI_ANALYSIS_INTERVAL", "15"))
# Minimum packets before AI fires
AI_MIN_PACKETS       = int(os.getenv("AI_MIN_PACKETS", "5"))

# ================= NEW HARDWARE THRESHOLDS =================
# Thresholds for deriving launched/ejected flags from new compact-format packets.
# GUESS: launched when acceleration magnitude > 3 g OR altitude > 5 m.
#        ejected  when relay pin goes HIGH after launch (relay=1 post-launch).
# Adjust after reviewing the CanSat circuit — relay usage may differ.
LAUNCH_ACCEL_THRESHOLD_G = float(os.getenv("LAUNCH_ACCEL_THRESHOLD_G", "3.0"))  # g
LAUNCH_ALT_THRESHOLD_M   = float(os.getenv("LAUNCH_ALT_THRESHOLD_M",   "5.0"))  # metres

# Receiver heartbeat / link timeouts (seconds)
RX_ONLINE_TIMEOUT_S  = float(os.getenv("RX_ONLINE_TIMEOUT_S",  "5.0"))
LINK_TIMEOUT_S       = float(os.getenv("LINK_TIMEOUT_S",       "10.0"))

# ================= WIFI RECEIVER STATE =================
# Tracks state for the new compact-format ("n" key) packet path.
_wifi_rx_state: dict = {
    "last_rx_poll_t":   None,   # time of last GET /api/commands call
    "last_packet_t":    None,   # time of last accepted POST /api/telemetry
    "lost":             0,      # estimated lost packets from sequence gaps
    "bad":              0,      # packets rejected (no "n" key or malformed)
    "last_seq":         None,   # last received sequence number
    "session_start_t":  None,   # server time of first packet in current session
    "session_launched": False,  # derived launched flag, sticky per session
    "session_ejected":  False,  # derived ejected flag, sticky per session
}
# Pending uplink commands for the receiver to collect on GET /api/commands.
_uplink_queue: list = []
_uplink_lock  = threading.Lock()

# ================= AI CONFIG (NVIDIA Nemotron-3 Ultra 550b) =================
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "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")
NVIDIA_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
ai_client = None

if OpenAI and NVIDIA_API_KEY:
    try:
        ai_client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=NVIDIA_API_KEY,
        )
        print(f"🤖 AI analysis: NVIDIA Nemotron-3 Ultra 550b ({NVIDIA_MODEL})")
    except Exception as e:
        print(f"⚠️  Error initializing NVIDIA OpenAI client: {e}")
elif not OpenAI:
    print("⚠️  WARNING: openai package not installed — falling back to local flight analysis")
else:
    print("⚠️  WARNING: NVIDIA_API_KEY not set — falling back to local flight analysis")

# ================= APP =================
app = Flask(__name__, template_folder='template', static_folder='template', static_url_path='', root_path=os.path.dirname(__file__))
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# Shared ground-station token for state-changing routes (override via GS_TOKEN).
GS_TOKEN = os.getenv("GS_TOKEN", "vikram-gs-local")
_cors_origins = [
    o.strip() for o in os.getenv(
        "GS_CORS_ORIGINS",
        "http://127.0.0.1:5000,http://localhost:5000,http://127.0.0.1:5055,http://localhost:5055",
    ).split(",")
    if o.strip()
]
CORS(app, origins=_cors_origins, allow_headers=["Content-Type", "X-GS-Token"])

# Thread-safe shared state
_lock            = threading.Lock()
latest_data: dict        = {}
flight_data: deque       = deque(maxlen=MAX_FLIGHT_PACKETS)
serial_connected: bool   = False
current_session_id: str  = "default"  # Current flight session
last_ts_value: int       = 0          # For reset detection
data_version: int        = 0          # Bumped on each new packet (SSE)
port_reload_flag: bool   = False      # Break serial loop after COM switch
reset_info: dict = {
    "detected": False,
    "at": None,
    "from_ts": None,
    "to_ts": None,
    "session_id": None,
}
packet_id_history: deque = deque(maxlen=500)  # for loss estimation
_open_anomalies: dict = {}  # (session_id, type, severity) -> last insert time
_anomaly_lock = threading.Lock()
ANOMALY_THROTTLE_S = 5.0

# Cached AI result
ai_cache: dict = {
    "analysis":   None,
    "reasoning":  None,
    "timestamp":  None,
    "provider":   None,
    "phase":      None,
    "anomalies":  [],
    "running":    False,
}

# Simple in-process rate limiter  {ip: [timestamps]}
_rate_limit_store: dict = {}
RATE_LIMIT_MAX   = int(os.getenv("RATE_LIMIT_MAX", "60"))    # requests
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60")) # seconds


# =====================================================================
# DATABASE
# =====================================================================
def init_db():
    conn   = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='telemetry'")
    if cursor.fetchone() is None:
        cursor.execute('''
            CREATE TABLE telemetry (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT,
                packet_id   INTEGER,
                timestamp   INTEGER,
                mission_time REAL,
                altitude    REAL,
                temperature REAL,
                pressure    REAL,
                humidity    REAL,
                ax REAL, ay REAL, az REAL,
                gx REAL, gy REAL, gz REAL,
                lat REAL, lon REAL,
                battery_voltage REAL,
                rssi INTEGER,
                snr REAL,
                launched    BOOLEAN,
                ejected     BOOLEAN,
                sim         BOOLEAN,
                flight_phase TEXT,
                raw_json    TEXT,
                received_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                temp_dht    REAL,
                mq135       REAL,
                current_ma  REAL,
                gps_alt     REAL,
                sats        INTEGER,
                relay       INTEGER,
                servo       INTEGER
            )
        ''')
    else:
        cursor.execute("PRAGMA table_info(telemetry)")
        existing_cols = {row[1] for row in cursor.fetchall()}

        missing_columns = [
            ("session_id", "TEXT"),
            ("packet_id", "INTEGER"),
            ("timestamp", "INTEGER"),
            ("mission_time", "REAL"),
            ("altitude", "REAL"),
            ("temperature", "REAL"),
            ("pressure", "REAL"),
            ("humidity", "REAL"),
            ("ax", "REAL"), ("ay", "REAL"), ("az", "REAL"),
            ("gx", "REAL"), ("gy", "REAL"), ("gz", "REAL"),
            ("lat", "REAL"), ("lon", "REAL"),
            ("battery_voltage", "REAL"),
            ("rssi", "INTEGER"),
            ("snr", "REAL"),
            ("launched", "BOOLEAN"),
            ("ejected", "BOOLEAN"),
            ("sim", "BOOLEAN"),
            ("flight_phase", "TEXT"),
            ("raw_json", "TEXT"),
            ("received_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
            # ── New columns for compact-format hardware ────────────────
            ("temp_dht",   "REAL"),
            ("mq135",      "REAL"),
            ("current_ma", "REAL"),
            ("gps_alt",    "REAL"),
            ("sats",       "INTEGER"),
            ("relay",      "INTEGER"),
            ("servo",      "INTEGER"),
        ]

        for name, definition in missing_columns:
            if name not in existing_cols:
                cursor.execute(f"ALTER TABLE telemetry ADD COLUMN {name} {definition}")

    # Indexes for faster queries
    index_columns = [
        ("idx_session_id", "session_id"),
        ("idx_packet_id", "packet_id"),
        ("idx_timestamp", "timestamp"),
        ("idx_received_at", "received_at"),
        ("idx_altitude", "altitude"),
        ("idx_lat_lon", "lat, lon"),
    ]
    for idx_name, cols in index_columns:
        try:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON telemetry({cols})")
        except sqlite3.OperationalError as e:
            print(f"⚠️  Skipped index {idx_name}: {e}")

    # Create missions table for session tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS missions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT UNIQUE,
            start_time  DATETIME DEFAULT CURRENT_TIMESTAMP,
            end_time    DATETIME,
            team_name   TEXT,
            cansat_id   TEXT,
            location    TEXT,
            notes       TEXT
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mission_session ON missions(session_id)")
    
    # Create phase transitions table for flight phase logging
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS phase_transitions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT,
            phase       TEXT,
            timestamp_ms INTEGER,
            packet_id   INTEGER,
            altitude_m  REAL,
            velocity_ms REAL,
            az_accel    REAL,
            logged_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES missions(session_id)
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_phase_session ON phase_transitions(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_phase_timestamp ON phase_transitions(timestamp_ms)")
    
    # Create anomaly events table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS anomaly_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT,
            packet_id   INTEGER,
            anomaly_type TEXT,
            severity    TEXT,
            description TEXT,
            value       REAL,
            timestamp_ms INTEGER,
            detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES missions(session_id)
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_session ON anomaly_events(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_type ON anomaly_events(anomaly_type)")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mission_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT,
            event_type  TEXT,
            event_note  TEXT,
            packet_id   INTEGER,
            altitude    REAL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON mission_events(session_id)")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT, command TEXT, status TEXT,
            packet_id INTEGER, sent_at DATETIME, acked_at DATETIME
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_commands_session ON commands(session_id)")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS range_samples (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT,
            packet_id   INTEGER,
            rssi        REAL,
            snr         REAL,
            distance_m  REAL,
            logged_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_range_session ON range_samples(session_id)")
    
    conn.commit()
    conn.close()
    print("✅ Database initialised with extended schema:", DB_PATH)


def _sanitise_float(val, default=0.0):
    """Return a clean float or a safe default."""
    try:
        f = float(val)
        if f != f:   # NaN
            return default
        if abs(f) > 1e9:
            return default
        return round(f, 6)
    except (TypeError, ValueError):
        return default


def _num(val, default=0.0):
    """Coerce None/invalid sensor values to a numeric default for comparisons."""
    if val is None:
        return default
    try:
        f = float(val)
        if f != f:
            return default
        return f
    except (TypeError, ValueError):
        return default


def _fmt_sensor(val, spec=".2f"):
    if val is None:
        return "n/a"
    try:
        return format(float(val), spec)
    except (TypeError, ValueError):
        return "n/a"


def _sanitise_int(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _sanitise_optional_float(val):
    if val is None or val == "":
        return None
    try:
        f = float(val)
        if f != f or abs(f) > 1e9:
            return None
        return round(f, 6)
    except (TypeError, ValueError):
        return None


def _sanitise_gps(val):
    """Return a coordinate or None when missing/invalid. Never coerce to 0.0."""
    if val is None or val == "":
        return None
    try:
        f = float(val)
        if f != f or abs(f) > 180:
            return None
        return round(f, 6)
    except (TypeError, ValueError):
        return None


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _new_track_state():
    return {
        "lat": None, "lon": None, "ts": None,
        "dist": 0.0, "had_gps": False,
        "alt": None, "alt_ts": None,
    }


_last_gps_fix: dict = {}
_track_state: dict = _new_track_state()
_last_logged_phase: str = "PRE-LAUNCH"
_auto_exported_sessions: set = set()
_session_boot_ts = None


def _attach_track_fields(data: dict, state: dict) -> dict:
    """Stamp cumulative distance-along-track (m), track_source, and v_z on a packet.

    GPS gaps do not haversine across the dropout (that would fabricate range).
    During a gap, distance is held unless ground speed is available (labeled estimated).
    """
    if "dist" not in state:
        state.update(_new_track_state())
    lat, lon = data.get("lat"), data.get("lon")
    ts = data.get("ts") or 0
    gps_ok = lat is not None and lon is not None
    prev_ok = state.get("lat") is not None and state.get("lon") is not None

    if gps_ok and prev_ok:
        state["dist"] += _haversine_m(state["lat"], state["lon"], lat, lon)
        data["track_source"] = "gps"
        state["lat"], state["lon"] = lat, lon
        state["had_gps"] = True
    elif gps_ok:
        data["track_source"] = "gps"
        state["lat"], state["lon"] = lat, lon
        state["had_gps"] = True
    else:
        dt = ((ts - state["ts"]) / 1000.0) if state.get("ts") is not None else 0.0
        spd = data.get("speed") or 0.0
        if state.get("had_gps") and dt > 0 and spd > 0:
            state["dist"] += float(spd) * dt
            data["track_source"] = "estimated"
        else:
            data["track_source"] = "none"
        # Drop last fix so the next valid GPS does not add the gap as range.
        state["lat"], state["lon"] = None, None

    data["distance_along_track"] = round(state["dist"], 3)

    alt = data.get("alt")
    if state.get("alt") is not None and alt is not None and state.get("alt_ts") is not None:
        dt_alt = (ts - state["alt_ts"]) / 1000.0
        data["v_z"] = round((alt - state["alt"]) / dt_alt, 4) if dt_alt > 0 else 0.0
    else:
        data["v_z"] = 0.0
    if alt is not None:
        state["alt"] = alt
        state["alt_ts"] = ts
    state["ts"] = ts
    return state


def _sanitise_packet(data: dict) -> dict:
    """Validate and sanitise a raw telemetry packet (old / pre-normalised format)."""
    lat = _sanitise_gps(data.get("lat", data.get("latitude")))
    lon = _sanitise_gps(data.get("lon", data.get("longitude")))
    if lat is not None and lon is not None and abs(lat) < 1e-8 and abs(lon) < 1e-8:
        lat, lon = None, None
    speed = _sanitise_optional_float(data.get("speed", data.get("spd", data.get("vel"))))
    return {
        "session_id": str(data.get("session_id", "default")),
        "packet_id": _sanitise_int(data.get("packet_id", 0), 0),
        "ts":       _sanitise_int(data.get("ts", 0), 0),
        "mission_time": _sanitise_float(data.get("mission_time", 0.0)),
        "alt":      _sanitise_float(data.get("alt")),
        "temp":     _sanitise_float(data.get("temp"), default=25.0),
        "pressure": _sanitise_float(data.get("pressure"), default=101325.0),
        "humidity": _sanitise_float(data.get("humidity"), default=50.0),
        "ax":       _sanitise_float(data.get("ax")),
        "ay":       _sanitise_float(data.get("ay")),
        "az":       _sanitise_float(data.get("az")),
        "gx":       _sanitise_float(data.get("gx")),
        "gy":       _sanitise_float(data.get("gy")),
        "gz":       _sanitise_float(data.get("gz")),
        "lat":      lat,
        "lon":      lon,
        "speed":    speed,
        "batt_v":   _sanitise_float(data.get("batt_v"), default=3.7),
        "rssi":     int(data.get("rssi", -100)) if isinstance(data.get("rssi"), (int, float)) else -100,
        "snr":      _sanitise_float(data.get("snr"), default=0.0),
        "launched": bool(data.get("launched", False)),
        "ejected":  bool(data.get("ejected",  False)),
        "sim":      bool(data.get("sim",      False)),
        # new columns — kept as-is from old format (None when missing)
        "temp_dht":  _sanitise_optional_float(data.get("temp_dht")),
        "mq135":     _sanitise_optional_float(data.get("mq135")),
        "current_ma":_sanitise_optional_float(data.get("current_ma")),
        "gps_alt":   _sanitise_optional_float(data.get("gps_alt")),
        "sats":      int(data.get("sats")) if data.get("sats") is not None else None,
        "relay":     int(data.get("relay")) if data.get("relay") is not None else None,
        "servo":     int(data.get("servo")) if data.get("servo") is not None else None,
    }


# ---------------------------------------------------------------------------
# NEW-FORMAT ADAPTER  (compact keys from E220 / Glyph C6 WiFi receiver)
# ---------------------------------------------------------------------------
# Field mapping: compact key -> normalised key, conversion note
#  n    -> packet_id (as is)
#  a    -> alt        (metres, as is)
#  bt   -> temp       (BMP180 °C; fallback: dt DHT temp)
#  p    -> pressure   (Pa, as is)
#  h    -> humidity   (%, as is)
#  ax..az -> ax..az  (m/s² -> g: divide by 9.80665)
#  gx..gz -> gx..gz  (rad/s -> °/s: multiply by 57.29578)
#  la   -> lat        GPS latitude (as is, absent when no fix)
#  lo   -> lon        GPS longitude (as is, absent when no fix)
#  v    -> batt_v     INA219 bus voltage (GUESS: this maps to batt_v)
#  dt   -> temp_dht   (new column)
#  mq   -> mq135      (new column)
#  i    -> current_ma (new column)
#  ga   -> gps_alt    (new column)
#  s    -> sats       (new column)
#  r    -> relay      (new column)
#  sv   -> servo      (new column)
# rssi, snr are NOT available from E220 in transparent mode -> left as None.

_G        = 9.80665
_RAD2DEG  = 57.29577951308232


def _adapt_new_format(raw: dict) -> dict:
    """Convert compact-key new-hardware packet to the normalised format.

    Rules:
    - Never invent sensor values: if a key is absent the normalised key is None.
    - rssi / snr are always None (E220 transparent mode gives no RSSI to the host).
    - Accelerations arrive in m/s²; divide by g to get g-force.
    - Gyro arrives in rad/s; multiply by 57.296 to get °/s.
    - lat/lon absent when no GPS fix (firmware omits them).
    """
    normed: dict = {}

    # Sequence -> packet_id
    normed["packet_id"] = int(raw["n"])

    # Altitude (metres above pad)
    normed["alt"] = _sanitise_optional_float(raw.get("a"))  # None if BMP absent

    # Temperature: BMP180 preferred, DHT fallback
    bt = _sanitise_optional_float(raw.get("bt"))
    dt_val = _sanitise_optional_float(raw.get("dt"))
    normed["temp"] = bt if bt is not None else dt_val   # None when both absent

    # Pressure (Pa)
    normed["pressure"] = _sanitise_optional_float(raw.get("p"))

    # Humidity (%)
    normed["humidity"] = _sanitise_optional_float(raw.get("h"))

    # Accelerations: m/s² -> g
    def _acc(k):
        v = _sanitise_optional_float(raw.get(k))
        return round(v / _G, 6) if v is not None else None

    normed["ax"] = _acc("ax")
    normed["ay"] = _acc("ay")
    normed["az"] = _acc("az")

    # Gyro: rad/s -> °/s
    def _gyr(k):
        v = _sanitise_optional_float(raw.get(k))
        return round(v * _RAD2DEG, 6) if v is not None else None

    normed["gx"] = _gyr("gx")
    normed["gy"] = _gyr("gy")
    normed["gz"] = _gyr("gz")

    # GPS (absent when no fix; firmware omits la/lo when invalid)
    lat = _sanitise_gps(raw.get("la"))
    lon = _sanitise_gps(raw.get("lo"))
    # reject (0, 0) as invalid
    if lat is not None and lon is not None and abs(lat) < 1e-8 and abs(lon) < 1e-8:
        lat, lon = None, None
    normed["lat"] = lat
    normed["lon"] = lon

    # Power — bus voltage (GUESS: INA219 "v" maps to batt_v)
    normed["batt_v"] = _sanitise_optional_float(raw.get("v"))

    # No RSSI/SNR from E220 transparent mode
    normed["rssi"] = None
    normed["snr"]  = None

    # New-format extra channels
    normed["temp_dht"]   = dt_val
    normed["mq135"]      = _sanitise_optional_float(raw.get("mq"))
    normed["current_ma"] = _sanitise_optional_float(raw.get("i"))
    normed["gps_alt"]    = _sanitise_optional_float(raw.get("ga"))
    s_val = raw.get("s")
    normed["sats"]  = int(s_val)  if s_val is not None else None
    r_val = raw.get("r")
    normed["relay"] = int(r_val)  if r_val is not None else None
    sv_val = raw.get("sv")
    normed["servo"] = int(sv_val) if sv_val is not None else None

    normed["sim"] = False
    return normed


def ingest_packet(raw: dict, source: str = "serial") -> dict | None:
    """Central entry point for ALL incoming telemetry packets.

    Accepts both:
      - Old format: has 'alt', 'ax'... already normalised.
      - New compact format: has 'n'; converted via _adapt_new_format().

    Returns the normalised data dict (already placed into flight_data /
    latest_data) or None if the packet was rejected.
    """
    global current_session_id, last_ts_value, data_version
    global _last_logged_phase, _session_boot_ts

    is_new_format = "n" in raw and "alt" not in raw

    if is_new_format:
        # ---- NEW COMPACT FORMAT ----------------------------------------
        try:
            normed = _adapt_new_format(raw)
        except Exception as e:
            print(f"⚠️  Compact-format adapt error: {e}  raw={raw}")
            with _uplink_lock:
                _wifi_rx_state["bad"] += 1
            return None

        seq = normed["packet_id"]

        with _uplink_lock:
            last_seq = _wifi_rx_state["last_seq"]
            now_t    = time.time()

            # Reset detection: sequence went backwards (reboot), not a duplicate packet
            seq_restarted = (
                last_seq is not None
                and seq < last_seq
                and (seq < 5 or (last_seq - seq) > 20)
            )
            if seq_restarted:
                print(f"🔄 RESET DETECTED (compact): seq {last_seq} -> {seq} — new session")
                new_sid = f"session_{int(now_t)}"
                current_session_id = new_sid
                _wifi_rx_state["session_launched"] = False
                _wifi_rx_state["session_ejected"]  = False
                _wifi_rx_state["session_start_t"]  = now_t
                _last_logged_phase = "PRE-LAUNCH"
                _session_boot_ts = None
                with _lock:
                    _track_state.clear()
                    _track_state.update(_new_track_state())
                    _last_gps_fix.clear()
                    reset_info.update({
                        "detected": True, "at": now_t,
                        "from_ts": last_seq, "to_ts": seq,
                        "session_id": new_sid,
                    })
            elif _wifi_rx_state["session_start_t"] is None:
                _wifi_rx_state["session_start_t"] = now_t

            # Packet loss from sequence gaps
            if last_seq is not None and seq > last_seq + 1:
                _wifi_rx_state["lost"] += seq - last_seq - 1

            _wifi_rx_state["last_seq"]      = seq
            _wifi_rx_state["last_packet_t"] = now_t

            # Mission time = seconds since session's first packet
            session_start = _wifi_rx_state.get("session_start_t") or now_t
            mission_time  = round(now_t - session_start, 3)

            # Derive launched / ejected flags (sticky per session)
            ax_g = _num(normed.get("ax"))
            ay_g = _num(normed.get("ay"))
            az_g = _num(normed.get("az"))
            accel_mag = math.sqrt(ax_g**2 + ay_g**2 + az_g**2)
            alt_m = _num(normed.get("alt"))
            if not _wifi_rx_state["session_launched"]:
                if accel_mag > LAUNCH_ACCEL_THRESHOLD_G or alt_m > LAUNCH_ALT_THRESHOLD_M:
                    _wifi_rx_state["session_launched"] = True
                    print(f"🚀 LAUNCHED detected (accel={accel_mag:.2f}g, alt={alt_m:.1f}m)")
            if _wifi_rx_state["session_launched"] and not _wifi_rx_state["session_ejected"]:
                if normed.get("relay") == 1:
                    _wifi_rx_state["session_ejected"] = True
                    print(f"🪂 EJECTED detected (relay=1 after launch)")

            normed["launched"] = _wifi_rx_state["session_launched"]
            normed["ejected"]  = _wifi_rx_state["session_ejected"]

        # Stamp server-side ts (ms) and mission_time
        normed["ts"]           = int(time.time() * 1000)
        normed["mission_time"] = mission_time
        normed["session_id"]   = current_session_id
        normed["speed"]        = None  # will be computed below if GPS present

    else:
        # ---- OLD / PRE-NORMALISED FORMAT (serial, test-data, etc.) -----
        normed = _sanitise_packet(raw)
        normed["session_id"] = current_session_id

        # Legacy reset detection via ts counter
        current_ts = normed.get("ts", 0)
        if last_ts_value > 10000 and current_ts < 1000:
            print(f"🔄 RESET DETECTED (serial): ts {last_ts_value}ms -> {current_ts}ms")
            current_session_id = f"session_{int(time.time())}"
            normed["session_id"] = current_session_id
            with _lock:
                reset_info.update({
                    "detected": True, "at": time.time(),
                    "from_ts": last_ts_value, "to_ts": current_ts,
                    "session_id": current_session_id,
                })
        last_ts_value = current_ts

    # ---- Common post-processing (both formats) --------------------------
    # GPS-derived speed
    lat, lon = normed.get("lat"), normed.get("lon")
    ts_ms = normed.get("ts", 0)
    with _lock:
        last_fix_state = _last_gps_fix.copy()
    if normed.get("speed") is None:
        if lat is not None and lon is not None and last_fix_state.get("lat") is not None:
            dt = (ts_ms - last_fix_state["ts"]) / 1000.0
            normed["speed"] = (
                _haversine_m(last_fix_state["lat"], last_fix_state["lon"], lat, lon) / dt
                if dt > 0 else last_fix_state.get("speed", 0.0)
            )
        else:
            normed["speed"] = 0.0
    if lat is not None and lon is not None:
        with _lock:
            _last_gps_fix.update({"lat": lat, "lon": lon, "ts": ts_ms, "speed": normed["speed"]})

    # Track fields
    with _lock:
        _attach_track_fields(normed, _track_state)

    # Update shared state
    with _lock:
        latest_data.clear()
        latest_data.update(normed)
        flight_data.append(normed)
        data_version += 1
        pid = normed.get("packet_id")
        if pid is not None:
            packet_id_history.append(int(pid))

    save_to_db(normed)

    # Phase transitions and anomalies
    with _lock:
        recent = list(flight_data)[-20:]
    if len(recent) >= 2:
        current_phase = _detect_flight_phase(recent)
        with _lock:
            if latest_data:
                latest_data["phase"] = current_phase
            if flight_data:
                flight_data[-1]["phase"] = current_phase
        _detect_anomalies(recent, session_id=current_session_id)

    return normed


# Shared GPS fix state and track state used by ingest_packet across both paths
_last_gps_fix: dict = {}
_track_state:  dict = _new_track_state()


def save_to_db(data: dict):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT INTO telemetry
                    (session_id, packet_id, timestamp, mission_time, altitude, temperature, pressure, humidity,
                     ax, ay, az, gx, gy, gz, lat, lon, battery_voltage, rssi, snr,
                     launched, ejected, sim, raw_json,
                     temp_dht, mq135, current_ma, gps_alt, sats, relay, servo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.get("session_id", "default"), data.get("packet_id", 0),
                data.get("ts", 0), data.get("mission_time", 0),
                data.get("alt"), data.get("temp"), data.get("pressure"), data.get("humidity"),
                data.get("ax"), data.get("ay"), data.get("az"),
                data.get("gx"), data.get("gy"), data.get("gz"),
                data.get("lat"), data.get("lon"),
                data.get("batt_v"), data.get("rssi"), data.get("snr"),
                data.get("launched", False), data.get("ejected", False), data.get("sim", False),
                json.dumps(data),
                data.get("temp_dht"), data.get("mq135"), data.get("current_ma"),
                data.get("gps_alt"), data.get("sats"), data.get("relay"), data.get("servo"),
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️  DB write error: {e}")


def load_from_db(limit=1000, since_ts=None):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            if since_ts:
                cursor.execute(
                    "SELECT raw_json FROM telemetry WHERE timestamp >= ? ORDER BY id DESC LIMIT ?",
                    (since_ts, limit)
                )
            else:
                cursor.execute(
                    "SELECT raw_json FROM telemetry ORDER BY id DESC LIMIT ?",
                    (limit,)
                )
            rows = cursor.fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]
    except Exception as e:
        print(f"⚠️  DB read error: {e}")
        return []


# =====================================================================
# RATE LIMITER
# =====================================================================
def rate_limit(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        ip  = request.remote_addr
        now = time.time()
        hits = _rate_limit_store.get(ip, [])
        hits = [t for t in hits if now - t < RATE_LIMIT_WINDOW]
        if len(hits) >= RATE_LIMIT_MAX:
            return jsonify({"error": "Rate limit exceeded. Try again later."}), 429
        hits.append(now)
        _rate_limit_store[ip] = hits
        return f(*args, **kwargs)
    return decorated


def require_gs_token(f):
    """Require X-GS-Token on state-changing ground-station routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _gs_authorized():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def _gs_authorized() -> bool:
    """Accept the shared token, or a same-origin request from the dashboard itself."""
    token = request.headers.get("X-GS-Token", "")
    if GS_TOKEN and token == GS_TOKEN:
        return True
    host = request.host_url.rstrip("/")
    origin = (request.headers.get("Origin") or "").rstrip("/")
    if origin and origin == host:
        return True
    referer = request.headers.get("Referer") or ""
    if host and referer.startswith(host):
        return True
    return False


# =====================================================================
# HELPERS
# =====================================================================
def list_available_ports():
    return [p.device for p in serial.tools.list_ports.comports()]


def log_phase_transition(phase: str, recent: list, session_id: str = "default", v_z: float = 0.0):
    """Log phase transitions to database with velocity tracking."""
    try:
        if not recent:
            return
        
        latest = recent[-1]
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO phase_transitions 
                (session_id, phase, timestamp_ms, packet_id, altitude_m, velocity_ms, az_accel)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                phase,
                latest.get("ts", 0),
                latest.get("packet_id", 0),
                latest.get("alt", 0),
                v_z,  # vertical velocity in m/s
                latest.get("az", 0)
            ))
            conn.commit()
        print(f"📍 Phase transition: {phase} @ {latest.get('alt', 0):.1f}m, v_z={v_z:.2f} m/s")
    except Exception as e:
        print(f"⚠️  Phase logging error: {e}")



def _detect_flight_phase(recent: list) -> str:
    """
    Determine the current flight phase from the last N packets.
    Enhanced with pressure-based apogee detection and descent rate validation.
    Returns one of: PRE-LAUNCH, IGNITION, POWERED_ASCENT, COAST,
                    APOGEE, DESCENT, RECOVERY
    """
    if not recent:
        return "PRE-LAUNCH"

    latest  = recent[-1]
    ejected  = latest.get("ejected",  False)
    launched = latest.get("launched", False)
    alt      = _num(latest.get("alt"), 0)
    pressure = _num(latest.get("pressure"), 101325)

    if ejected:
        return "RECOVERY"

    if not launched:
        # Check for ignition spike (any packet in last 5 with high az)
        for p in recent[-5:]:
            if abs(_num(p.get("az"), 0)) > 3.0 or alt > 2:
                return "IGNITION"
        return "PRE-LAUNCH"

    max_alt = max(_num(p.get("alt"), 0) for p in recent)
    max_pressure = min(_num(p.get("pressure"), 101325) for p in recent)  # Lower pressure at altitude

    # Estimate vertical velocity from last two packets
    if len(recent) >= 2:
        dt  = (_num(recent[-1].get("ts"), 1) - _num(recent[-2].get("ts"), 0)) / 1000.0  # ms → s
        dh  = _num(recent[-1].get("alt"), 0) - _num(recent[-2].get("alt"), 0)
        v_z = dh / dt if dt > 0 else 0
    else:
        v_z = 0

    az_net = _num(latest.get("az"), 1.0)

    if az_net > 1.5:           # still under thrust
        return "POWERED_ASCENT"
    if v_z > 0.5:              # coasting upward
        return "COAST"

    # Descent takes priority over a lingering near-peak apogee check.
    if v_z < -0.5:
        return "DESCENT"
    
    # Enhanced apogee detection: pressure-based cross-check
    is_at_peak_alt = alt >= max_alt * 0.95
    is_at_peak_pressure = pressure <= max_pressure * 1.005  # Within 0.5% of min pressure
    velocity_near_zero = abs(v_z) < 0.5
    
    if (is_at_peak_alt or is_at_peak_pressure) and velocity_near_zero:
        return "APOGEE"
    
    return "DESCENT"


def _detect_anomalies(recent: list, session_id: str = "default") -> list:
    """Return a list of anomaly dicts with enhanced logging. Includes descent rate validation."""
    anomalies = []
    if not recent:
        return anomalies

    latest = recent[-1]

    # High lateral G
    ax, ay = _num(latest.get("ax")), _num(latest.get("ay"))
    lateral_g = (ax**2 + ay**2) ** 0.5
    if lateral_g > 2.0:
        anomalies.append({"type": "HIGH_LATERAL_G", "severity": "HIGH", "text": f"HIGH LATERAL G: {lateral_g:.2f}g"})

    # Altitude drop after launch
    if latest.get("launched") and len(recent) > 10:
        alts = [_num(p.get("alt"), 0) for p in recent[-10:]]
        if alts[-1] < alts[0] - 5:
            anomalies.append({"type": "ALTITUDE_DROP", "severity": "HIGH", "text": f"ALTITUDE DROP: {alts[0]:.1f}m → {alts[-1]:.1f}m"})

    # Gyro spike
    for axis in ["gx", "gy", "gz"]:
        val = abs(_num(latest.get(axis), 0))
        if val > 500:
            anomalies.append({"type": f"GYRO_SPIKE", "severity": "WARN", "text": f"GYRO SPIKE {axis}: {val:.1f}°/s"})

    # Az near zero during ascent
    if latest.get("launched") and not latest.get("ejected"):
        if abs(_num(latest.get("az"), 1)) < 0.05:
            anomalies.append({"type": "AZ_NEAR_ZERO", "severity": "WARN", "text": "AZ ≈ 0: possible free-fall"})

    # Descent rate validation for ejection confirmation
    if latest.get("ejected") and len(recent) >= 2:
        dt = (_num(recent[-1].get("ts"), 1) - _num(recent[-2].get("ts"), 0)) / 1000.0
        dh = _num(recent[-1].get("alt"), 0) - _num(recent[-2].get("alt"), 0)
        descent_rate = abs(dh / dt) if dt > 0 else 0
        # Expected descent rate with parachute: 5-10 m/s
        if descent_rate < 2.0:
            anomalies.append({"type": "LOW_DESCENT_RATE", "severity": "WARN", "text": f"Low descent rate: {descent_rate:.2f} m/s"})
        if descent_rate > 50.0:
            anomalies.append({"type": "HIGH_DESCENT_RATE", "severity": "HIGH", "text": f"High descent rate: {descent_rate:.2f} m/s"})

    # Low battery — skip when the packet has no voltage field
    batt = latest.get("batt_v")
    if batt is not None and _num(batt) < 3.2:
        anomalies.append({"type": "LOW_BATTERY", "severity": "WARN", "text": f"Low battery: {_num(batt):.2f}V"})

    # Signal loss — skip when RSSI is unavailable (E220 transparent mode)
    rssi = latest.get("rssi")
    if rssi is not None and _num(rssi) < -110:
        anomalies.append({"type": "WEAK_SIGNAL", "severity": "WARN", "text": f"Weak signal: {rssi} dBm"})

    # Log anomalies with a 30-second cooldown per (session, type, severity)
    # to eliminate oscillating duplicate entries from alternating sensor packets.
    now = time.time()
    to_insert = []
    with _anomaly_lock:
        for anom in anomalies:
            key = (session_id, anom["type"], anom["severity"])
            last = _open_anomalies.get(key)
            if last is None or (now - last) >= 30.0:
                to_insert.append(anom)
                _open_anomalies[key] = now

    if to_insert:
        try:
            latest = recent[-1]
            with sqlite3.connect(DB_PATH) as conn:
                for anom in to_insert:
                    conn.execute("""
                        INSERT INTO anomaly_events (session_id, packet_id, anomaly_type, severity, description, timestamp_ms)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        session_id,
                        latest.get("packet_id", 0),
                        anom["type"],
                        anom["severity"],
                        anom["text"],
                        latest.get("ts", 0)
                    ))
                conn.commit()
        except Exception as e:
            print(f"⚠️  Anomaly logging error: {e}")

    return anomalies


# =====================================================================
# SERIAL THREAD
# =====================================================================
def read_serial():
    global serial_connected, port_reload_flag
    backoff = 2
    prev_phase = "PRE-LAUNCH"

    print(f"🔄 Serial thread started — target port: {SERIAL_PORT}")

    while True:
        try:
            available = list_available_ports()
            if SERIAL_PORT not in available:
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)   # exponential backoff, cap 30 s
                continue

            backoff = 2  # reset on successful find
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            with _lock:
                serial_connected = True
                port_reload_flag = False
            print(f"✅ Serial connected on {SERIAL_PORT}")

            # Reset shared GPS / track state for this serial session
            with _lock:
                _last_gps_fix.clear()
                _track_state.clear()
                _track_state.update(_new_track_state())

            while True:
                try:
                    with _lock:
                        if port_reload_flag:
                            print(f"🔌 Port reload requested → closing {ser.port}")
                            break

                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue

                    print(f"RAW: {repr(line)}")

                    idx = line.find("{")
                    if idx < 0:
                        continue
                    try:
                        raw_data, _ = json.JSONDecoder().raw_decode(line[idx:])
                    except json.JSONDecodeError:
                        continue

                    # Route through ingest_packet (handles both old and new formats)
                    ingest_packet(raw_data, source="serial")

                    # Phase transition logging (uses data already in flight_data)
                    with _lock:
                        recent = list(flight_data)[-20:]
                    if len(recent) >= 2:
                        current_phase = _detect_flight_phase(recent)
                        if current_phase != prev_phase:
                            dt = (recent[-1].get("ts", 1) - recent[-2].get("ts", 0)) / 1000.0
                            dh = recent[-1].get("alt", 0) - recent[-2].get("alt", 0)
                            v_z = dh / dt if dt > 0 else 0
                            log_phase_transition(
                                phase=current_phase,
                                recent=recent,
                                session_id=current_session_id,
                                v_z=v_z,
                            )
                            prev_phase = current_phase

                except serial.SerialException as e:
                    print(f"❌ Serial lost: {e}")
                    break
                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    print(f"⚠️  Line parse error: {e}")

            ser.close()
            with _lock:
                serial_connected = False
            print("🔌 Serial disconnected — retrying…")
            time.sleep(backoff)

        except Exception as e:
            print(f"🛑 Serial thread error: {e}")
            with _lock:
                serial_connected = False
            time.sleep(backoff)


# =====================================================================
# CONTINUOUS AI ANALYSIS THREAD (NVIDIA Nemotron-3 Ultra 550B)
# =====================================================================
def _build_ai_prompt(recent: list, stats: dict, phase: str, anomalies: list) -> str:
    """Build a detailed CanSat/rocket telemetry prompt for Nemotron-3 Ultra."""
    anomaly_block = (
        "ANOMALIES DETECTED:\n" + "\n".join(f"  • {a.get('text', a.get('type', str(a))) if isinstance(a, dict) else str(a)}" for a in anomalies)
        if anomalies else "No anomalies detected."
    )

    sample = recent[-20:]
    table_rows = []
    for p in sample:
        table_rows.append(
            f"  t={p.get('ts','?')}ms alt={p.get('alt',0):.2f}m "
            f"ax={p.get('ax',0):.3f}g ay={p.get('ay',0):.3f}g az={p.get('az',0):.3f}g "
            f"gx={p.get('gx',0):.1f} gy={p.get('gy',0):.1f} gz={p.get('gz',0):.1f} "
            f"press={p.get('pressure',0):.1f}Pa temp={p.get('temp',0):.1f}C"
        )
    table = "\n".join(table_rows)

    return f"""You are an expert aerospace engineer analysing live CanSat telemetry from Team Vikram Mission Control.

=== FLIGHT SUMMARY ===
Detected Phase : {phase}
Peak Altitude  : {stats.get('max_alt', 0):.2f} m
Total Packets  : {stats.get('points', 0)}
{anomaly_block}

=== LAST 20 TELEMETRY PACKETS ===
(columns: timestamp, altitude, accel X/Y/Z in g, gyro X/Y/Z in °/s, pressure, temperature)
{table}

=== TASK ===
Write a concise (≤150 words) professional aeronautical status report:
1. Confirm the current flight phase and justify it using the telemetry.
2. Comment on airframe stability (lateral G, gyro angular rates).
3. Flag any anomalies and their engineering significance.
4. Recommend actionable next steps (e.g. "monitor descent rate", "verify parachute deployment").
Use precise SI units. Avoid filler phrases like "it seems" or "it appears".
"""


def query_nemotron(prompt: str) -> tuple[str, str]:
    """Execute streaming inference on nvidia/nemotron-3-ultra-550b-a55b with thinking enabled."""
    if ai_client:
        completion = ai_client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=1,
            top_p=0.95,
            max_tokens=16384,
            extra_body={"chat_template_kwargs": {"enable_thinking": True}},
            stream=True,
        )

        reasoning_parts = []
        content_parts = []

        for chunk in completion:
            if not chunk.choices:
                continue
            reasoning = getattr(chunk.choices[0].delta, "reasoning_content", None)
            if reasoning:
                reasoning_parts.append(reasoning)
            if chunk.choices[0].delta.content is not None:
                content_parts.append(chunk.choices[0].delta.content)

        return "".join(content_parts).strip(), "".join(reasoning_parts).strip()

    if not NVIDIA_API_KEY:
        raise RuntimeError("NVIDIA API key not configured")

    # Direct HTTPS streaming fallback if openai package is not available
    import urllib.request
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    payload = {
        "model": NVIDIA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 1,
        "top_p": 0.95,
        "max_tokens": 16384,
        "chat_template_kwargs": {"enable_thinking": True},
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    reasoning_parts = []
    content_parts = []
    with urllib.request.urlopen(req, timeout=30) as resp:
        for line in resp:
            line_str = line.decode("utf-8", errors="ignore").strip()
            if not line_str.startswith("data:"):
                continue
            data_str = line_str[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                r = delta.get("reasoning_content")
                if r:
                    reasoning_parts.append(r)
                c = delta.get("content")
                if c:
                    content_parts.append(c)
            except Exception:
                pass

    return "".join(content_parts).strip(), "".join(reasoning_parts).strip()


def _analyse_flight(snap: list) -> dict:
    """Analyse flight snapshot with NVIDIA Nemotron-3 Ultra 550B, falling back to local model."""
    recent = snap[-100:] if snap else []
    phase = _detect_flight_phase(recent)
    anomalies = _detect_anomalies(recent, session_id=current_session_id)
    stats = {
        "max_alt": max((p.get("alt", 0) or 0) for p in snap) if snap else 0.0,
        "points": len(snap),
    }

    # Summary for PDF generation
    summary = {}
    if build_flight_report:
        try:
            _, summary = build_flight_report(recent, phase, anomalies)
        except Exception as e:
            print(f"⚠️  Local report summary build error: {e}")

    # 1. Try NVIDIA Nemotron-3 Ultra 550b
    if ai_client or NVIDIA_API_KEY:
        try:
            prompt = _build_ai_prompt(recent, stats, phase, anomalies)
            content, reasoning = query_nemotron(prompt)
            if content:
                return {
                    "analysis": content,
                    "reasoning": reasoning,
                    "timestamp": time.time(),
                    "provider": f"NVIDIA Nemotron-3 Ultra 550B ({NVIDIA_MODEL})",
                    "phase": phase,
                    "anomalies": anomalies,
                    "summary": summary,
                    "cached": False,
                }
        except Exception as err:
            print(f"⚠️  NVIDIA Nemotron error (falling back to local): {err}")

    # 2. Local fallback model
    if build_flight_report:
        try:
            local_analysis, summary = build_flight_report(recent, phase, anomalies)
            return {
                "analysis": local_analysis,
                "reasoning": None,
                "timestamp": time.time(),
                "provider": f"Local Model ({FLIGHT_ML_PROVIDER})",
                "phase": phase,
                "anomalies": anomalies,
                "summary": summary,
                "cached": False,
            }
        except Exception as err:
            print(f"⚠️  Local flight model error: {err}")

    # 3. Simple fallback
    return {
        "analysis": f"Phase: {phase}. Altitude: {stats['max_alt']:.1f}m. Packets: {stats['points']}.",
        "reasoning": None,
        "timestamp": time.time(),
        "provider": "telemetry-fallback",
        "phase": phase,
        "anomalies": anomalies,
        "summary": summary,
        "cached": False,
    }


def continuous_ai_analysis():
    """Background thread: re-analyse every AI_ANALYSIS_INTERVAL seconds."""
    global ai_cache

    print(f"🤖 Continuous AI thread started (interval={AI_ANALYSIS_INTERVAL}s)")

    while True:
        time.sleep(AI_ANALYSIS_INTERVAL)

        with _lock:
            snap = list(flight_data)

        if len(snap) < AI_MIN_PACKETS:
            continue

        if ai_cache["running"]:
            continue

        ai_cache["running"] = True
        try:
            result = _analyse_flight(snap)
            ai_cache.update(result | {"running": False})
            print(
                f"🤖 AI analysis updated — phase={result['phase']}, "
                f"anomalies={len(result['anomalies'])}"
            )
        except Exception as e:
            print(f"⚠️  Continuous AI error: {e}")
            ai_cache["running"] = False


# =====================================================================
# API ROUTES
# =====================================================================

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/compact")
@app.route("/ground-station")
def compact_view():
    return redirect("/")


@app.route("/static/<path:filename>")
def serve_static_alias(filename):
    for d in ["template/vendor", "template"]:
        p = os.path.join(os.path.dirname(__file__), d, filename)
        if os.path.exists(p) and os.path.isfile(p):
            return send_file(p)
    return "", 404


@app.route("/style.css")
def serve_css():
    css_file = os.path.join(os.path.dirname(__file__), "template", "style.css")
    if os.path.exists(css_file):
        return send_file(css_file, mimetype="text/css")
    return "", 404


@app.route("/script.js")
def serve_js():
    js_file = os.path.join(os.path.dirname(__file__), "template", "script.js")
    if os.path.exists(js_file):
        return send_file(js_file, mimetype="application/javascript")
    return "", 404


@app.route("/telemetry", methods=["GET"])
def telemetry():
    with _lock:
        return jsonify(dict(latest_data))


@app.route("/telemetry/all", methods=["GET"])
@app.route("/api/history", methods=["GET"])
def telemetry_all():
    n = request.args.get("n", type=int)
    since_ts = request.args.get("since_ts", type=int)
    with _lock:
        snap = list(flight_data)
    if since_ts is not None:
        snap = [p for p in snap if (p.get("ts") or 0) > since_ts]
    if n is not None:
        snap = snap[-min(n, 5000):]
    return jsonify(snap)


@app.route("/telemetry/range", methods=["GET"])
def telemetry_range():
    """
    Query telemetry by time range.
    Params: since_ts (int, ms), limit (int, default 500)
    """
    since_ts = request.args.get("since_ts", type=int)
    limit    = min(request.args.get("limit", 500, type=int), 5000)
    data     = load_from_db(limit=limit, since_ts=since_ts)
    return jsonify(data)


@app.route("/status", methods=["GET"])
def status():
    with _lock:
        conn = serial_connected
        ld   = dict(latest_data)
        pkts = len(flight_data)
        snap = list(flight_data)
        ver  = data_version
        reset = dict(reset_info)
        pids = list(packet_id_history)
        session = current_session_id

    max_alt = max((p.get("alt", 0) or 0) for p in snap) if snap else 0
    max_spd = max((p.get("speed", 0) or 0) for p in snap) if snap else 0

    # Packet success from packet_id gaps when available
    packet_loss_pct = 0.0
    if len(pids) >= 2:
        expected = max(pids) - min(pids) + 1
        if expected > 0:
            packet_loss_pct = max(0.0, (1 - len(set(pids)) / expected) * 100)

    last_good = None
    if ld:
        last_good = time.time()  # approximate; client tracks more precisely via SSE

    # WiFi receiver state
    with _uplink_lock:
        rx_state = dict(_wifi_rx_state)
        pending_cmds = len(_uplink_queue)

    now = time.time()
    last_pkt_t = rx_state.get("last_packet_t") or (ld.get("ts", 0) / 1000.0 if ld.get("ts") else None)
    last_rx_t  = rx_state.get("last_rx_poll_t")
    rx_online  = (last_rx_t  is not None) and (now - last_rx_t  < RX_ONLINE_TIMEOUT_S)
    link_ok    = (last_pkt_t is not None) and (now - last_pkt_t < LINK_TIMEOUT_S)
    # connected = serial OR wifi link
    connected  = conn or link_ok
    lost_count = rx_state.get("lost", 0)
    bad_count  = rx_state.get("bad", 0)
    last_pkt_age = round(now - last_pkt_t, 1) if last_pkt_t else None

    # Merge packet-id-history losses with wifi_rx_state lost counter
    if len(pids) >= 2:
        expected = max(pids) - min(pids) + 1
        if expected > 0:
            packet_loss_pct = max(0.0, (1 - len(set(pids)) / expected) * 100)

    return jsonify({
        # ── existing keys (unchanged) ──
        "connected":        connected,
        "serial_connected": conn,
        "has_telemetry":    bool(ld),
        "packets_received": pkts,
        "latest_ts":        ld.get("ts"),
        "port":             SERIAL_PORT,
        "ai_enabled":       ai_client is not None,
        "ai_model":         NVIDIA_MODEL if ai_client else FLIGHT_ML_PROVIDER,
        "ai_last_run":      ai_cache.get("timestamp"),
        "flight_phase":     ai_cache.get("phase") or _detect_flight_phase(snap[-20:] if snap else []),
        "data_version":     ver,
        "session_id":       session,
        "max_altitude":     max_alt,
        "max_speed":        max_spd,
        "packet_loss_pct":  round(packet_loss_pct, 1),
        "reset":            reset,
        "last_good_at":     last_good,
        "server_time":      now,
        # ── new keys for WiFi receiver ──
        "rx_online":        rx_online,
        "link_ok":          link_ok,
        "lost":             lost_count,
        "bad":              bad_count,
        "last_packet_age":  last_pkt_age,
        "pending_commands": pending_cmds,
        "session_start_t":  rx_state.get("session_start_t") or ((snap[0].get("ts") / 1000.0) if (snap and snap[0].get("ts") and snap[0].get("ts") > 1e11) else None),
        "duration_s":       round(now - rx_state.get("session_start_t"), 1) if rx_state.get("session_start_t") else (ld.get("mission_time", 0.0) if ld else 0.0),
        "mission_time":     ld.get("mission_time", 0.0) if ld else 0.0,
    })


def _mission_summary_payload():
    with _lock:
        snap = list(flight_data)
        session = current_session_id
        pids = list(packet_id_history)
        phase = ai_cache.get("phase") or _detect_flight_phase(snap[-20:] if snap else [])

    if not snap:
        return {
            "session_id": session,
            "phase": phase or "PRE-LAUNCH",
            "max_altitude_m": 0,
            "max_speed_ms": 0,
            "duration_s": 0,
            "packet_count": 0,
            "packet_success_pct": 100.0,
            "packet_loss_pct": 0.0,
            "peak_temp_c": None,
            "min_batt_v": None,
        }

    alts = [p.get("alt", 0) or 0 for p in snap]
    spds = [p.get("speed", 0) or 0 for p in snap]
    temps = [p.get("temp") for p in snap if p.get("temp") is not None]
    batts = [p.get("batt_v") for p in snap if p.get("batt_v") is not None]
    t0 = snap[0].get("ts", 0) or 0
    t1 = snap[-1].get("ts", 0) or 0
    duration = max(0, (t1 - t0) / 1000.0)

    loss = 0.0
    success = 100.0
    if len(pids) >= 2:
        expected = max(pids) - min(pids) + 1
        if expected > 0:
            unique = len(set(pids))
            success = min(100.0, unique / expected * 100)
            loss = max(0.0, 100 - success)

    return {
        "session_id": session,
        "phase": phase,
        "max_altitude_m": round(max(alts), 2),
        "max_speed_ms": round(max(spds), 2),
        "duration_s": round(duration, 1),
        "packet_count": len(snap),
        "packet_success_pct": round(success, 1),
        "packet_loss_pct": round(loss, 1),
        "peak_temp_c": round(max(temps), 1) if temps else None,
        "min_batt_v": round(min(batts), 2) if batts else None,
    }


@app.route("/api/mission-summary", methods=["GET"])
def mission_summary():
    return jsonify(_mission_summary_payload())


@app.route("/api/phases", methods=["GET"])
def get_phases():
    """Phase transitions for chart markers."""
    session = request.args.get("session_id", current_session_id)
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT phase, timestamp_ms, packet_id, altitude_m, velocity_ms, logged_at
            FROM phase_transitions
            WHERE session_id = ?
            ORDER BY timestamp_ms ASC
        """, (session,))
        rows = cursor.fetchall()
        conn.close()
        phases = [
            {
                "phase": r[0],
                "timestamp_ms": r[1],
                "packet_id": r[2],
                "altitude_m": r[3],
                "velocity_ms": r[4],
                "logged_at": r[5],
            }
            for r in rows
        ]
        # Fallback: derive markers from in-memory flight if DB empty
        if not phases:
            with _lock:
                snap = list(flight_data)
            derived = []
            prev = None
            for i, p in enumerate(snap):
                window = snap[max(0, i - 19): i + 1]
                ph = _detect_flight_phase(window)
                if ph != prev and ph in ("APOGEE", "RECOVERY", "DESCENT", "POWERED_ASCENT"):
                    if ph == "RECOVERY" or ph == "APOGEE" or (ph == "DESCENT" and prev in ("APOGEE", "COAST", "POWERED_ASCENT")):
                        derived.append({
                            "phase": ph,
                            "timestamp_ms": p.get("ts", 0),
                            "packet_id": p.get("packet_id", i),
                            "altitude_m": p.get("alt", 0),
                            "velocity_ms": 0,
                            "logged_at": None,
                        })
                    prev = ph
                else:
                    prev = ph
            phases = derived
        return jsonify({"phases": phases, "count": len(phases)})
    except Exception as e:
        return jsonify({"error": str(e), "phases": []}), 500


@app.route("/stream")
@app.route("/api/stream")
def stream_sse():
    """Server-Sent Events stream for live telemetry (fallback: client polls)."""
    def event_stream():
        last_ver = -1
        while True:
            with _lock:
                ver = data_version
                ld = dict(latest_data)
                conn = serial_connected
                pkts = len(flight_data)
                reset = dict(reset_info)
                session = current_session_id
            now = time.time()
            with _uplink_lock:
                rx_state = dict(_wifi_rx_state)
            last_pkt_t = rx_state.get("last_packet_t") or (ld.get("ts", 0) / 1000.0 if ld.get("ts") else None)
            link_ok = (last_pkt_t is not None) and (now - last_pkt_t < LINK_TIMEOUT_S)
            is_live = bool(conn or link_ok)
            if ver != last_ver:
                last_ver = ver
                payload = {
                    "type": "telemetry",
                    "version": ver,
                    "latest": ld,
                    "connected": is_live,
                    "serial_connected": is_live,
                    "link_ok": link_ok,
                    "has_telemetry": bool(ld),
                    "session_id": session,
                    "reset": reset,
                    "phase": ai_cache.get("phase"),
                    "server_time": now,
                    "summary": _mission_summary_payload(),
                }
                yield f"data: {json.dumps(payload)}\n\n"
            else:
                # heartbeat keeps proxies from closing the stream
                yield f": ping {now}\n\n"
            time.sleep(0.35)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/reset/ack", methods=["POST"])
def ack_reset():
    with _lock:
        reset_info["detected"] = False
    return jsonify({"status": "acked"})


@app.route("/report")
def flight_report():
    """Print-ready HTML flight report (Save as PDF from the browser)."""
    summary = _mission_summary_payload()
    with _lock:
        snap = list(flight_data)[-200:]
        phase = ai_cache.get("phase") or summary.get("phase")
        analysis = ai_cache.get("analysis") or "No AI analysis available yet."
        session = current_session_id

    # Recent events
    events = []
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            SELECT event_type, altitude, packet_id, event_note, created_at
            FROM mission_events ORDER BY id DESC LIMIT 20
        """)
        events = cur.fetchall()
        cur.execute("""
            SELECT phase, altitude_m, timestamp_ms FROM phase_transitions
            WHERE session_id = ? ORDER BY id DESC LIMIT 15
        """, (session,))
        phases = cur.fetchall()
        conn.close()
    except Exception:
        phases = []

    rows_html = "".join(
        f"<tr><td>{html_lib.escape(str(p.get('ts','')))}</td>"
        f"<td>{p.get('alt',0):.2f}</td>"
        f"<td>{(p.get('temp') if p.get('temp') is not None else '—')}</td>"
        f"<td>{p.get('batt_v','—')}</td>"
        f"<td>{p.get('rssi','—')}</td>"
        f"<td>{p.get('lat','—')}</td>"
        f"<td>{p.get('lon','—')}</td></tr>"
        for p in snap
    )
    events_html = "".join(
        f"<tr><td>{html_lib.escape(str(e[0]))}</td><td>{e[1]}</td><td>{e[2]}</td>"
        f"<td>{html_lib.escape(str(e[3] or ''))}</td><td>{e[4]}</td></tr>"
        for e in events
    ) or "<tr><td colspan='5'>No events</td></tr>"
    phases_html = "".join(
        f"<tr><td>{html_lib.escape(str(ph[0]))}</td><td>{ph[1]}</td><td>{ph[2]}</td></tr>"
        for ph in phases
    ) or "<tr><td colspan='3'>No phase transitions logged</td></tr>"

    analysis_safe = html_lib.escape(analysis).replace("\n", "<br>")
    generated = time.strftime("%Y-%m-%d %H:%M:%S")

    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"/>
<title>VIKRAM Flight Report — {html_lib.escape(session)}</title>
<style>
  body{{font-family:Georgia,serif;color:#111;margin:40px;line-height:1.45}}
  h1{{font-family:Arial,sans-serif;letter-spacing:.12em;margin:0}}
  h2{{font-family:Arial,sans-serif;font-size:14px;text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid #ccc;padding-bottom:6px;margin-top:28px}}
  .meta{{color:#555;font-size:13px;margin:8px 0 24px}}
  .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}}
  .kpi{{border:1px solid #ddd;padding:12px;border-radius:6px}}
  .kpi b{{display:block;font-size:22px;font-family:Arial,sans-serif}}
  .kpi span{{font-size:11px;text-transform:uppercase;color:#666;letter-spacing:.06em}}
  table{{width:100%;border-collapse:collapse;font-size:12px;font-family:Consolas,monospace}}
  th,td{{border-bottom:1px solid #eee;padding:6px 8px;text-align:left}}
  th{{font-family:Arial,sans-serif;font-size:11px;text-transform:uppercase;color:#555}}
  .ai{{background:#f7f7f7;padding:14px;border-radius:6px;font-size:13px}}
  .actions{{margin:20px 0}}
  button{{padding:10px 16px;font-size:14px;cursor:pointer}}
  @media print{{.actions{{display:none}} body{{margin:16px}}}}
</style></head><body>
  <div class="actions"><button onclick="window.print()">Print / Save as PDF</button></div>
  <img src="/assets/vikram-logo.png" alt="VIKRAM" style="height:72px;width:auto;display:block;margin-bottom:8px"/>
  <div class="meta">Flight report · Session {html_lib.escape(session)} · Phase {html_lib.escape(str(phase))} · Generated {generated}</div>
  <div class="grid">
    <div class="kpi"><b>{summary['max_altitude_m']} m</b><span>Max altitude</span></div>
    <div class="kpi"><b>{summary['max_speed_ms']} m/s</b><span>Max speed</span></div>
    <div class="kpi"><b>{summary['duration_s']} s</b><span>Duration</span></div>
    <div class="kpi"><b>{summary['packet_success_pct']}%</b><span>Packet success</span></div>
  </div>
  <h2>AI analysis</h2>
  <div class="ai">{analysis_safe}</div>
  <h2>Phase transitions</h2>
  <table><thead><tr><th>Phase</th><th>Altitude (m)</th><th>Timestamp (ms)</th></tr></thead>
  <tbody>{phases_html}</tbody></table>
  <h2>Mission events</h2>
  <table><thead><tr><th>Type</th><th>Altitude</th><th>Packet</th><th>Note</th><th>Time</th></tr></thead>
  <tbody>{events_html}</tbody></table>
  <h2>Telemetry sample (latest {len(snap)})</h2>
  <table><thead><tr><th>ts</th><th>alt</th><th>temp</th><th>batt</th><th>rssi</th><th>lat</th><th>lon</th></tr></thead>
  <tbody>{rows_html}</tbody></table>
  <script>window.addEventListener('load',()=>{{ /* ready for print */ }});</script>
</body></html>"""
    return Response(doc, mimetype="text/html")


@app.route("/report.pdf")
def flight_report_pdf():
    """Downloadable PDF from the local flight model (fpdf2)."""
    if _FPDF is None:
        return jsonify({
            "error": "fpdf2 is not installed",
            "hint": "pip install fpdf2 pandas",
        }), 500
    with _lock:
        snap = list(flight_data)
    if not snap:
        return jsonify({"error": "No telemetry yet"}), 404
    result = _analyse_flight(snap)
    pdf_bytes = build_flight_pdf(
        result["analysis"], result.get("summary") or {}, snap[-200:], result["phase"]
    )
    try:
        save_flight_pdf(
            result["analysis"], result.get("summary") or {}, snap[-200:], result["phase"]
        )
    except OSError as exc:
        print(f"⚠️  Could not save PDF copy: {exc}")
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"vikram_flight_{current_session_id}.pdf",
    )


@app.route("/api/ai-analysis/latest", methods=["GET"])
def ai_analysis_latest():
    """Return the most recently cached AI analysis instantly."""
    if ai_cache["analysis"] is None:
        return jsonify({
            "analysis": None,
            "reasoning": None,
            "phase": ai_cache.get("phase"),
            "anomalies": ai_cache.get("anomalies") or [],
            "cached": True,
            "pending": True,
            "message": "No analysis available yet.",
        }), 200
    return jsonify({
        "analysis":  ai_cache["analysis"],
        "reasoning": ai_cache.get("reasoning"),
        "timestamp": ai_cache["timestamp"],
        "provider":  ai_cache["provider"],
        "phase":     ai_cache["phase"],
        "anomalies": ai_cache["anomalies"],
        "cached":    True,
    })


@app.route("/api/ai-analysis", methods=["GET"])
@rate_limit
def ai_analysis():
    """Trigger an on-demand local flight analysis (rate limited)."""
    with _lock:
        snap = list(flight_data)

    if len(snap) < AI_MIN_PACKETS:
        return jsonify({"error": f"Not enough data yet ({len(snap)}/{AI_MIN_PACKETS} packets)"}), 404

    if ai_cache["analysis"] and ai_cache["timestamp"]:
        age = time.time() - ai_cache["timestamp"]
        if age < 30:
            result = dict(ai_cache)
            result["cached"] = True
            return jsonify(result)

    result = _analyse_flight(snap)
    ai_cache.update(result | {"running": False})
    return jsonify({k: v for k, v in result.items() if k != "summary"})


@app.route("/test-data", methods=["GET", "POST"])
def test_data():
    """Load synthetic test packets for UI debugging."""
    if not _gs_authorized():
        return jsonify({"error": "unauthorized", "hint": "Open the dashboard from this server, or send X-GS-Token."}), 401
    global data_version
    import uuid
    session_id = str(uuid.uuid4())[:8]
    now_ms = int(time.time() * 1000)
    packets = [
        {"session_id": session_id, "packet_id": 1, "alt": 0,   "temp": 28.5, "pressure": 101325, "humidity": 55, "lat": 12.9626, "lon": 79.9541, "batt_v": 3.95, "rssi": -75, "snr": 8.5, "ax": 0.008, "ay": 0.001, "az": 0.986, "gx": 0.07, "gy": 0.17, "gz": 0.02, "launched": False, "ejected": False, "sim": True, "ts": now_ms - 6000, "mission_time": 0},
        {"session_id": session_id, "packet_id": 2, "alt": 5,   "temp": 28.3, "pressure": 101290, "humidity": 55, "lat": 12.9627, "lon": 79.9542, "batt_v": 3.94, "rssi": -74, "snr": 8.7, "ax": 0.01,  "ay": 0.002, "az": 0.99,  "gx": 0.08, "gy": 0.18, "gz": 0.02, "launched": False, "ejected": False, "sim": True, "ts": now_ms - 5000, "mission_time": 0.3},
        {"session_id": session_id, "packet_id": 3, "alt": 15,  "temp": 28.0, "pressure": 101200, "humidity": 54, "lat": 12.9628, "lon": 79.9543, "batt_v": 3.92, "rssi": -72, "snr": 9.2, "ax": 0.02,  "ay": 0.005, "az": 2.50,  "gx": 0.10, "gy": 0.20, "gz": 0.03, "launched": True,  "ejected": False, "sim": True, "ts": now_ms - 4000, "mission_time": 0.6},
        {"session_id": session_id, "packet_id": 4, "alt": 50,  "temp": 27.5, "pressure": 100950, "humidity": 52, "lat": 12.9630, "lon": 79.9545, "batt_v": 3.88, "rssi": -68, "snr": 10.1, "ax": 0.05,  "ay": 0.01,  "az": 3.20,  "gx": 0.15, "gy": 0.25, "gz": 0.05, "launched": True,  "ejected": False, "sim": True, "ts": now_ms - 3000, "mission_time": 0.9},
        {"session_id": session_id, "packet_id": 5, "alt": 120, "temp": 26.8, "pressure": 100450, "humidity": 50, "lat": 12.9633, "lon": 79.9548, "batt_v": 3.82, "rssi": -65, "snr": 11.0, "ax": 0.03,  "ay": 0.008, "az": 1.10,  "gx": 0.12, "gy": 0.22, "gz": 0.04, "launched": True,  "ejected": False, "sim": True, "ts": now_ms - 2000, "mission_time": 1.2},
        {"session_id": session_id, "packet_id": 6, "alt": 200, "temp": 25.5, "pressure": 99725, "humidity": 48, "lat": 12.9636, "lon": 79.9551, "batt_v": 3.75, "rssi": -62, "snr": 12.3, "ax": 0.02,  "ay": 0.005, "az": 0.98,  "gx": 0.10, "gy": 0.19, "gz": 0.03, "launched": True,  "ejected": False, "sim": True, "ts": now_ms - 1000, "mission_time": 1.5},
        {"session_id": session_id, "packet_id": 7, "alt": 198, "temp": 25.2, "pressure": 99850, "humidity": 48, "lat": 12.9637, "lon": 79.9552, "batt_v": 3.73, "rssi": -61, "snr": 12.5, "ax": 0.01,  "ay": 0.003, "az": 0.05,  "gx": 0.05, "gy": 0.10, "gz": 0.01, "launched": True,  "ejected": True,  "sim": True, "ts": now_ms,        "mission_time": 1.8},
    ]
    clean = [_sanitise_packet(p) for p in packets]
    last_fix = None
    track_state = _new_track_state()
    for p in clean:
        p["session_id"] = current_session_id
        lat, lon = p.get("lat"), p.get("lon")
        if p.get("speed") is None and lat is not None and lon is not None and last_fix:
            dt = (p.get("ts", 0) - last_fix["ts"]) / 1000.0
            p["speed"] = (_haversine_m(last_fix["lat"], last_fix["lon"], lat, lon) / dt) if dt > 0 else 0.0
        elif p.get("speed") is None:
            p["speed"] = 0.0
        if lat is not None and lon is not None:
            last_fix = {"lat": lat, "lon": lon, "ts": p.get("ts", 0)}
        _attach_track_fields(p, track_state)
    with _lock:
        flight_data.extend(clean)
        latest_data.clear()
        latest_data.update(clean[-1])
        data_version += 1
        for p in clean:
            pid = p.get("packet_id")
            if pid is not None:
                packet_id_history.append(int(pid))
    return jsonify({"status": "Test data loaded", "packets_added": len(clean), "total_packets": len(flight_data)})


@app.route("/ports", methods=["GET"])
def ports():
    ports_info = []
    try:
        for p in serial.tools.list_ports.comports():
            desc = f" ({p.description})" if p.description and p.description != p.device and "n/a" not in p.description.lower() else ""
            ports_info.append({"port": p.device, "label": f"{p.device}{desc}"})
    except Exception as e:
        print(f"⚠️  Error enumerating COM ports: {e}")
    available = [p["port"] for p in ports_info]
    return jsonify({
        "available_ports": available,
        "ports_info": ports_info,
        "current_port": SERIAL_PORT,
        "serial_connected": serial_connected
    })


def send_command():
    """Receive and queue uplink commands.

    Accepts both:
    - Old format: {"command": "ARM"|"DEPLOY"|"ABORT"} — acknowledged, logged, behaviour unchanged.
    - New hardware: {"relay": 0|1}, {"servo": 0-180}, {"cal": 1} — queued for WiFi receiver.
    """
    try:
        data = request.get_json(silent=True) or {}
        if not data:
            return jsonify({"status": "error", "message": "JSON body required"}), 400

        cmd_str: str | None = None    # compact JSON to queue for WiFi receiver
        log_cmd: str = ""
        packet_id = data.get("packet_id", 0)

        if "relay" in data and data["relay"] in (0, 1, True, False):
            r_val = int(data["relay"])
            cmd_str = json.dumps({"r": r_val}, separators=(",", ":"))
            log_cmd = f"RELAY={r_val}"

        elif data.get("cal"):
            cmd_str = json.dumps({"cal": 1}, separators=(",", ":"))
            log_cmd = "CAL_ZERO"

        elif "servo" in data:
            try:
                angle = max(0, min(180, int(data["servo"])))
                cmd_str = json.dumps({"sv": angle}, separators=(",", ":"))
                log_cmd = f"SERVO={angle}"
            except (TypeError, ValueError):
                return jsonify({"status": "error", "message": "Invalid servo value"}), 400

        else:
            # Old format: ARM / DEPLOY / ABORT
            cmd = data.get("command", "").upper()
            if cmd not in ["ARM", "DEPLOY", "ABORT"]:
                return jsonify({"status": "error", "message": "Invalid command"}), 400
            log_cmd = cmd
            # Old commands are acknowledged immediately (serial uplink not implemented here)
            print(f"[COMMAND] {cmd} received (packet_id={packet_id})")
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("""
                        INSERT INTO commands (session_id, command, status, packet_id, sent_at)
                        VALUES (?, ?, ?, ?, datetime('now'))
                    """, (current_session_id, cmd, "SENT", packet_id))
                    conn.commit()
            except Exception as db_err:
                print(f"⚠️  Command DB logging error: {db_err}")
            return jsonify({
                "status": "sent",
                "command": cmd,
                "packet_id": packet_id,
                "timestamp": time.time(),
            })

        # Queue for WiFi receiver
        with _uplink_lock:
            _uplink_queue.append(cmd_str)

        # Log to DB
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    INSERT INTO commands (session_id, command, status, packet_id, sent_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                """, (current_session_id, log_cmd, "QUEUED", packet_id))
                conn.commit()
        except Exception as db_err:
            print(f"⚠️  Command DB logging error: {db_err}")

        print(f"[COMMAND] Queued for WiFi receiver: {cmd_str}")
        return jsonify({
            "status": "queued",
            "queued": json.loads(cmd_str),
            "message": "Queued; sent to CanSat within about a second.",
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/command", methods=["POST"])
@require_gs_token
def api_command():
    return send_command()


# =====================================================================
# WIFI RECEIVER ENDPOINTS  (no token — ESP32 cannot send headers)
# NOTE: Run this server on a private WiFi network only.
# =====================================================================

@app.route("/api/telemetry", methods=["POST"])
def wifi_telemetry():
    """WiFi receiver (Glyph C6) posts one compact JSON packet from the CanSat.

    Body must be a JSON object with key 'n' (sequence number).
    No authentication token — the receiver cannot send headers.
    This route is intentionally open for the local network.
    """
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict) or "n" not in raw:
        with _uplink_lock:
            _wifi_rx_state["bad"] += 1
        return jsonify(error="invalid packet: must be a JSON object with 'n' key"), 400

    result = ingest_packet(raw, source="wifi")
    if result is None:
        return jsonify(error="packet processing failed"), 400
    return jsonify(ok=True)


@app.route("/api/commands", methods=["GET"])
def wifi_commands():
    """WiFi receiver polls for pending uplink commands (heartbeat).

    Also records the time of the poll as the receiver heartbeat.
    Returns {"cmds": [...]}, empties the queue.
    No authentication token — the receiver cannot send headers.
    """
    with _uplink_lock:
        _wifi_rx_state["last_rx_poll_t"] = time.time()
        out = list(_uplink_queue)
        _uplink_queue.clear()

    # Mark queued commands as SENT in the DB
    if out:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    UPDATE commands SET status='SENT', sent_at=datetime('now')
                    WHERE status='QUEUED' AND session_id=?
                """, (current_session_id,))
                conn.commit()
        except Exception as e:
            print(f"⚠️  Command status update error: {e}")

    return jsonify(cmds=out)


@app.route("/db/stats", methods=["GET"])
def db_stats(): 
    try:
        conn   = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), MIN(received_at), MAX(received_at), MAX(altitude) FROM telemetry")
        row = cursor.fetchone()
        conn.close()
        return jsonify({
            "total_packets": row[0],
            "first_packet":  row[1],
            "last_packet":   row[2],
            "peak_altitude": row[3],
            "db_path":       DB_PATH,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/db/export", methods=["GET"])
@app.route("/api/export", methods=["GET"])
def db_export():
    """Export all telemetry to CSV with metadata."""
    try:
        conn   = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """SELECT session_id, packet_id, timestamp, mission_time, altitude, temperature, pressure, humidity,
                      ax, ay, az, gx, gy, gz, lat, lon, battery_voltage, rssi, snr,
                      launched, ejected, sim, received_at
               FROM telemetry ORDER BY id"""
        )
        rows    = cursor.fetchall()
        headers = ["session_id", "packet_id", "timestamp_ms", "mission_time_s", "altitude_m", "temp_c", 
                   "pressure_pa", "humidity_pct", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps",
                   "lat", "lon", "battery_v", "rssi_dbm", "snr_db", "launched", "ejected", "sim", "received_at"]
        conn.close()

        buf = io.StringIO()
        writer = csv.writer(buf)
        # Write metadata header
        writer.writerow(["# CanSat Telemetry Export"])
        writer.writerow(["# Generated:", time.strftime("%Y-%m-%d %H:%M:%S")])
        writer.writerow(["# Total Packets:", len(rows)])
        writer.writerow([])
        
        writer.writerow(headers)
        writer.writerows(rows)
        buf.seek(0)

        return send_file(
            io.BytesIO(buf.getvalue().encode()),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"telemetry_export_{time.strftime('%Y%m%d_%H%M%S')}.csv",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/db/clear", methods=["POST"])
@require_gs_token
def db_clear():
    global flight_data, latest_data
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM telemetry")
            conn.commit()
        with _lock:
            flight_data.clear()
            latest_data.clear()
        ai_cache.update({"analysis": None, "timestamp": None, "provider": None,
                         "phase": None, "anomalies": [], "running": False})
        return jsonify({"status": "Database and cache cleared"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/live/reset", methods=["POST", "GET"])
def live_reset():
    """Reset live flight buffer and start a fresh session on dashboard reload."""
    global current_session_id, last_ts_value, data_version, _last_logged_phase, _session_boot_ts
    now = time.time()
    current_session_id = f"session_{int(now)}"
    _last_logged_phase = "PRE-LAUNCH"
    _session_boot_ts = None
    with _lock:
        flight_data.clear()
        latest_data.clear()
        packet_id_history.clear()
        _last_gps_fix.clear()
        _track_state.clear()
        _track_state.update(_new_track_state())
        data_version += 1
    with _uplink_lock:
        _wifi_rx_state["session_start_t"]  = None
        _wifi_rx_state["last_packet_t"]    = None
        _wifi_rx_state["lost"]             = 0
        _wifi_rx_state["bad"]              = 0
        _wifi_rx_state["last_seq"]         = None
        _wifi_rx_state["session_launched"] = False
        _wifi_rx_state["session_ejected"]  = False
    ai_cache.update({"analysis": None, "timestamp": None, "provider": None,
                     "phase": "PRE-LAUNCH", "anomalies": [], "running": False})
    return jsonify(ok=True, session_id=current_session_id)


# =====================================================================
# COMMAND & CONTROL ENDPOINT
# =====================================================================
@app.route("/cmd", methods=["POST"])
@rate_limit
@require_gs_token
def receive_command():
    """
    Receive command from ground station uplink.
    Expected payload: { "command": "ARM" | "DEPLOY" | "ABORT", "packet_id": int, "timestamp": int }
    """
    try:
        payload = request.get_json(silent=True) or {}
        if not payload:
            return jsonify({"status": "error", "message": "JSON body required"}), 400
        command = payload.get("command", "").upper()
        packet_id = payload.get("packet_id", 0)
        timestamp = payload.get("timestamp", int(time.time() * 1000))
        
        # Validate command
        valid_commands = ["ARM", "DEPLOY", "ABORT", "SIM_MODE_ON", "SIM_MODE_OFF"]
        if command not in valid_commands:
            return jsonify({"status": "error", "message": f"Invalid command: {command}"}), 400
        
        # Log command to database with full PDR schema
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    INSERT INTO commands (session_id, command, status, packet_id, sent_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                """, (current_session_id, command, "SENT", packet_id))
                conn.commit()
        except Exception as db_err:
            print(f"⚠️  Command DB logging error: {db_err}")
        
        # Log to console
        print(f"📡 COMMAND RECEIVED: {command} (packet_id={packet_id}, session={current_session_id})")
        
        # Return acknowledgment
        return jsonify({
            "status": "sent",
            "command": command,
            "packet_id": packet_id,
            "server_timestamp": int(time.time() * 1000)
        }), 200
    
    except Exception as e:
        print(f"⚠️  Command processing error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# NEW API ENDPOINTS - PDR REQUIREMENTS
# =====================================================================

@app.route("/api/commands/log", methods=["GET"])
def get_command_log():
    """Retrieve command history with status tracking."""
    try:
        session = request.args.get("session_id", current_session_id)
        limit = int(request.args.get("limit", "100"))
        cmd_filter = (request.args.get("filter") or "").strip().upper()
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            if cmd_filter:
                cursor.execute("""
                    SELECT id, session_id, command, status, packet_id, sent_at, acked_at
                    FROM commands
                    WHERE session_id = ? AND command = ?
                    ORDER BY id DESC
                    LIMIT ?
                """, (session, cmd_filter, limit))
            else:
                cursor.execute("""
                    SELECT id, session_id, command, status, packet_id, sent_at, acked_at
                    FROM commands
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                """, (session, limit))
            rows = cursor.fetchall()
        
        commands = [
            {
                "id": row[0],
                "session_id": row[1],
                "command": row[2],
                "status": row[3],
                "packet_id": row[4],
                "sent_at": row[5],
                "acked_at": row[6]
            }
            for row in rows
        ]
        
        return jsonify({"commands": commands, "count": len(commands)}), 200
    except Exception as e:
        print(f"⚠️  Command log error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/events", methods=["GET"])
def get_events():
    """Retrieve mission event log."""
    try:
        session = request.args.get("session_id", current_session_id)
        limit = int(request.args.get("limit", "100"))
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, session_id, event_type, event_note, packet_id, altitude, created_at
                FROM mission_events
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (session, limit))
            rows = cursor.fetchall()
        
        events = [
            {
                "id": row[0],
                "session_id": row[1],
                "event_type": row[2],
                "event_note": row[3],
                "packet_id": row[4],
                "altitude": row[5],
                "created_at": row[6]
            }
            for row in rows
        ]
        
        return jsonify({"events": events, "count": len(events)}), 200
    except Exception as e:
        print(f"⚠️  Events retrieval error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/events", methods=["POST"])
@rate_limit
def create_event():
    """Create a new mission event (user-initiated or automatic)."""
    try:
        payload = request.get_json(silent=True) or {}
        if not payload:
            return jsonify({"error": "JSON body required"}), 400
        event_type = payload.get("event_type", "MANUAL")
        event_note = payload.get("event_note", "")
        
        # Possible event types: CONNECT, LAUNCH, APOGEE, EJECTION, LAND, MANUAL
        valid_types = ["CONNECT", "LAUNCH", "APOGEE", "EJECTION", "LAND", "MANUAL"]
        if event_type not in valid_types:
            return jsonify({"error": f"Invalid event type: {event_type}"}), 400
        
        with _lock:
            latest = latest_data.copy()
        
        packet_id = latest.get("packet_id", 0)
        altitude = latest.get("alt", 0)
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO mission_events (session_id, event_type, event_note, packet_id, altitude)
                VALUES (?, ?, ?, ?, ?)
            """, (current_session_id, event_type, event_note, packet_id, altitude))
            conn.commit()
            cursor.execute("SELECT last_insert_rowid()")
            event_id = cursor.fetchone()[0]
        
        print(f"📌 EVENT LOGGED: {event_type} @ {altitude:.1f}m")
        
        return jsonify({
            "status": "created",
            "event_id": event_id,
            "session_id": current_session_id,
            "event_type": event_type
        }), 201
    except Exception as e:
        print(f"⚠️  Event creation error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sessions", methods=["GET"])
def get_sessions():
    """Retrieve list of all flight sessions (missions table + telemetry fallback)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT session_id, start_time, end_time, team_name, cansat_id, location
            FROM missions
            ORDER BY start_time DESC
        """)
        rows = cursor.fetchall()
        sessions = [
            {
                "session_id": row[0],
                "start_time": row[1],
                "end_time": row[2],
                "team_name": row[3],
                "cansat_id": row[4],
                "location": row[5],
            }
            for row in rows
        ]
        known = {s["session_id"] for s in sessions}

        # Fallback: unique session_ids from telemetry
        cursor.execute("""
            SELECT session_id, MIN(received_at), MAX(received_at), COUNT(*), MAX(altitude)
            FROM telemetry
            WHERE session_id IS NOT NULL AND session_id != ''
            GROUP BY session_id
            ORDER BY MAX(received_at) DESC
            LIMIT 50
        """)
        for row in cursor.fetchall():
            sid = row[0]
            if sid in known:
                continue
            sessions.append({
                "session_id": sid,
                "start_time": row[1],
                "end_time": row[2],
                "team_name": "VIKRAM",
                "cansat_id": "CAN-7USAT",
                "location": None,
                "packet_count": row[3],
                "peak_altitude": row[4],
            })
        conn.close()

        # Always include live session
        if current_session_id and current_session_id not in {s["session_id"] for s in sessions}:
            sessions.insert(0, {
                "session_id": current_session_id,
                "start_time": None,
                "end_time": None,
                "team_name": "VIKRAM",
                "cansat_id": "CAN-7USAT",
                "location": "live",
            })

        return jsonify({"sessions": sessions, "count": len(sessions), "current": current_session_id}), 200
    except Exception as e:
        print(f"⚠️  Sessions retrieval error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sessions/<session_id>/export", methods=["GET"])
@rate_limit
def export_session(session_id):
    """Export session-specific telemetry as CSV."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get mission metadata (optional)
        cursor.execute("SELECT * FROM missions WHERE session_id = ?", (session_id,))
        mission = cursor.fetchone()
        
        # Get telemetry data for session
        cursor.execute("""
            SELECT * FROM telemetry WHERE session_id = ? ORDER BY id
        """, (session_id,))
        
        cols = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        conn.close()

        if not rows and not mission:
            return jsonify({"error": f"Session {session_id} not found"}), 404
        
        # Build CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header with metadata
        writer.writerow(["# Session Export"])
        writer.writerow(["Session ID", session_id])
        if mission:
            writer.writerow(["Team Name", mission[3]])
            writer.writerow(["CanSat ID", mission[4]])
            writer.writerow(["Location", mission[5]])
            writer.writerow(["Start Time", mission[1]])
            writer.writerow(["End Time", mission[2]])
        writer.writerow([])
        
        # Telemetry data
        writer.writerow(cols)
        writer.writerows(rows)
        
        csv_bytes = output.getvalue().encode("utf-8")
        
        return send_file(
            io.BytesIO(csv_bytes),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"telemetry_{session_id}.csv"
        )
    except Exception as e:
        print(f"⚠️  Session export error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/anomalies", methods=["GET"])
def get_anomalies():
    """Retrieve anomaly history for current or specified session."""
    try:
        session = request.args.get("session_id", current_session_id)
        anomaly_type = request.args.get("type", None)
        severity = request.args.get("severity", None)
        limit = int(request.args.get("limit", "100"))
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        query = "SELECT id, session_id, packet_id, anomaly_type, severity, description, timestamp_ms FROM anomaly_events WHERE session_id = ?"
        params = [session]
        
        if anomaly_type:
            query += " AND anomaly_type = ?"
            params.append(anomaly_type)
        
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        
        query += " ORDER BY timestamp_ms DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        anomalies = [
            {
                "id": row[0],
                "session_id": row[1],
                "packet_id": row[2],
                "anomaly_type": row[3],
                "severity": row[4],
                "description": row[5],
                "timestamp_ms": row[6]
            }
            for row in rows
        ]
        
        return jsonify({"anomalies": anomalies, "count": len(anomalies)}), 200
    except Exception as e:
        print(f"⚠️  Anomalies retrieval error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/link-margin", methods=["GET"])
def get_link_margin():
    """Retrieve link margin metrics (RSSI, SNR, path loss, link margin).

    When rssi or snr is None (new E220 hardware), returns n/a strings instead
    of computed numbers so the frontend can display 'n/a' rather than garbage.
    """
    try:
        with _lock:
            latest = latest_data.copy()

        tx_power       = 14    # dBm (typical LoRa TX)
        rx_sensitivity = -137  # dBm (E220 868 MHz typical)

        if not latest:
            return jsonify({
                "rssi_dbm": None, "snr_db": None,
                "path_loss_db": None, "link_margin_db": None,
                "snr_quality": "n/a",
                "tx_power_dbm": tx_power, "rx_sensitivity_dbm": rx_sensitivity,
                "packet_id": 0, "timestamp": 0,
                "e220_mode": True,
            }), 200

        rssi = latest.get("rssi")  # None for new compact-format packets
        snr  = latest.get("snr")   # None for new compact-format packets

        if rssi is not None:
            path_loss   = tx_power - rssi
            link_margin = rssi - rx_sensitivity
        else:
            path_loss   = None
            link_margin = None

        if snr is None:
            snr_quality = "n/a"
        elif snr >= 10:
            snr_quality = "EXCELLENT"
        elif snr >= 7:
            snr_quality = "GOOD"
        elif snr >= 0:
            snr_quality = "FAIR"
        else:
            snr_quality = "POOR"

        return jsonify({
            "rssi_dbm":           rssi,
            "snr_db":             snr,
            "path_loss_db":       path_loss,
            "link_margin_db":     link_margin,
            "snr_quality":        snr_quality,
            "tx_power_dbm":       tx_power,
            "rx_sensitivity_dbm": rx_sensitivity,
            "packet_id":          latest.get("packet_id", 0),
            "timestamp":          latest.get("ts", 0),
            "e220_mode":          rssi is None,
        }), 200
    except Exception as e:
        print(f"⚠️  Link margin error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/port", methods=["POST"])
@rate_limit
@require_gs_token
def switch_port():
    """Dynamically switch to a different COM port."""
    try:
        global SERIAL_PORT, port_reload_flag
        
        payload = request.get_json(silent=True) or {}
        new_port = payload.get("port", "COM3")
        
        available = list_available_ports()
        if new_port not in available and new_port != SERIAL_PORT:
            return jsonify({"error": f"Port {new_port} not available", "available_ports": available}), 400
        
        SERIAL_PORT = new_port
        with _lock:
            port_reload_flag = True
        print(f"🔌 Serial port switched to {SERIAL_PORT}")
        
        return jsonify({
            "status": "switched",
            "port": new_port,
            "available_ports": available
        }), 200
    except Exception as e:
        print(f"⚠️  Port switch error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/model", methods=["GET"])
def get_model():
    for path in [
        os.path.join(os.path.dirname(__file__), "66-scifi-cartoon-rocket-obj", "scifi_cartoon_rocket.obj"),
        os.path.join(os.path.dirname(__file__), "scifi_cartoon_rocket.obj"),
    ]:
        if os.path.exists(path):
            return send_file(path, mimetype="application/octet-stream")
    return jsonify({"error": "Model not found"}), 404


@app.route("/api/materials", methods=["GET"])
def get_materials():
    mtl = os.path.join(os.path.dirname(__file__), "66-scifi-cartoon-rocket-obj", "scifi_cartoon_rocket.mtl")
    if os.path.exists(mtl):
        return send_file(mtl, mimetype="application/octet-stream")
    return jsonify({"error": "Materials file not found"}), 404


@app.route("/api/model-stl", methods=["GET"])
def get_model_stl():
    stl = os.path.join(os.path.dirname(__file__), "Retro_Rocket.STL")
    if os.path.exists(stl):
        return send_file(stl, mimetype="application/octet-stream", download_name="Retro_Rocket.STL")
    return jsonify({"error": "STL model not found"}), 404


@app.route("/api/simulated/<filename>", methods=["GET"])
def get_simulated_image(filename):
    # Prevent path traversal
    filename = os.path.basename(filename)
    path = os.path.join(os.path.dirname(__file__), "simulated output", filename)
    if os.path.exists(path):
        return send_file(path, mimetype="image/png")
    return jsonify({"error": "Image not found"}), 404


@app.route("/api/simulated", methods=["GET"])
def list_simulated_images():
    sim_dir = os.path.join(os.path.dirname(__file__), "simulated output")
    if os.path.exists(sim_dir):
        images = [f for f in os.listdir(sim_dir) if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))]
        return jsonify({"images": images})
    return jsonify({"images": []})


# =====================================================================
# VIDEO STREAMING
# =====================================================================

# ── Config ────────────────────────────────────────────────────────────
#   Override ESP32_CAM_URL in .env to point at your ESP32-CAM IP:
#     ESP32_CAM_URL=http://192.168.1.100:81/stream
ESP32_CAM_URL = os.getenv("ESP32_CAM_URL", "http://192.168.1.100:81/stream")

# Lazy-init USB camera so the server starts even with no webcam attached
_usb_camera = None
_cam_lock    = threading.Lock()

def _get_camera():
    """Return a cv2.VideoCapture object, creating it on first call."""
    global _usb_camera
    if _cv2 is None:
        return None
    with _cam_lock:
        if _usb_camera is None or not _usb_camera.isOpened():
            _usb_camera = _cv2.VideoCapture(0)
    return _usb_camera


def _gen_local_frames():
    """Yield MJPEG frames from the local USB webcam."""
    cam = _get_camera()
    if cam is None:
        return
    while True:
        ok, frame = cam.read()
        if not ok:
            break
        _, buf = _cv2.imencode(
            ".jpg", frame,
            [_cv2.IMWRITE_JPEG_QUALITY, int(os.getenv("VIDEO_QUALITY", "70"))]
        )
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + buf.tobytes()
            + b"\r\n"
        )


@app.route("/video_feed")
def video_feed():
    """
    Option A – Local USB webcam on the ground-station PC.
    Opens camera index 0; change VIDEO_DEVICE_INDEX in .env to use a
    different camera (e.g. 1 for an external USB cam).
    """
    if _cv2 is None:
        return jsonify({"error": "opencv-python not installed"}), 503

    device = int(os.getenv("VIDEO_DEVICE_INDEX", "0"))

    def gen():
        cam = _cv2.VideoCapture(device)
        if not cam.isOpened():
            return
        try:
            while True:
                ok, frame = cam.read()
                if not ok:
                    break
                _, buf = _cv2.imencode(
                    ".jpg", frame,
                    [_cv2.IMWRITE_JPEG_QUALITY, int(os.getenv("VIDEO_QUALITY", "70"))]
                )
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + buf.tobytes()
                    + b"\r\n"
                )
        finally:
            cam.release()

    return Response(
        stream_with_context(gen()),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/video_feed_esp")
def video_feed_esp():
    """
    Option B – ESP32-CAM inside the CanSat or mounted on the ground station.
    Set ESP32_CAM_URL in .env, e.g.:
        ESP32_CAM_URL=http://192.168.1.100:81/stream
    Flash the built-in CameraWebServer example onto your AI-Thinker ESP32-CAM,
    note the IP from Serial Monitor, and update the URL above.
    """
    if _requests is None:
        return jsonify({"error": "requests package not installed"}), 503

    def proxy():
        try:
            r = _requests.get(ESP32_CAM_URL, stream=True, timeout=10)
            for chunk in r.iter_content(chunk_size=1024):
                yield chunk
        except Exception as exc:
            app.logger.warning("ESP32-CAM proxy error: %s", exc)

    # The ESP32 CameraWebServer sketch uses this multipart boundary
    return Response(
        stream_with_context(proxy()),
        content_type="multipart/x-mixed-replace; boundary=123456789000000000000987654321"
    )


@app.route("/api/video/status")
def video_status():
    """Quick health-check for the video integration."""
    cam_ok = False
    if _cv2 is not None:
        cap = _cv2.VideoCapture(int(os.getenv("VIDEO_DEVICE_INDEX", "0")))
        cam_ok = cap.isOpened()
        cap.release()
    return jsonify({
        "opencv_available": _cv2 is not None,
        "requests_available": _requests is not None,
        "local_camera_detected": cam_ok,
        "esp32_cam_url": ESP32_CAM_URL,
    })





# =====================================================================
# MAIN
# =====================================================================
if __name__ == "__main__":
    print("\n🚀 Starting Flight Telemetry Server…")
    print(f"   Serial     : {SERIAL_PORT} @ {BAUD_RATE} baud")
    print(f"   WiFi RX    : POST /api/telemetry  GET /api/commands  (no token, local net)")
    print(f"   AI interval: every {AI_ANALYSIS_INTERVAL}s")
    print(f"   Launch thresholds: accel>{LAUNCH_ACCEL_THRESHOLD_G}g  alt>{LAUNCH_ALT_THRESHOLD_M}m")

    init_db()

    # Initialise shared GPS / track state
    _last_gps_fix.clear()
    _track_state.update(_new_track_state())

    # Start live telemetry buffer clean for fresh flight session
    # Historical data remains archived in SQLite (telemetry.db) for session exports/queries
    print("   Live buffer initialized clean (fresh session)")

    debug_mode = os.getenv("FLASK_DEBUG", "0") == "1"
    gs_host = os.getenv("GS_BIND", "0.0.0.0")   # listen on all interfaces so WiFi receiver can reach
    gs_port = int(os.getenv("PORT", os.getenv("GS_PORT", "5000")))

    def _start_background_threads():
        threading.Thread(target=read_serial, daemon=True, name="serial-reader").start()
        threading.Thread(target=continuous_ai_analysis, daemon=True, name="ai-analyser").start()

    # Avoid double serial open when the Werkzeug reloader is on.
    if not debug_mode or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _start_background_threads()

    print(f"\n   Dashboard  : http://localhost:{gs_port}")
    print(f"   Receiver must POST to: http://<PC-LAN-IP>:{gs_port}/api/telemetry")
    print(f"   Test data  : POST http://localhost:{gs_port}/test-data  (X-GS-Token)")
    print(f"   AI latest  : http://localhost:{gs_port}/api/ai-analysis/latest")
    print(f"   CSV export : http://localhost:{gs_port}/db/export\n")

    app.run(host=gs_host, port=gs_port, debug=False, use_reloader=False, threaded=True)

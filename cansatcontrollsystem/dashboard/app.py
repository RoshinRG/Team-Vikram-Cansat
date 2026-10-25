"""CanSat ground-station backend.

Receiver (Glyph C6) --WiFi/HTTP--> this Flask server --SSE--> browser dashboard.

Endpoints
  POST /api/telemetry   receiver posts one compact JSON packet from the CanSat
  GET  /api/commands    receiver polls for pending uplink commands (also its heartbeat)
  POST /api/command     dashboard queues a command  {"relay":0|1}, {"servo":0-180} or {"cal":1} (zero altitude)
  GET  /api/stream      Server-Sent Events: realtime telemetry + status
  GET  /api/history     recent packets
  GET  /api/status      link / counters
  GET  /api/export      download the CSV log
"""
import csv
import json
import os
import queue
import threading
import time
from collections import deque
from datetime import datetime
from math import atan2, degrees, sqrt

from flask import Flask, Response, jsonify, render_template, request, send_file

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


@app.after_request
def no_cache(resp):
    if not request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"telemetry_{datetime.now():%Y%m%d_%H%M%S}.csv")

LINK_TIMEOUT_S = 10      # no packet for this long -> "link lost"
RX_TIMEOUT_S = 5         # receiver not polling for this long -> "receiver offline"

# compact keys sent over LoRa -> readable names
KEYMAP = {
    "n": "seq", "bt": "temp_bmp", "p": "pressure", "a": "altitude",
    "dt": "temp_dht", "h": "humidity", "mq": "mq135",
    "ax": "acc_x", "ay": "acc_y", "az": "acc_z",
    "gx": "gyro_x", "gy": "gyro_y", "gz": "gyro_z",
    "la": "lat", "lo": "lon", "ga": "gps_alt", "s": "sats",
    "v": "bus_v", "i": "current_ma", "r": "relay", "sv": "servo",
}

lock = threading.Lock()
history = deque(maxlen=3000)
subscribers = set()
commands = deque()
state = {
    "packets": 0,
    "lost": 0,
    "bad": 0,
    "last_seq": None,
    "last_packet_t": None,
    "last_rx_poll_t": None,
}
CSV_FIELDS = [*KEYMAP.values(), "pitch", "roll", "power_mw", "gps_fix", "ts"]
csv_started = False


def publish(event, data):
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    for q in list(subscribers):
        try:
            q.put_nowait(msg)
        except queue.Full:
            pass


def build_status():
    now = time.time()
    lp, rp = state["last_packet_t"], state["last_rx_poll_t"]
    total = state["packets"] + state["lost"]
    return {
        "packets": state["packets"],
        "lost": state["lost"],
        "bad": state["bad"],
        "loss_pct": round(100 * state["lost"] / total, 1) if total else 0,
        "link_ok": lp is not None and now - lp < LINK_TIMEOUT_S,
        "rx_online": rp is not None and now - rp < RX_TIMEOUT_S,
        "last_packet_age": round(now - lp, 1) if lp else None,
        "pending_commands": len(commands),
        "server_time": int(now * 1000),
    }


def enrich(raw):
    """Rename the compact keys and add values derived only from fields that really arrived."""
    p = {name: raw[k] for k, name in KEYMAP.items() if k in raw}
    if all(k in p for k in ("acc_x", "acc_y", "acc_z")):
        ax, ay, az = p["acc_x"], p["acc_y"], p["acc_z"]
        p["pitch"] = round(degrees(atan2(-ax, sqrt(ay * ay + az * az))), 1)
        p["roll"] = round(degrees(atan2(ay, az)), 1)
    if "bus_v" in p and "current_ma" in p:
        p["power_mw"] = round(p["bus_v"] * p["current_ma"], 1)
    p["gps_fix"] = "lat" in p and "lon" in p
    p["ts"] = int(time.time() * 1000)
    return p


def log_csv(p):
    global csv_started
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore", restval="")
        if not csv_started:
            w.writeheader()
            csv_started = True
        w.writerow(p)


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/telemetry")
def telemetry():
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict) or "n" not in raw:
        with lock:
            state["bad"] += 1
        return jsonify(error="invalid packet"), 400

    p = enrich(raw)
    with lock:
        seq = p["seq"]
        last = state["last_seq"]
        if last is not None:
            if seq > last + 1:
                state["lost"] += seq - last - 1
            elif seq <= last:      # CanSat rebooted
                pass
        state["last_seq"] = seq
        state["packets"] += 1
        state["last_packet_t"] = time.time()
        history.append(p)
        log_csv(p)
        status = build_status()

    publish("telemetry", p)
    publish("status", status)
    return jsonify(ok=True)


@app.get("/api/commands")
def get_commands():
    """Receiver polls this; acts as its heartbeat too."""
    with lock:
        state["last_rx_poll_t"] = time.time()
        out = list(commands)
        commands.clear()
    return jsonify(cmds=out)


@app.post("/api/command")
def post_command():
    body = request.get_json(silent=True) or {}
    cmd = None
    if "relay" in body and body["relay"] in (0, 1, True, False):
        cmd = {"r": int(body["relay"])}
    elif body.get("cal"):
        cmd = {"cal": 1}
    elif "servo" in body:
        try:
            cmd = {"sv": max(0, min(180, int(body["servo"])))}
        except (TypeError, ValueError):
            pass
    if cmd is None:
        return jsonify(error="invalid command"), 400
    with lock:
        commands.append(json.dumps(cmd, separators=(",", ":")))
    return jsonify(ok=True, queued=cmd)


@app.get("/api/status")
def status():
    with lock:
        return jsonify(build_status())


@app.get("/api/history")
def get_history():
    n = min(int(request.args.get("n", 300)), 3000)
    with lock:
        return jsonify(list(history)[-n:])


@app.get("/api/export")
def export():
    if not os.path.exists(LOG_FILE):
        return jsonify(error="no data yet"), 404
    return send_file(LOG_FILE, as_attachment=True, download_name=os.path.basename(LOG_FILE))


@app.get("/api/stream")
def stream():
    q = queue.Queue(maxsize=200)
    subscribers.add(q)

    def gen():
        try:
            with lock:
                last = history[-1] if history else None
                st = build_status()
            yield f"event: status\ndata: {json.dumps(st)}\n\n"
            if last:
                yield f"event: telemetry\ndata: {json.dumps(last)}\n\n"
            while True:
                try:
                    yield q.get(timeout=2)
                except queue.Empty:
                    with lock:
                        st = build_status()
                    yield f"event: status\ndata: {json.dumps(st)}\n\n"
        finally:
            subscribers.discard(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    print(f"CSV log: {LOG_FILE}")
    print("Dashboard: http://localhost:5000   (receiver must use http://<this-PC-LAN-IP>:5000)")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True, debug=False)

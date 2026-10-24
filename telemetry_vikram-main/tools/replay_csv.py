#!/usr/bin/env python3
"""
tools/replay_csv.py — Replay a recorded CanSat telemetry CSV into the ground-station.

Usage
-----
  python tools/replay_csv.py [--file sample_data/telemetry_sample.csv]
                             [--url  http://localhost:5000]
                             [--rate 2]        # packets per second (default 2)
                             [--loop]          # restart at end of file

The CSV can be:
  - The compact-format log produced by the SOURCE dashboard (columns: seq, temp_bmp, …)
  - Any CSV exported by *this* dashboard (/db/export or session export)

The script maps each row to the compact JSON format that POST /api/telemetry expects,
so the full ingest_packet() pipeline (phase detection, DB, SSE, AI…) runs normally.

Secrets / authentication: none needed.  /api/telemetry is intentionally open.
"""

import argparse
import csv
import json
import sys
import time
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Column mapping: CSV column name → compact JSON key sent to /api/telemetry
# ---------------------------------------------------------------------------
# Source-format CSV (from ahamedfaisal-dot/cansatcontrollsystem dashboard logs)
_SRC_MAP = {
    "seq":        "n",
    "temp_bmp":   "bt",
    "pressure":   "p",
    "altitude":   "a",
    "temp_dht":   "dt",
    "humidity":   "h",
    "mq135":      "mq",
    "acc_x":      "ax",
    "acc_y":      "ay",
    "acc_z":      "az",
    "gyro_x":     "gx",
    "gyro_y":     "gy",
    "gyro_z":     "gz",
    "lat":        "la",
    "lon":        "lo",
    "gps_alt":    "ga",
    "sats":       "s",
    "bus_v":      "v",
    "current_ma": "i",
    "relay":      "r",
    "servo":      "sv",
}

# Target-export CSV (from /db/export)
_TARGET_MAP = {
    "packet_id":       "n",
    "temperature":     "bt",
    "pressure":        "p",
    "altitude":        "a",
    "humidity":        "h",
    "ax":              "ax",
    "ay":              "ay",
    "az":              "az",
    "gx":              "gx",
    "gy":              "gy",
    "gz":              "gz",
    "lat":             "la",
    "lon":             "lo",
    "battery_voltage": "v",
    "rssi":            None,   # not forwarded (transparent mode has none)
    "snr":             None,
}

_NUMERIC = {"n", "bt", "p", "a", "dt", "h", "mq", "ax", "ay", "az",
            "gx", "gy", "gz", "la", "lo", "ga", "s", "v", "i", "r", "sv"}


def _coerce(key: str, val: str):
    """Convert a CSV string to the appropriate Python type."""
    stripped = val.strip()
    if stripped in ("", "nan", "None", "null", "False", "True"):
        if stripped == "True":
            return 1
        if stripped == "False":
            return 0
        return None
    if key in _NUMERIC:
        try:
            f = float(stripped)
            # Use int for integer-like fields
            if key in ("n", "s", "r", "sv"):
                return int(f)
            return f
        except ValueError:
            return None
    return stripped


def _row_to_compact(row: dict, col_map: dict, seq_override: int | None = None) -> dict | None:
    """Map a CSV row dict to the compact JSON format for /api/telemetry."""
    out: dict = {}
    for csv_col, compact_key in col_map.items():
        if compact_key is None:
            continue
        val_str = row.get(csv_col)
        if val_str is None:
            continue
        val = _coerce(compact_key, val_str)
        if val is None:
            continue
        out[compact_key] = val

    # "n" is mandatory
    if "n" not in out:
        if seq_override is not None:
            out["n"] = seq_override
        else:
            return None

    # Drop zero GPS (invalid)
    if out.get("la", 1) == 0 and out.get("lo", 1) == 0:
        out.pop("la", None)
        out.pop("lo", None)

    return out


def detect_format(headers: list[str]) -> dict:
    """Detect which CSV format this is and return the appropriate column map."""
    if "seq" in headers or "temp_bmp" in headers:
        print("[replay] Detected: SOURCE dashboard CSV format")
        return _SRC_MAP
    elif "packet_id" in headers or "altitude" in headers:
        print("[replay] Detected: TARGET export CSV format")
        return _TARGET_MAP
    else:
        print("[replay] Unknown CSV format — trying SOURCE map (best effort)", file=sys.stderr)
        return _SRC_MAP


def post_packet(url: str, payload: dict) -> bool:
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode(errors='ignore')}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  POST error: {e}", file=sys.stderr)
        return False


def replay(csv_path: str, url: str, rate: float, loop: bool):
    endpoint = url.rstrip("/") + "/api/telemetry"
    delay    = 1.0 / max(rate, 0.01)
    run      = True
    seq      = 0

    while run:
        sent = ok = 0
        with open(csv_path, newline="", encoding="utf-8") as fh:
            # Skip comment / metadata lines
            lines = [ln for ln in fh if not ln.startswith("#")]

        reader = csv.DictReader(lines)
        col_map = detect_format(list(reader.fieldnames or []))

        print(f"[replay] Replaying {csv_path!r} -> {endpoint}  ({rate} pkt/s)")
        t0 = time.time()

        for row in reader:
            pkt = _row_to_compact(row, col_map, seq_override=seq)
            if pkt is None:
                continue
            seq += 1
            ok += int(post_packet(endpoint, pkt))
            sent += 1
            elapsed = time.time() - t0
            print(f"\r  Sent {sent}  OK {ok}  seq={pkt['n']}  ({elapsed:.1f}s)", end="", flush=True)
            time.sleep(delay)

        print(f"\n[replay] Done: {sent} packets sent, {ok} accepted.")
        run = loop


def main():
    parser = argparse.ArgumentParser(description="Replay a CanSat telemetry CSV into the ground-station.")
    parser.add_argument("--file", default="sample_data/telemetry_sample.csv",
                        help="Path to the CSV file (default: sample_data/telemetry_sample.csv)")
    parser.add_argument("--url",  default="http://localhost:5000",
                        help="Base URL of the ground-station server (default: http://localhost:5000)")
    parser.add_argument("--rate", type=float, default=2.0,
                        help="Replay rate in packets per second (default: 2)")
    parser.add_argument("--loop", action="store_true",
                        help="Loop the file indefinitely")
    args = parser.parse_args()

    replay(args.file, args.url, args.rate, args.loop)


if __name__ == "__main__":
    main()

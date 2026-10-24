"""Local CanSat flight analyser — pandas KPIs + threshold model + PDF.

Replaces the NVIDIA NIM client. Live packets are read from the Flask process
(the ground-station API), not scraped from an external website.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None

PROVIDER = "vikram-flight-ml"
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

PHASE_DETAIL = {
    "PRE-LAUNCH": "System armed, awaiting ignition event.",
    "IGNITION": "Ignition transient detected. Monitoring for full thrust.",
    "POWERED_ASCENT": "Positive net thrust confirmed. Structural loads nominal.",
    "COAST": "Motor burnout. Vehicle coasting under aerodynamic forces.",
    "APOGEE": "Peak altitude reached. Monitoring for recovery deployment.",
    "DESCENT": "Descent phase active. Tracking terminal velocity.",
    "RECOVERY": "Ejection confirmed. Recovery system deployed.",
}


def _num(value, default=None):
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def packets_to_dataframe(packets: list):
    if pd is None or not packets:
        return None
    df = pd.json_normalize(list(packets))
    if "ts" in df.columns:
        df["timestamp"] = pd.to_datetime(df["ts"], unit="ms", errors="coerce", utc=True)
        df = df.sort_values("ts", kind="mergesort")
    ax = pd.to_numeric(df["ax"], errors="coerce") if "ax" in df.columns else 0
    ay = pd.to_numeric(df["ay"], errors="coerce") if "ay" in df.columns else 0
    df["lateral_g"] = ((ax.fillna(0) ** 2) + (ay.fillna(0) ** 2)) ** 0.5
    if "alt" in df.columns and "ts" in df.columns:
        alt = pd.to_numeric(df["alt"], errors="coerce")
        ts_s = pd.to_numeric(df["ts"], errors="coerce") / 1000.0
        dt = ts_s.diff()
        df["v_z"] = alt.diff() / dt.replace(0, pd.NA)
    return df


def process_data(packets: list) -> tuple:
    """Flatten packets, compute KPIs, apply threshold anomaly rules."""
    packets = list(packets or [])
    df = packets_to_dataframe(packets)
    latest = packets[-1] if packets else {}

    def series_stat(name, fn):
        if df is None or name not in df.columns:
            vals = [_num(p.get(name)) for p in packets]
            vals = [v for v in vals if v is not None]
            return fn(vals) if vals else None
        s = pd.to_numeric(df[name], errors="coerce").dropna()
        return float(fn(s)) if len(s) else None

    t0 = _num(packets[0].get("ts")) if packets else None
    t1 = _num(latest.get("ts")) if latest else None
    duration_s = ((t1 - t0) / 1000.0) if t0 is not None and t1 is not None else 0.0

    pids = [_num(p.get("packet_id")) for p in packets]
    pids = [int(p) for p in pids if p is not None]
    packet_loss_pct = 0.0
    if len(pids) >= 2:
        expected = max(pids) - min(pids) + 1
        if expected > 0:
            packet_loss_pct = max(0.0, (1 - len(set(pids)) / expected) * 100)

    laterals = []
    if df is not None and "lateral_g" in df.columns:
        laterals = [float(x) for x in df["lateral_g"].dropna().tolist()]
    else:
        laterals = [
            ((_num(p.get("ax"), 0) ** 2) + (_num(p.get("ay"), 0) ** 2)) ** 0.5
            for p in packets
        ]

    v_z_last = None
    if df is not None and "v_z" in df.columns:
        vz = pd.to_numeric(df["v_z"], errors="coerce").dropna()
        if len(vz):
            v_z_last = float(vz.iloc[-1])
    elif len(packets) >= 2:
        dt = (_num(latest.get("ts"), 0) - _num(packets[-2].get("ts"), 0)) / 1000.0
        if dt > 0:
            v_z_last = (_num(latest.get("alt"), 0) - _num(packets[-2].get("alt"), 0)) / dt

    hot = 0
    if df is not None and "temp" in df.columns:
        hot = int((pd.to_numeric(df["temp"], errors="coerce") > 60).sum())
    else:
        hot = sum(1 for p in packets if _num(p.get("temp")) is not None and _num(p.get("temp")) > 60)

    time_range = "N/A"
    if df is not None and "timestamp" in df.columns:
        ts = df["timestamp"].dropna()
        if len(ts):
            time_range = f"{ts.min()} to {ts.max()}"
    elif t0 is not None and t1 is not None:
        time_range = f"{int(t0)}–{int(t1)} ms"

    summary = {
        "record_count": len(packets),
        "time_range": time_range,
        "duration_s": round(duration_s, 1),
        "avg_temp": series_stat("temp", lambda s: sum(s) / len(s) if not hasattr(s, "mean") else s.mean()),
        "max_temp": series_stat("temp", max),
        "max_pressure": series_stat("pressure", max),
        "min_pressure": series_stat("pressure", min),
        "max_alt": series_stat("alt", max),
        "avg_alt": series_stat("alt", lambda s: sum(s) / len(s) if not hasattr(s, "mean") else s.mean()),
        "max_lateral_g": max(laterals) if laterals else 0.0,
        "v_z": v_z_last,
        "avg_batt": series_stat("batt_v", lambda s: sum(s) / len(s) if not hasattr(s, "mean") else s.mean()),
        "min_batt": series_stat("batt_v", min),
        "avg_rssi": series_stat("rssi", lambda s: sum(s) / len(s) if not hasattr(s, "mean") else s.mean()),
        "packet_loss_pct": round(packet_loss_pct, 1),
        "temp_over_60_count": hot,
        "latest": latest,
    }
    if df is not None:
        for key in ("avg_temp", "max_temp", "max_pressure", "min_pressure", "max_alt", "avg_alt", "avg_batt", "min_batt", "avg_rssi"):
            if summary[key] is not None:
                summary[key] = float(summary[key])
    return df, summary


def _fmt(value, digits=1, suffix="", missing="n/a"):
    if value is None:
        return missing
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return missing


def build_report(packets: list, phase: str, anomalies: list) -> tuple[str, dict]:
    """Return (status text, KPI summary) for the dashboard AI panel."""
    _, summary = process_data(packets)
    latest = summary.get("latest") or {}
    texts = []
    for a in anomalies or []:
        if isinstance(a, dict):
            texts.append(a.get("text") or a.get("type") or str(a))
        else:
            texts.append(str(a))

    lat = latest.get("lat")
    lon = latest.get("lon")
    gps = (
        f"{_fmt(lat, 5)}, {_fmt(lon, 5)}"
        if lat is not None and lon is not None else "no fix"
    )
    lateral = summary.get("max_lateral_g") or 0.0
    stability = (
        f"Peak lateral load {lateral:.2f} g — oscillation above 1.5 g, monitor airframe."
        if lateral > 1.5 else
        f"Peak lateral load {lateral:.2f} g — airframe stable."
    )
    vz = summary.get("v_z")
    vz_txt = f"Vertical rate {_fmt(vz, 2, ' m/s')}." if vz is not None else ""
    recs = ", ".join(
        r for r in [
            "Monitor descent rate." if (vz is not None and vz < -15) else "",
            "Verify ejection charge continuity." if phase in ("APOGEE", "DESCENT") and not latest.get("ejected") else "",
            "Check battery before next attempt." if (summary.get("min_batt") is not None and summary["min_batt"] < 3.4) else "",
            "Investigate RF link." if (summary.get("avg_rssi") is not None and summary["avg_rssi"] < -100) else "",
        ] if r
    ) or "No immediate action required. Continue monitoring."

    lines = [
        "LOCAL FLIGHT ANALYSIS (pandas threshold model)",
        "",
        f"Phase: {phase or 'UNKNOWN'}. {PHASE_DETAIL.get(phase, 'Phase indeterminate.')}",
        f"Packets: {summary['record_count']} over {summary['duration_s']} s "
        f"(loss {summary['packet_loss_pct']}%). Peak altitude {_fmt(summary.get('max_alt'), 2, ' m')}.",
        f"{stability} {vz_txt}".strip(),
        f"Environment: T avg {_fmt(summary.get('avg_temp'), 1, '°C')} "
        f"(max {_fmt(summary.get('max_temp'), 1, '°C')}), "
        f"P {_fmt(latest.get('pressure'), 0, ' Pa')}, "
        f"RH {_fmt(latest.get('humidity'), 0, '%')}. GPS {gps}.",
        f"Power {_fmt(latest.get('batt_v'), 2, ' V')} "
        f"(min {_fmt(summary.get('min_batt'), 2, ' V')}). "
        f"RSSI {_fmt(latest.get('rssi'), 0, ' dBm')}.",
        f"Anomalies: {len(texts)}." + ((" " + "; ".join(texts[:6]) + ".") if texts else " None flagged."),
        f"Recommend: {recs}",
    ]
    return "\n".join(lines), summary


if FPDF is not None:
    class TelemetryPDF(FPDF):
        def header(self):
            self.set_font("Helvetica", "B", 14)
            self.cell(0, 10, "VIKRAM CanSat Flight Report", ln=True, align="C")
            self.set_font("Helvetica", "", 9)
            self.cell(
                0, 5,
                f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                ln=True, align="C",
            )
            y = self.get_y()
            self.line(10, y, 200, y)
            self.ln(5)

        def footer(self):
            self.set_y(-15)
            self.set_font("Helvetica", "I", 8)
            self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")
else:
    TelemetryPDF = None


def _pdf_text(value) -> str:
    text = "" if value is None else str(value)
    return text.encode("latin-1", "replace").decode("latin-1")


def generate_pdf_bytes(analysis: str, summary: dict, packets: list, phase: str) -> bytes:
    if FPDF is None:
        raise RuntimeError("fpdf2 is not installed")

    pdf = TelemetryPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Summary Statistics", ln=True)
    pdf.set_font("Helvetica", "", 10)
    rows = [
        ("Phase", phase),
        ("Records", summary.get("record_count")),
        ("Duration (s)", summary.get("duration_s")),
        ("Time range", summary.get("time_range")),
        ("Peak altitude (m)", _fmt(summary.get("max_alt"), 2)),
        ("Avg temperature (C)", _fmt(summary.get("avg_temp"), 1)),
        ("Max pressure (Pa)", _fmt(summary.get("max_pressure"), 0)),
        ("Packet loss (%)", summary.get("packet_loss_pct")),
        ("Min battery (V)", _fmt(summary.get("min_batt"), 2)),
        ("Avg RSSI (dBm)", _fmt(summary.get("avg_rssi"), 0)),
        ("Anomaly (T>60 C) count", summary.get("temp_over_60_count")),
    ]
    for key, val in rows:
        pdf.cell(90, 7, _pdf_text(f"  {key}"), border=1)
        pdf.cell(90, 7, _pdf_text(f"  {val}"), border=1, ln=True)
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Flight analysis", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, _pdf_text(analysis))
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Latest records", ln=True)
    pdf.set_font("Helvetica", "B", 8)
    cols = [("ts", 32), ("alt", 22), ("temp", 22), ("press", 28), ("batt", 22), ("rssi", 22)]
    for name, w in cols:
        pdf.cell(w, 6, name, border=1, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 7)
    for p in list(packets)[-40:]:
        vals = [
            p.get("ts", ""),
            _fmt(p.get("alt"), 1, missing=""),
            _fmt(p.get("temp"), 1, missing=""),
            _fmt(p.get("pressure"), 0, missing=""),
            _fmt(p.get("batt_v"), 2, missing=""),
            _fmt(p.get("rssi"), 0, missing=""),
        ]
        for (name, w), val in zip(cols, vals):
            pdf.cell(w, 5, _pdf_text(val)[:18], border=1, align="C")
        pdf.ln()

    data = pdf.output(dest="S")
    if isinstance(data, str):
        data = data.encode("latin-1")
    return bytes(data)


def save_pdf(analysis: str, summary: dict, packets: list, phase: str, filename: str | None = None) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = filename or f"telemetry_report_{stamp}.pdf"
    path = os.path.join(REPORTS_DIR, filename)
    with open(path, "wb") as fh:
        fh.write(generate_pdf_bytes(analysis, summary, packets, phase))
    return path

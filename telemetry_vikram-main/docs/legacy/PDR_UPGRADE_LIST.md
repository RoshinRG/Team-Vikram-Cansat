# 🛰️ CAN-7USAT PDR — Upgrade List (Website / Software Only)

> **Project:** CAN-7USAT India 2026 | **Review Stage:** Preliminary Design Review (PDR)
> **Document:** Upgrade requirements for the Ground Station Web Dashboard
> **Last Updated:** 2026-05-12

---

## 🔴 CRITICAL UPGRADES (Must-Have Before PDR)

### 1. Dashboard — Sensor Expansion
| # | Current State | Required Upgrade |
|---|---------------|-----------------|
| 1.1 | Only alt, ax/ay/az, gx/gy/gz displayed | Add **temperature**, **pressure**, **humidity**, **GPS (lat/lon)**, **battery voltage**, **RSSI/SNR** fields to the UI |
| 1.2 | No GPS tracking panel | Add **live GPS map panel** (Leaflet.js / OpenStreetMap) showing CanSat position in real time |
| 1.3 | No voltage/power display | Add **battery voltage gauge** and **power status indicator** |

### 2. Telemetry Packet — Schema Upgrade
| # | Current Field Set | Required Addition |
|---|-------------------|------------------|
| 2.1 | `alt, ax, ay, az, gx, gy, gz, launched, ejected, sim, ts` | Add: `temp`, `pressure`, `humidity`, `lat`, `lon`, `batt_v`, `rssi`, `snr`, `packet_id`, `mission_time` |
| 2.2 | No packet ID or sequence number | Add `packet_id` counter to detect packet loss |
| 2.3 | No mission elapsed time | Add `mission_time` (time since launch in seconds) |

### 3. Database — Schema Migration
| # | Current | Upgrade Needed |
|---|---------|---------------|
| 3.1 | Stores only 12 fields | Extend `telemetry` table to include all new sensor columns |
| 3.2 | No packet loss tracking | Add `packet_id` column and packet-loss calculation logic |
| 3.3 | No mission session concept | Add `session_id` to group flights and support multiple flights |

### 4. Flight Phase Detection — Upgrade
| # | Current | Upgrade Needed |
|---|---------|---------------|
| 4.1 | Basic 7-phase logic (az/velocity based) | Add **pressure-based apogee detection** as cross-check |
| 4.2 | No parachute deployment confirmation | Add `ejected` + descent rate cross-validation |
| 4.3 | No IGNITION → COAST transition logging | Log phase transitions to DB with timestamp |

---

## 🟡 HIGH PRIORITY UPGRADES (Required for Full PDR)

### 5. Real-Time Plots — Enhancements
| # | Current | Upgrade Needed |
|---|---------|---------------|
| 5.1 | Altitude, accel, gyro charts exist | Add **Temperature vs Time** chart |
| 5.2 | No pressure chart | Add **Pressure vs Altitude** dual-axis chart |
| 5.3 | No descent rate plot | Add **Vertical Velocity / Descent Rate** chart with parachute deployment marker |
| 5.4 | No GPS track plot | Add **GPS trajectory plot** (2D ground track + altitude profile) |
| 5.5 | No packet loss indicator on charts | Highlight missing packets in chart with gap/marker |

### 6. Command & Control Panel — New Feature
| # | Current | Upgrade Needed |
|---|---------|---------------|
| 6.1 | No uplink/command capability | Add **uplink command panel** (ARM, DEPLOY, ABORT, SIM-MODE toggle) |
| 6.2 | No TX confirmation | Show command acknowledge status from CanSat |
| 6.3 | Serial port selector is static | Add **dynamic COM port selector dropdown** in the UI |

### 7. Ground Station Architecture — Upgrade
| # | Current | Upgrade Needed |
|---|---------|---------------|
| 7.1 | Single PC, USB-only | Document and support **elevated antenna mount setup** (portable mode) |
| 7.2 | No network relay | Add optional **WebSocket relay** for remote monitoring over LAN |
| 7.3 | No operator log | Add **mission event log panel** (timestamped events: connect, launch, apogee, eject, land) |

### 8. CSV / Data Export — Enhancement
| # | Current | Upgrade Needed |
|---|---------|---------------|
| 8.1 | CSV export works | Add **per-session export** (filter by session/flight) |
| 8.2 | No metadata in CSV | Include mission metadata header row (team name, date, CanSat ID) |
| 8.3 | No auto-export trigger | Auto-export CSV when `ejected = True` (post-flight trigger) |

---

## 🟢 STANDARD UPGRADES (Recommended for Polish)

### 9. AI Analysis — Upgrade
| # | Current | Upgrade Needed |
|---|---------|---------------|
| 9.1 | Gemini 2.0 Flash analysis every 15s | Upgrade prompt to include **temperature, pressure, GPS** data |
| 9.2 | Fallback analysis is generic | Improve fallback to generate phase-specific structured reports |
| 9.3 | No anomaly history | Display **anomaly timeline** — list all past anomalies with timestamps |

### 10. Flight Software State Tracking — New
| # | Current | Upgrade Needed |
|---|---------|---------------|
| 10.1 | No software FSM display | Add **state machine panel** showing FSM state in real time |
| 10.2 | No boot time display | Display **CanSat boot time** (from first `ts` packet) |
| 10.3 | No reset detection | Detect and display **CanSat reset events** (ts counter resets to 0) |

### 11. UI/UX Improvements
| # | Current | Upgrade Needed |
|---|---------|---------------|
| 11.1 | Basic status indicator | Add **mission clock** (elapsed mission time) |
| 11.2 | No audio alerts | Add **browser audio alert** on anomaly detection / parachute deployment |
| 11.3 | No dark/light mode toggle | Add dark/light mode UI toggle |
| 11.4 | 3D rocket rotates by accel data | Improve 3D model to show **parachute deployment animation** on ejection |
| 11.5 | No mobile support | Make dashboard **responsive** for tablet/mobile monitoring |

### 12. Communication Test Plans — Documentation
| # | Current | Upgrade Needed |
|---|---------|---------------|
| 12.1 | No link budget display | Add **link margin calculator panel** (RSSI, distance, SNR display) |
| 12.2 | No range test log | Add **range test logging mode** — log RSSI/SNR vs distance |
| 12.3 | No packet loss metric | Display **packet loss % in real time** on status bar |

---

## 📋 UPGRADE PRIORITY SUMMARY

| Priority | Count | Category |
|----------|-------|----------|
| 🔴 Critical | 11 items | Sensor fields, DB schema, GPS map |
| 🟡 High | 17 items | Commands, plots, ground station |
| 🟢 Standard | 15 items | AI, FSM, UI/UX, comms |
| **Total** | **43 items** | Full upgrade scope |

---

## 🗓️ Suggested Upgrade Milestones

| Milestone | Deadline | Items |
|-----------|----------|-------|
| M1 — Schema & DB | Week 1 | Sec 2, 3 |
| M2 — Sensor UI | Week 2 | Sec 1, 5 |
| M3 — GPS & Maps | Week 3 | Sec 1.2, 5.4 |
| M4 — Commands & Alerts | Week 4 | Sec 6, 11.2 |
| M5 — AI & FSM Display | Week 5 | Sec 9, 10 |
| M6 — Polish & Test | Week 6 | Sec 11, 12 |

---

*Document auto-generated from PDR checklist analysis — CAN-7USAT Ground Station v2.0*

# CanSat Control System (ESP32 TX + Glyph C6 RX, E220 LoRa)

| Folder | Board | Purpose |
|---|---|---|
| `tx_esp32/` | ESP32 DevKit (30-pin, USB-C) | CanSat transmitter: sensors, GPS, SD log, relay, servo, LoRa TX |
| `rx_glyph_c6/` | Glyph C6 | Ground receiver: LoRa RX, WiFi, posts to the Flask dashboard |
| `dashboard/` | PC | Flask backend + offline realtime dashboard |

LoRa settings (both E220): 868.125 MHz, channel 18, 2.4 kbps, UART 9600 8N1, transparent mode (M0 = M1 = GND).

---

## Arduino IDE setup

**TX (ESP32 DevKit)**
- Board: **ESP32 Dev Module**
- Serial Monitor: 115200 baud
- If upload fails / "Connecting....": hold **BOOT**, tap **EN**, release BOOT when "Writing" starts.
- Unplug nothing from GPIO0/2/12/15 (they are not used, so no boot problems).

**RX (Glyph C6)**
- Board: **ESP32C6 Dev Module** (ESP32 Arduino core 3.x)
- Tools -> **USB CDC On Boot: Enabled**
- Serial Monitor: 115200 baud

Libraries (Library Manager):
- DHT sensor library + Adafruit Unified Sensor
- Adafruit BMP085 Library (works for BMP180)
- MPU6050 by Electronic Cats (search "MPU6050"; it also installs I2Cdev)
- Adafruit INA219
- TinyGPSPlus
- ESP32Servo
- ArduinoJson (v7) - TX and RX

---

## TX board wiring (ESP32 DevKit)

The ESP32 DevKit has a **VIN** pin that carries the 5 V from USB (or accepts 5 V from your battery boost). VIN powers everything that needs 5 V.

### Pin map summary

| Board pin | GPIO | Used for |
|---|---|---|
| `D21` | 21 | I2C SDA (BMP180, MPU6050, INA219) |
| `D22` | 22 | I2C SCL (BMP180, MPU6050, INA219) |
| `RX2` | 16 | E220 TXD |
| `TX2` | 17 | E220 RXD |
| `D25` | 25 | GPS TX -> ESP32 RX |
| `D26` | 26 | GPS RX <- ESP32 TX |
| `D18` | 18 | SD SCK |
| `D19` | 19 | SD MI (MISO) |
| `D23` | 23 | SD MO (MOSI) |
| `D5` | 5 | SD CS |
| `D34` | 34 | MQ135 AO (through divider, input-only ADC1 pin) |
| `D4` | 4 | DHT11 data |
| `D13` | 13 | Relay transistor (BC547 base via 1k) |
| `D27` | 27 | Servo signal |
| `3V3` | - | 3.3 V sensors |
| `VIN` | - | 5 V: E220, MQ135, servo, relay coil |
| `GND` | - | common ground for everything |

Not used (leave free): `D0, D2, D12, D15` (boot strapping), `D35, VP, VN` (input only), `D14, D32, D33`.
Pins `RX0/TX0` stay free for USB serial (do not connect anything there).

### I2C modules (all share SDA/SCL, power from 3V3)

| Module pin | ESP32 |
|---|---|
| BMP180 (GY-68) VIN | `3V3` |
| BMP180 GND | `GND` |
| BMP180 SCL / SDA | `D22` / `D21` |
| MPU6050 VCC | `3V3` |
| MPU6050 GND | `GND` |
| MPU6050 SCL / SDA | `D22` / `D21` |
| INA219 Vcc | `3V3` |
| INA219 Gnd | `GND` |
| INA219 Scl / Sda | `D22` / `D21` |

Addresses: BMP180 0x77, MPU6050 0x68, INA219 0x40 (no clash).

INA219 current sensing: Vin+ to battery positive, Vin- to the load / supply input, so the current flows through the board's shunt. GND common.

### E220 LoRa

| E220 | ESP32 |
|---|---|
| VCC | `VIN` (5V) |
| GND | `GND` |
| TXD | `RX2` (GPIO16) |
| RXD | `TX2` (GPIO17) |
| M0 | `GND` |
| M1 | `GND` |
| AUX | not connected |

Same pins as your original working ESP32 test code. Attach the antenna before powering on.

### GPS NEO-6M

| GPS | ESP32 |
|---|---|
| VCC | `3V3` |
| GND | `GND` |
| TX | `D25` |
| RX | `D26` |

### SD card G-MOD

| SD G-MOD | ESP32 |
|---|---|
| 3V3 | `3V3` |
| GND | `GND` |
| SCK | `D18` |
| MO | `D23` |
| MI | `D19` |
| CS | `D5` |
| D1, D2, CD | not connected |

### DHT11

| DHT11 | ESP32 |
|---|---|
| VCC | `3V3` |
| GND | `GND` |
| DATA | `D4` (+ 10k pull-up to 3V3 if the module has none) |

### MQ135 (needs a voltage divider)

| MQ135 | Connection |
|---|---|
| VCC | `VIN` (5V) |
| GND | `GND` |
| AO | 10k -> `D34` (GPIO34), and `D34` -> 15k -> `GND` |

The MQ135 outputs up to 5 V; the ESP32 ADC pin must stay under about 3.1 V. Give the sensor a few minutes to warm up. Code sends the raw ADC value only (no ppm calibration).

### Relay with BC547

```
GPIO13 (D13) --[1k]-- BC547 base
BC547 emitter --------- GND
BC547 collector ------- relay coil pin 1
relay coil pin 2 ------ 5V (VIN pin)
1N4007 across coil: stripe (cathode) to 5V side, other end to collector side
load on relay COM / NO contacts
```

BC547 pinout (flat side facing you, legs down): C - B - E.

### Servo

| Servo | Connection |
|---|---|
| Signal (orange/yellow) | `D27` |
| VCC (red) | 5V (`VIN` pin or a separate 5V supply) |
| GND (brown/black) | `GND` |

Add a 470 uF capacitor across servo 5V/GND. If the board resets when the servo moves, use a separate 5V supply with common GND.

### Power notes
- `3V3` pin: BMP180, MPU6050, INA219, DHT11, GPS, SD.
- `VIN` (5V): E220, MQ135, servo, relay coil.
- On battery, feed 5 V into `VIN` (and `GND`) from a 5 V boost / regulator; do not use VIN and USB 5 V at the same time unless your board has a protection diode.

---

## RX board wiring (Glyph C6)

| E220 | Glyph C6 |
|---|---|
| VCC | `USB` (5V) |
| GND | `GND` |
| TXD | `RX` (GPIO17) |
| RXD | `TX` (GPIO16) |
| M0, M1 | `GND` |

If nothing is received, swap the TXD / RXD wires.

---

## Data flow

```
CanSat (ESP32 TX, JSON over LoRa) -> RX Glyph C6 -> WiFi HTTP POST -> Flask dashboard (browser, realtime)
                                  ^                                     |
                                  +---- polls /api/commands (1 s) <-----+  relay / servo commands
```

## Packet format (TX -> RX, one JSON line every 2 s, ~190 bytes)

```json
{"n":12,"bt":25.3,"p":101325,"a":12.3,"dt":25,"h":45,"mq":512,"ax":0.12,"ay":0.03,"az":9.81,
 "gx":0.0,"gy":0.0,"gz":0.0,"la":12.345678,"lo":77.123456,"ga":123,"s":8,"v":4.95,"i":120,"r":0,"sv":90}
```

| Key | Meaning | Key | Meaning |
|---|---|---|---|
| `n` | packet number | `la`,`lo` | GPS lat/lon |
| `bt` | BMP180 temp C | `ga` | GPS altitude m |
| `p` | pressure Pa | `s` | satellites |
| `a` | altitude m above the launch pad (calibrated) | `v` | bus voltage V |
| `dt`,`h` | DHT11 temp C / humidity % | `i` | current mA |
| `mq` | MQ135 raw ADC | `r` | relay state |
| `ax..az`,`gx..gz` | MPU6050 accel / gyro | `sv` | servo angle |

Keys are short to stay inside the E220's 200-byte sub-packet. The same data is also logged to `/cansat.csv` on the SD card. **A value that is not available is left out of the packet** (and is an empty cell in the CSV), never sent as 0: for example `la`, `lo` and `ga` are only sent while the GPS has a fix less than 5 s old, `ax..gz` only if the MPU6050 answers, and `bt`, `p`, `a` only if the BMP180 works.

Uplink commands are JSON too: `{"r":1}` (relay on), `{"r":0}`, `{"sv":90}` (servo), `{"cal":1}` (zero altitude).

### Altitude calibration (BMP180)
The altitude in the packet is **height above the launch pad**, not above sea level, so weather and the site's elevation do not matter.

- **At boot:** the TX averages 40 pressure readings (outliers trimmed) and stores that as the pad pressure. Power it on at the launch site, on the pad, and wait about 2 s before closing the can.
- **Re-zero any time:** press **ZERO ALT** in the dashboard (sends `{"cal":1}`). Do this on the pad just before launch. The new pad pressure is saved to flash.
- **Reset protection:** the pad pressure is stored in flash. If the board resets in flight, the pressure at boot differs from the saved one by more than 3 hPa (~25 m), so the TX keeps the saved pad reference instead of zeroing in mid-air. Because of this, if you move to a new site more than ~25 m different in height, press ZERO ALT once.
- **Filtering:** BMP180 runs in ultra-high-resolution mode, sampled at 10 Hz, median of the last 5 samples, so spikes are rejected with only ~0.25 s lag.
- **Formula:** `h = 44330 * (1 - (P / P_pad)^0.190295)`.
- The packet's `p` field is still the filtered absolute pressure, and `ga` is the GPS altitude above sea level.

---

## Flask dashboard (`dashboard/`)

Features: 8 realtime charts, 3D attitude model, local GPS track plot, attitude indicator, link/packet-loss status, packet log, relay/servo control, CSV export (also auto-logged to `dashboard/logs/`).

```
cd dashboard
pip install -r requirements.txt
python app.py
```

- Open `http://localhost:5000` on the PC (or `http://<PC-IP>:5000` from a phone on the same WiFi).
- Fully offline: three.js, Chart.js and the fonts are stored in `dashboard/static/`, the 3D CanSat model is generated in code, and the GPS plot is drawn locally (no map tiles, no API keys). No internet is needed anywhere.
- 3D model: drag to rotate, scroll to zoom; it follows the live pitch/roll, deploys a parachute in the DESCENT phase and lights an LED when the relay is on.
- Allow Python through Windows Firewall (private network) so the receiver can reach port 5000.
- The dashboard shows only data received from the transmitter. There is no simulator and no placeholder values: a sensor that is missing or has no reading is left out of the packet and shown as `-`.

### RX firmware setup
1. Install the **ArduinoJson** (v7) library (TX and RX both use it).
2. In `rx_glyph_c6.ino` set `WIFI_SSID`, `WIFI_PASS` and `SERVER` (`http://<PC-LAN-IP>:5000`, get the IP with `ipconfig`). The Glyph C6 uses 2.4 GHz WiFi.
3. Run `dashboard/app.py` first, then power the RX; the dashboard's "Receiver" pill turns green when the RX is polling.

API: `POST /api/telemetry`, `GET /api/commands`, `POST /api/command`, `GET /api/stream` (SSE), `/api/history`, `/api/status`, `/api/export`.

// ===========================================================================
//  base_station.ino  —  E220 LoRa 868 MHz → WiFi → Flask ground station
//  Board : ESP32-C6 (Glyph C6 or any ESP32-C6 dev board)
//  Adapted from ahamedfaisal-dot/cansatcontrollsystem rx_glyph_c6.ino
//
//  SETUP:
//   1. Copy secrets.example.h  ->  secrets.h  and fill in your values.
//   2. secrets.h is in .gitignore — never commit it.
//   3. In Arduino IDE: Tools > Board > esp32 > ESP32C6 Dev Module
//      Tools > USB CDC On Boot > Enabled  (so Serial works over USB-C)
//   4. Wire E220 module: E220-TXD -> GPIO17, E220-RXD -> GPIO16, GND, 3V3
// ===========================================================================

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#if __has_include("secrets.h")
#include "secrets.h"   // #define WIFI_SSID, WIFI_PASSWORD, SERVER_URL
#else
#include "secrets.example.h"
#endif

// ---------------------------------------------------------------------------
// E220 LoRa UART (transparent mode, must match transmitter channel settings)
// E220 TXD → board RX (GPIO17), E220 RXD → board TX (GPIO16)
// ---------------------------------------------------------------------------
#define E220_RX 17
#define E220_TX 16

// How often to poll for uplink commands (milliseconds)
#define POLL_MS 1000

HardwareSerial E220(1);   // UART1 remapped to GPIO16/17

unsigned long lastPoll = 0;
unsigned long rxCount = 0, badCount = 0, postFail = 0;

// ---------------------------------------------------------------------------
// WiFi helpers
// ---------------------------------------------------------------------------
void connectWiFi()
{
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Connecting to WiFi '%s'", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000)
  {
    delay(300);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("\nConnected. IP %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.println("\nWiFi failed (will retry).");
}

bool postJson(const String &path, const String &body)
{
  if (WiFi.status() != WL_CONNECTED) return false;
  HTTPClient http;
  http.setConnectTimeout(1500);
  http.setTimeout(1500);
  http.begin(String(SERVER_URL) + path);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  http.end();
  return code == 200;
}

// ---------------------------------------------------------------------------
// Uplink: ask dashboard for queued commands, send them to the CanSat over LoRa
// ---------------------------------------------------------------------------
void pollCommands()
{
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.setConnectTimeout(1500);
  http.setTimeout(1500);
  http.begin(String(SERVER_URL) + "/api/commands");
  if (http.GET() == 200)
  {
    JsonDocument d;
    if (!deserializeJson(d, http.getString()))
    {
      for (JsonVariant c : d["cmds"].as<JsonArray>())
      {
        const char *s = c.as<const char *>();
        if (!s) continue;
        E220.println(s);       // forward JSON command to CanSat over LoRa
        Serial.printf("Uplink -> %s\n", s);
      }
    }
  }
  http.end();
}

// ---------------------------------------------------------------------------
// Arduino lifecycle
// ---------------------------------------------------------------------------
void setup()
{
  Serial.begin(115200);   // USB-CDC: enable "USB CDC On Boot" in Tools menu
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) delay(10);

  E220.setRxBufferSize(1024);   // buffer LoRa data while HTTP is in progress
  E220.begin(9600, SERIAL_8N1, E220_RX, E220_TX);
  E220.setTimeout(300);

  Serial.println();
  Serial.println("==============================");
  Serial.println("  E220 RECEIVER (Glyph C6)  ");
  Serial.println("  LoRa -> WiFi -> Flask GS  ");
  Serial.println("==============================");
  Serial.println("868 MHz | 2.4 kbps | 9600 8N1 | Transparent mode");

  connectWiFi();
  Serial.printf("Server: %s\n", SERVER_URL);
  Serial.println("Waiting for LoRa packets...");
}

void loop()
{
  connectWiFi();

  // Drain all bytes available from the E220 UART
  while (E220.available() > 0)
  {
    String line = E220.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) continue;

    // Validate: must be a JSON object containing the sequence key "n"
    JsonDocument d;
    DeserializationError err = deserializeJson(d, line);
    if (err || d["n"].isNull())
    {
      badCount++;
      Serial.printf("Bad packet (%s): %s\n", err.c_str(), line.c_str());
      continue;
    }

    rxCount++;
    bool ok = postJson("/api/telemetry", line);
    if (!ok) postFail++;
    Serial.printf("RX #%lu seq=%d %d B -> server %s  (bad:%lu fail:%lu)\n",
                  rxCount, d["n"].as<int>(), (int)line.length(),
                  ok ? "OK" : "FAIL", badCount, postFail);
  }

  // Poll for uplink commands once per POLL_MS
  if (millis() - lastPoll >= POLL_MS)
  {
    lastPoll = millis();
    pollCommands();
  }
}

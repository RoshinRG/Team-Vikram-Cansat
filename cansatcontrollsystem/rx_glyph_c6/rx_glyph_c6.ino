#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ---------------- CONFIGURE THESE ----------------
const char *WIFI_SSID = "Don't smuggle_2G";
const char *WIFI_PASS = "azam1502";
// IP of the PC running dashboard/app.py (same WiFi). Find it with `ipconfig`.
const char *SERVER = "http://192.168.0.203:5000";
// -------------------------------------------------

// E220 TXD -> board RX pin (GPIO17), E220 RXD -> board TX pin (GPIO16)
#define E220_RX 17
#define E220_TX 16

#define POLL_MS 1000

HardwareSerial E220(1);   // UART1 remapped to GPIO16/17 (USB-CDC Serial stays on USB-C)

unsigned long lastPoll = 0;
unsigned long rxCount = 0, badCount = 0, postFail = 0;

void connectWiFi()
{
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Connecting to WiFi '%s'", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
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
  http.begin(String(SERVER) + path);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  http.end();
  return code == 200;
}

// Ask the dashboard for queued uplink commands and forward them over LoRa.
void pollCommands()
{
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.setConnectTimeout(1500);
  http.setTimeout(1500);
  http.begin(String(SERVER) + "/api/commands");
  if (http.GET() == 200)
  {
    JsonDocument d;
    if (!deserializeJson(d, http.getString()))
    {
      for (JsonVariant c : d["cmds"].as<JsonArray>())
      {
        const char *s = c.as<const char *>();
        if (!s) continue;
        E220.println(s);
        Serial.printf("Uplink -> %s\n", s);
      }
    }
  }
  http.end();
}

void setup()
{
  Serial.begin(115200);   // USB-CDC (enable "USB CDC On Boot" in Tools)
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) delay(10);

  E220.setRxBufferSize(1024);   // keep LoRa data while an HTTP request is in progress
  E220.begin(9600, SERIAL_8N1, E220_RX, E220_TX);
  E220.setTimeout(300);

  Serial.println();
  Serial.println("==============================");
  Serial.println("  E220 RECEIVER (Glyph C6)");
  Serial.println("  LoRa -> WiFi -> Flask");
  Serial.println("==============================");
  Serial.println("868.125 MHz* | Ch 18* | 2.4 kbps* | 9600 8N1 | Transparent*");

  connectWiFi();
  Serial.printf("Server: %s\n", SERVER);
  Serial.println("Waiting for messages...");
}

void loop()
{
  connectWiFi();

  while (E220.available() > 0)
  {
    String line = E220.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) continue;

    // validate before forwarding
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
    Serial.printf("RX #%lu seq=%d %d bytes -> server %s  (bad:%lu fail:%lu)\n",
                  rxCount, d["n"].as<int>(), (int)line.length(), ok ? "OK" : "FAIL", badCount, postFail);
  }

  if (millis() - lastPoll >= POLL_MS)
  {
    lastPoll = millis();
    pollCommands();
  }
}

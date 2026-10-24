#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <DHT.h>
#include <Adafruit_BMP085.h>   // works for BMP180
#include <MPU6050.h>          // "MPU6050" by Electronic Cats
#include <Adafruit_INA219.h>
#include <TinyGPSPlus.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>
#include <Preferences.h>

// ---------------- Pins (ESP32 DevKit, 30-pin) ----------------
#define LORA_RX    16   // E220 TXD -> RX2 (GPIO16)
#define LORA_TX    17   // E220 RXD -> TX2 (GPIO17)
#define GPS_RX     25   // GPS TX  -> D25
#define GPS_TX     26   // GPS RX  -> D26
#define I2C_SDA    21   // BMP180, MPU6050, INA219
#define I2C_SCL    22
#define SD_SCK     18
#define SD_MISO    19
#define SD_MOSI    23
#define SD_CS       5
#define MQ_PIN     34   // VN/ D34, ADC1 input-only (via voltage divider!)
#define DHT_PIN     14   // D4
#define RELAY_PIN  13   // D13 -> 1k -> BC547 base
#define SERVO_PIN  27   // D27

#define SEND_MS      2000

DHT dht(DHT_PIN, DHT11);
Adafruit_BMP085 bmp;
MPU6050 *mpu = nullptr;   // created once we know the I2C address (0x68 or 0x69)
uint8_t mpuAddr = 0;
Adafruit_INA219 ina;
TinyGPSPlus gps;
Servo servo;

bool okBmp, okMpu, okIna, okSd;
int relayState = 0;
int servoAngle = 90;
unsigned long seq = 0, lastSend = 0;
String cmdBuf;

// ---------------- Barometric altitude (calibrated, relative to launch pad) ----------------
// Altitude = height above the pressure measured at the pad (basePa), NOT above sea level, so
// weather and site altitude do not matter. basePa is averaged at boot, re-zeroed by the
// {"cal":1} uplink command (dashboard "ZERO ALT"), and saved to flash so a mid-flight reset
// does not lose the pad reference.
#define BARO_SAMPLE_MS   100     // sample rate for the filter
#define BARO_MEDIAN_N    5       // median of the last N samples rejects spikes
#define RESET_KEEP_PA    300.0f  // boot pressure differs from saved by >3 hPa (~25 m) -> assume in-flight reset

Preferences prefs;
float basePa = 101325.0f;
float pBuf[BARO_MEDIAN_N];
int pIdx = 0, pCount = 0;
float pFilt = 0;
unsigned long lastBaroMs = 0;

float medianOf(const float *a, int n)
{
  float t[BARO_MEDIAN_N];
  memcpy(t, a, n * sizeof(float));
  for (int i = 1; i < n; i++)                       // insertion sort
  {
    float v = t[i]; int j = i - 1;
    while (j >= 0 && t[j] > v) { t[j + 1] = t[j]; j--; }
    t[j + 1] = v;
  }
  return t[n / 2];
}

void sampleBaro()
{
  if (!okBmp || millis() - lastBaroMs < BARO_SAMPLE_MS) return;
  lastBaroMs = millis();
  float p = bmp.readPressure();
  if (p < 30000 || p > 110000) return;              // reject garbage reads
  pBuf[pIdx] = p; pIdx = (pIdx + 1) % BARO_MEDIAN_N;
  if (pCount < BARO_MEDIAN_N) pCount++;
  pFilt = medianOf(pBuf, pCount);
}

// average of n readings with the highest and lowest quarter discarded
float averagePressure(int n)
{
  float v[60];
  if (n > 60) n = 60;
  int c = 0;
  for (int i = 0; i < n; i++)
  {
    float p = bmp.readPressure();
    if (p > 30000 && p < 110000) v[c++] = p;
    delay(20);
  }
  if (c < 5) return 0;
  for (int i = 1; i < c; i++)
  {
    float x = v[i]; int j = i - 1;
    while (j >= 0 && v[j] > x) { v[j + 1] = v[j]; j--; }
    v[j + 1] = x;
  }
  int lo = c / 4, hi = c - c / 4;
  float sum = 0;
  for (int i = lo; i < hi; i++) sum += v[i];
  return sum / (hi - lo);
}

bool calibrateBaro()                                // zero altitude at the current pressure
{
  if (!okBmp) return false;
  float p = averagePressure(40);
  if (p <= 0) return false;
  basePa = p;
  prefs.putFloat("p0", basePa);
  pCount = 0;                                       // restart the filter
  Serial.printf("Altitude zeroed: pad pressure %.2f hPa\n", basePa / 100.0f);
  return true;
}

void initBaro()
{
  prefs.begin("cansat", false);
  float saved = prefs.getFloat("p0", 0);
  float fresh = averagePressure(40);
  if (fresh <= 0) return;
  if (saved > 30000 && fabsf(fresh - saved) > RESET_KEEP_PA)
  {
    basePa = saved;                                 // looks like a reset in flight: keep the old pad reference
    Serial.printf("Big pressure change since last zero (%.1f hPa): keeping saved pad reference %.2f hPa\n",
                  (fresh - saved) / 100.0f, saved / 100.0f);
  }
  else
  {
    basePa = fresh;
    prefs.putFloat("p0", basePa);
    Serial.printf("Altitude zeroed at boot: pad pressure %.2f hPa\n", basePa / 100.0f);
  }
}

float baroAltitude()                                // metres above the pad
{
  if (pCount == 0) return 0;
  return 44330.0f * (1.0f - powf(pFilt / basePa, 0.190295f));
}

// Uplink commands are JSON lines: {"r":1} relay on/off, {"sv":90} servo angle, {"cal":1} zero altitude
void handleCommand(const String &c)
{
  JsonDocument d;
  if (deserializeJson(d, c)) return;

  if (!d["r"].isNull())
  {
    relayState = d["r"].as<int>() ? 1 : 0;
    digitalWrite(RELAY_PIN, relayState ? HIGH : LOW);
  }
  if (!d["sv"].isNull())
  {
    servoAngle = constrain(d["sv"].as<int>(), 0, 180);
    servo.write(servoAngle);
  }
  if (!d["cal"].isNull())
    calibrateBaro();
}

bool i2cPresent(uint8_t a)
{
  Wire.beginTransmission(a);
  return Wire.endTransmission() == 0;
}

uint8_t i2cRead(uint8_t addr, uint8_t reg)
{
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom(addr, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0xFF;
}

void i2cScan()
{
  Serial.print("I2C scan:");
  int n = 0;
  for (uint8_t a = 1; a < 127; a++)
    if (i2cPresent(a)) { Serial.printf(" 0x%02X", a); n++; }
  Serial.println(n ? "" : " nothing found - check SDA/SCL wiring and 3V3/GND");
}

void initMpu()
{
  for (uint8_t a : {0x68, 0x69})
    if (i2cPresent(a)) { mpuAddr = a; break; }
  if (!mpuAddr)
  {
    Serial.println("MPU6050: no response at 0x68/0x69 (check VCC=3V3, GND, SDA=D21, SCL=D22)");
    return;
  }
  mpu = new MPU6050(mpuAddr);
  mpu->initialize();
  uint8_t who = i2cRead(mpuAddr, 0x75);   // WHO_AM_I: 0x68 MPU6050, 0x70/0x71/0x72 clones/MPU6500
  Serial.printf("MPU6050 found at 0x%02X, WHO_AM_I=0x%02X\n", mpuAddr, who);
  okMpu = true;
}

void setup()
{
  Serial.begin(115200);                                   // USB serial
  Serial2.begin(9600, SERIAL_8N1, LORA_RX, LORA_TX);      // E220
  Serial1.begin(9600, SERIAL_8N1, GPS_RX, GPS_TX);        // NEO-6M

  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);

  servo.attach(SERVO_PIN);
  servo.write(servoAngle);

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);   // 100 kHz: more reliable with jumper wires
  i2cScan();
  dht.begin();
  okBmp = bmp.begin(BMP085_ULTRAHIGHRES);
  if (okBmp) initBaro();
  initMpu();
  okIna = ina.begin();

  SPI.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  okSd = SD.begin(SD_CS);
  if (okSd)
  {
    File f = SD.open("/cansat.csv", FILE_APPEND);
    if (f)
    {
      if (f.size() == 0)
        f.println("seq,ms,bmpT,press,alt,dhtT,dhtH,mq,ax,ay,az,gx,gy,gz,lat,lon,gpsAlt,sats,busV,mA,relay,servo");
      f.close();
    }
  }

  delay(1000);
  Serial.println();
  Serial.println("==============================");
  Serial.println("  E220 TRANSMITTER (ESP32)");
  Serial.println("==============================");
  Serial.printf("BMP180:%d MPU6050:%d INA219:%d SD:%d\n", okBmp, okMpu, okIna, okSd);
  Serial.println("868.125 MHz | Ch 18 | 2.4 kbps | 9600 8N1 | Transparent");
}

void loop()
{
  // feed GPS
  while (Serial1.available()) gps.encode(Serial1.read());
  sampleBaro();

  // commands from ground: R1, R0, S<0-180>
  while (Serial2.available())
  {
    char ch = Serial2.read();
    if (ch == '\n') { handleCommand(cmdBuf); cmdBuf = ""; }
    else if (cmdBuf.length() < 64) cmdBuf += ch;
  }

  if (millis() - lastSend < SEND_MS) return;
  lastSend = millis();

  // ---- read sensors; anything that is not available is simply left out of the packet ----
  bool bmpOk = okBmp && pCount > 0;
  float bmpT = 0, press = 0, alt = 0;
  if (bmpOk)
  {
    bmpT = bmp.readTemperature();
    press = pFilt;               // filtered absolute pressure (Pa)
    alt = baroAltitude();        // metres above the pad
  }

  float dhtT = dht.readTemperature();
  float dhtH = dht.readHumidity();
  bool dhtOk = !isnan(dhtT) && !isnan(dhtH);

  int mq = analogRead(MQ_PIN);

  float ax = 0, ay = 0, az = 0, gx = 0, gy = 0, gz = 0;
  if (okMpu)
  {
    int16_t rax, ray, raz, rgx, rgy, rgz;
    mpu->getMotion6(&rax, &ray, &raz, &rgx, &rgy, &rgz);
    // defaults: +-2 g (16384 LSB/g) and +-250 deg/s (131 LSB/deg/s) -> m/s^2 and rad/s
    ax = rax * 9.80665f / 16384.0f; ay = ray * 9.80665f / 16384.0f; az = raz * 9.80665f / 16384.0f;
    gx = rgx * DEG_TO_RAD / 131.0f; gy = rgy * DEG_TO_RAD / 131.0f; gz = rgz * DEG_TO_RAD / 131.0f;
  }

  float busV = 0, mA = 0;
  if (okIna)
  {
    busV = ina.getBusVoltage_V();
    mA = ina.getCurrent_mA();
  }

  bool fixOk = gps.location.isValid() && gps.location.age() < 5000;
  bool altOk = gps.altitude.isValid() && gps.altitude.age() < 5000;
  bool satOk = gps.satellites.isValid();

  // ---- compact JSON (short keys) for LoRa + the same fields as CSV for the SD card ----
  char json[260], csv[220];
  size_t jn = 0, cn = 0;
  jn += snprintf(json, sizeof(json), "{\"n\":%lu", seq);
  cn += snprintf(csv, sizeof(csv), "%lu,%lu", seq, millis());
  auto add = [&](const char *key, bool valid, double v, int dec)
  {
    if (jn > sizeof(json) - 24 || cn > sizeof(csv) - 20) return;
    if (valid)
    {
      jn += snprintf(json + jn, sizeof(json) - jn, ",\"%s\":%.*f", key, dec, v);
      cn += snprintf(csv + cn, sizeof(csv) - cn, ",%.*f", dec, v);
    }
    else
      cn += snprintf(csv + cn, sizeof(csv) - cn, ",");     // empty CSV cell, key omitted from JSON
  };
  add("bt", bmpOk,  bmpT,   1);
  add("p",  bmpOk,  press,  0);
  add("a",  bmpOk,  alt,    1);
  add("dt", dhtOk,  dhtT,   0);
  add("h",  dhtOk,  dhtH,   0);
  add("mq", true,   mq,     0);
  add("ax", okMpu,  ax,     2);
  add("ay", okMpu,  ay,     2);
  add("az", okMpu,  az,     2);
  add("gx", okMpu,  gx,     2);
  add("gy", okMpu,  gy,     2);
  add("gz", okMpu,  gz,     2);
  add("la", fixOk,  fixOk ? gps.location.lat() : 0, 6);
  add("lo", fixOk,  fixOk ? gps.location.lng() : 0, 6);
  add("ga", altOk,  altOk ? gps.altitude.meters() : 0, 0);
  add("s",  satOk,  satOk ? gps.satellites.value() : 0, 0);
  add("v",  okIna,  busV,   2);
  add("i",  okIna,  mA,     0);
  add("r",  true,   relayState, 0);
  add("sv", true,   servoAngle, 0);
  snprintf(json + jn, sizeof(json) - jn, "}");

  Serial2.println(json);   // LoRa
  Serial.print("Sent (");
  Serial.print(strlen(json));
  Serial.print(" B): ");
  Serial.println(json);

  if (okSd)
  {
    File f = SD.open("/cansat.csv", FILE_APPEND);
    if (f) { f.println(csv); f.close(); }
  }
  seq++;
}

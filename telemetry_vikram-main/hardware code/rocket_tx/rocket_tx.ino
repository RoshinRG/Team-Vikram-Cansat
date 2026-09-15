#include <Wire.h>
#include <Adafruit_BMP085.h>
#include <MPU6050.h>
#include <SPI.h>
#include <LoRa.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>

// ================= LoRa Pins =================
#define LORA_SCK   18
#define LORA_MISO  19
#define LORA_MOSI  23
#define LORA_CS    5
#define LORA_RST   14
#define LORA_IRQ   26

// ================= Servo =================
#define SERVO_PIN 25
#define SERVO_LOCK_ANGLE 0
#define SERVO_EJECT_ANGLE 90

// ================= Flight Logic =================
#define LAUNCH_ACCEL_THRESHOLD 3.0   // g
#define MIN_EJECTION_TIME 6000       // ms after launch
#define SEND_INTERVAL 300            // ms (LoRa safe)

// ================= Objects =================
Adafruit_BMP085 bmp;
MPU6050 mpu;
Servo ejectServo;

// ================= Flags =================
bool bmpAvailable = false;
bool mpuAvailable = false;
bool launched = false;
bool ejected = false;

// ================= Timing =================
unsigned long lastSend = 0;
unsigned long flightStartTime = 0;
unsigned long launchTime = 0;

// ================= Calibration =================
float baseAltitude = 0;
float accOffsetX = 0, accOffsetY = 0, accOffsetZ = 0;
float gyroOffsetX = 0, gyroOffsetY = 0, gyroOffsetZ = 0;

// ================= Apogee =================
float lastAltitude = 0;
int apogeeCounter = 0;

// ================= Simulation =================
float getSimulatedAltitude(unsigned long t_ms) {
  float t = t_ms / 1000.0;
  if (t < 5) return 40 * t * t;
  else if (t < 10) return 1000 - 20 * (t - 5) * (t - 5);
  else return max(1000 - 50 * (t - 10), 0.0f);
}

// ================= MPU Calibration =================
void calibrateMPU() {
  Serial.println("🧭 Calibrating MPU6050 (keep still)");
  delay(2000);

  long ax = 0, ay = 0, az = 0;
  long gx = 0, gy = 0, gz = 0;

  for (int i = 0; i < 500; i++) {
    int16_t rax, ray, raz, rgx, rgy, rgz;
    mpu.getMotion6(&rax, &ray, &raz, &rgx, &rgy, &rgz);

    ax += rax; ay += ray; az += raz;
    gx += rgx; gy += rgy; gz += rgz;
    delay(5);
  }

  accOffsetX = ax / 500.0;
  accOffsetY = ay / 500.0;
  accOffsetZ = (az / 500.0) - 16384.0;  // gravity
  gyroOffsetX = gx / 500.0;
  gyroOffsetY = gy / 500.0;
  gyroOffsetZ = gz / 500.0;

  Serial.println("✅ MPU6050 calibrated");
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  // ================= I2C =================
  Wire.begin(21, 22);
  Wire.setClock(400000);

  // ================= BMP180 =================
  if (bmp.begin()) {
    bmpAvailable = true;
    baseAltitude = bmp.readAltitude();
    Serial.println("✅ BMP180 detected");
  } else {
    Serial.println("⚠️ BMP180 NOT found → SIMULATION MODE");
  }

  // ================= MPU6050 =================
  mpu.initialize();
  delay(100);

  int16_t tx, ty, tz;
  mpu.getAcceleration(&tx, &ty, &tz);

  if (tx != 0 || ty != 0 || tz != 0) {
    mpuAvailable = true;
    calibrateMPU();
  } else {
    Serial.println("❌ MPU6050 invalid");
  }

  // ================= Servo =================
  ejectServo.attach(SERVO_PIN);
  ejectServo.write(SERVO_LOCK_ANGLE);

  // ================= LoRa =================
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_CS);
  LoRa.setPins(LORA_CS, LORA_RST, LORA_IRQ);

  if (!LoRa.begin(433E6)) {
    Serial.println("❌ LoRa failed");
    while (1);
  }

  LoRa.setSpreadingFactor(7);
  LoRa.setSignalBandwidth(125E3);
  LoRa.setCodingRate4(5);
  LoRa.enableCrc();

  flightStartTime = millis();
  Serial.println("🚀 ROCKET READY");
}

void loop() {
  if (millis() - lastSend >= SEND_INTERVAL) {
    lastSend = millis();

    // ================= ALTITUDE =================
    float altitude, temperature, pressure;

    if (bmpAvailable) {
      temperature = bmp.readTemperature();
      pressure = bmp.readPressure();
      altitude = bmp.readAltitude() - baseAltitude;
    } else {
      temperature = 28.0;
      pressure = 101325;
      altitude = getSimulatedAltitude(millis() - flightStartTime);
    }

    // ================= MPU6050 =================
    float ax = 0, ay = 0, az = 0, gx = 0, gy = 0, gz = 0;

    if (mpuAvailable) {
      int16_t rax, ray, raz, rgx, rgy, rgz;
      mpu.getMotion6(&rax, &ray, &raz, &rgx, &rgy, &rgz);

      ax = (rax - accOffsetX) / 16384.0;
      ay = (ray - accOffsetY) / 16384.0;
      az = (raz - accOffsetZ) / 16384.0;
      gx = (rgx - gyroOffsetX) / 131.0;
      gy = (rgy - gyroOffsetY) / 131.0;
      gz = (rgz - gyroOffsetZ) / 131.0;
    }

    // ================= LAUNCH DETECTION =================
    float netAccel = sqrt(ax * ax + ay * ay + az * az);

    if (!launched && netAccel > LAUNCH_ACCEL_THRESHOLD) {
      launched = true;
      launchTime = millis();
      Serial.println("🚀 LAUNCH DETECTED");
    }

    // ================= APOGEE + EJECTION =================
    if (launched && !ejected) {
      if (altitude < lastAltitude) apogeeCounter++;
      else apogeeCounter = 0;

      if (apogeeCounter >= 3 &&
          millis() - launchTime > MIN_EJECTION_TIME) {

        Serial.println("🪂 APOGEE → EJECT");
        ejectServo.write(SERVO_EJECT_ANGLE);
        ejected = true;
      }
    }

    lastAltitude = altitude;

    // ================= JSON =================
    StaticJsonDocument<256> doc;
    doc["alt"] = altitude;
    doc["ax"] = ax;
    doc["ay"] = ay;
    doc["az"] = az;
    doc["gx"] = gx;
    doc["gy"] = gy;
    doc["gz"] = gz;
    doc["launched"] = launched;
    doc["ejected"] = ejected;
    doc["sim"] = !bmpAvailable;
    doc["ts"] = millis();

    String json;
    serializeJson(doc, json);

    // ================= LoRa SEND =================
    LoRa.beginPacket();
    LoRa.print("<");
    LoRa.print(json);
    LoRa.print(">");
    LoRa.endPacket();

    Serial.println(json);
  }
}

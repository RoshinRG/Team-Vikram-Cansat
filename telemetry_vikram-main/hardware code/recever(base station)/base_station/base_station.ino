#include <SPI.h>
#include <LoRa.h>

#define LORA_SCK   18
#define LORA_MISO  19
#define LORA_MOSI  23
#define LORA_CS    5
#define LORA_RST   14
#define LORA_IRQ   26

void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(LORA_CS, OUTPUT);
  digitalWrite(LORA_CS, HIGH);

  pinMode(LORA_RST, OUTPUT);
  digitalWrite(LORA_RST, LOW);
  delay(10);
  digitalWrite(LORA_RST, HIGH);
  delay(10);

  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_CS);
  LoRa.setPins(LORA_CS, LORA_RST, LORA_IRQ);

  if (!LoRa.begin(433E6)) {
    Serial.println("❌ LoRa init failed");
    while (1);
  }

  LoRa.setSpreadingFactor(7);
  LoRa.setSignalBandwidth(125E3);
  LoRa.setCodingRate4(5);
  LoRa.enableCrc();

  Serial.println("📡 Ground Station READY");
}


void loop() {
  int packetSize = LoRa.parsePacket();
  if (!packetSize) return;

  String raw = "";
  while (LoRa.available()) {
    raw += (char)LoRa.read();
  }

  // ================= FRAME CHECK =================
  if (!raw.startsWith("<") || !raw.endsWith(">")) {
    // corrupted / partial packet
    return;
  }

  // Remove < >
  raw.remove(0, 1);
  raw.remove(raw.length() - 1);

  // ================= PRINT CLEAN JSON =================
  Serial.println(raw);

  // ================= OPTIONAL: LINK QUALITY =================
  Serial.print("RSSI: ");
  Serial.print(LoRa.packetRssi());
  Serial.print(" dBm | SNR: ");
  Serial.print(LoRa.packetSnr());
  Serial.println(" dB");
}

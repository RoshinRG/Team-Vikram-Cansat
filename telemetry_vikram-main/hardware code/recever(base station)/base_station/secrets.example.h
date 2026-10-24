// =========================================================================
//  secrets.example.h  — COPY this file to secrets.h and fill in your values.
//  secrets.h is listed in .gitignore and is NEVER committed to the repo.
// =========================================================================

#pragma once

// Your 2.4 GHz WiFi network (the same one the ground-station PC is on)
#define WIFI_SSID     "YOUR_WIFI_SSID"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#define WIFI_PASS     "YOUR_WIFI_PASSWORD"

// LAN IP of the PC running app.py.
// Find it with: ipconfig   (Windows)  or  ip addr  (Linux/macOS)
// It usually looks like 192.168.x.y and MUST be on the same subnet as the ESP32.
#define SERVER_URL    "http://<PC-LAN-IP>:5000"
#define SERVER        "http://<PC-LAN-IP>:5000"

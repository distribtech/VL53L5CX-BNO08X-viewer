/*
 * VL53L5CX ToF Sensor + BNO08X IMU Reader for ESP32
 *
 * Reads 8x8 distance data from VL53L5CX and orientation from BNO08X,
 * outputs JSON over serial.
 *
 * Wiring (both sensors share I2C bus):
 *   VIN -> 3V3
 *   GND -> GND
 *   SDA -> GPIO 21
 *   SCL -> GPIO 22
 *   LPn -> GPIO 19 (VL53L5CX enable, set HIGH)
 */

#include <Wire.h>
#include <WiFi.h>
#include <WebServer.h>
#include <SparkFun_VL53L5CX_Library.h>
#include <SparkFun_BNO08x_Arduino_Library.h>

// Version - must match viewer config.VERSION
#define VERSION "0.1.0"

// Pin definitions
#define SDA_PIN 21
#define SCL_PIN 22
#define LPN_PIN 19

// VL53L5CX ToF sensor instance
SparkFun_VL53L5CX sensor;
VL53L5CX_ResultsData measurementData;

// BNO08X IMU instance
BNO08x imu;
bool imuAvailable = false;

// Current quaternion (wxyz format)
float quatW = 1.0, quatX = 0.0, quatY = 0.0, quatZ = 0.0;

// I2C speed - use 1MHz for fast data transfer
#define I2C_SPEED 1000000

// Wi-Fi access point (default transport)
const char* WIFI_AP_SSID = "VL53L5CX-Viewer";
const char* WIFI_AP_PASSWORD = "viewer123";
const uint16_t WIFI_WEB_PORT = 80;
const uint16_t WIFI_STREAM_PORT = 8765;
WiFiServer dataServer(WIFI_STREAM_PORT);
WiFiClient dataClient;
WebServer webServer(WIFI_WEB_PORT);

String buildLandingPage() {
  String html;
  html.reserve(2200);
  html += F("<!doctype html><html><head><meta charset='utf-8'>");
  html += F("<meta name='viewport' content='width=device-width,initial-scale=1'>");
  html += F("<title>VL53L5CX Viewer</title>");
  html += F("<style>");
  html += F("body{font-family:system-ui,-apple-system,sans-serif;background:#111827;color:#f9fafb;margin:0;padding:24px;}");
  html += F(".card{max-width:820px;margin:0 auto;background:#1f2937;border:1px solid #374151;border-radius:14px;padding:20px;}");
  html += F("h1{margin-top:0;font-size:1.4rem;}p{line-height:1.5;color:#d1d5db;}code{background:#111827;padding:2px 6px;border-radius:6px;}");
  html += F(".btn{display:inline-block;text-decoration:none;color:#fff;background:#2563eb;padding:10px 16px;border-radius:10px;margin:6px 8px 6px 0;font-weight:600;}");
  html += F(".btn.secondary{background:#374151;}ul{padding-left:18px;color:#d1d5db;}");
  html += F("</style></head><body><div class='card'>");
  html += F("<h1>VL53L5CX + BNO08X Viewer</h1>");
  html += F("<p><strong>Default mode:</strong> web server hosted on the ESP32.</p>");
  html += F("<p>Choose how you want to view data:</p>");
  html += F("<a class='btn' href='/esp32'>Use ESP32 web interface (default)</a>");
  html += F("<a class='btn secondary' href='/python'>Use Python <code>-m viewer</code></a>");
  html += F("<h2>Connection details</h2><ul>");
  html += F("<li>ESP32 web UI: <code>http://192.168.4.1/</code></li>");
  html += F("<li>TCP sensor stream: <code>192.168.4.1:");
  html += String(WIFI_STREAM_PORT);
  html += F("</code></li>");
  html += F("<li>Wi-Fi AP: <code>");
  html += WIFI_AP_SSID;
  html += F("</code></li></ul></div></body></html>");
  return html;
}

String buildEsp32ModePage() {
  String html;
  html.reserve(1500);
  html += F("<!doctype html><html><head><meta charset='utf-8'>");
  html += F("<meta name='viewport' content='width=device-width,initial-scale=1'>");
  html += F("<title>ESP32 Web Interface</title></head><body style='font-family:system-ui;background:#111827;color:#f9fafb;padding:24px'>");
  html += F("<h1>ESP32 web interface active</h1>");
  html += F("<p>This page is served directly by the ESP32 (default behavior).</p>");
  html += F("<p>Sensor stream endpoint for integrations:</p>");
  html += F("<p><code>192.168.4.1:");
  html += String(WIFI_STREAM_PORT);
  html += F("</code></p>");
  html += F("<p><a href='/' style='color:#60a5fa'>← Back to mode selection</a></p></body></html>");
  return html;
}

String buildPythonModePage() {
  String html;
  html.reserve(1700);
  html += F("<!doctype html><html><head><meta charset='utf-8'>");
  html += F("<meta name='viewport' content='width=device-width,initial-scale=1'>");
  html += F("<title>Python Viewer Mode</title></head><body style='font-family:system-ui;background:#111827;color:#f9fafb;padding:24px'>");
  html += F("<h1>Python viewer mode</h1>");
  html += F("<p>To use the desktop 3D interface, run on your computer:</p>");
  html += F("<pre style='background:#1f2937;border:1px solid #374151;border-radius:8px;padding:12px;overflow:auto'>python -m viewer</pre>");
  html += F("<p>Then open <code>http://localhost:8080</code> on that computer.</p>");
  html += F("<p><a href='/' style='color:#60a5fa'>← Back to mode selection</a></p></body></html>");
  return html;
}

void setupWebServer() {
  webServer.on("/", HTTP_GET, []() {
    webServer.send(200, "text/html", buildLandingPage());
  });
  webServer.on("/esp32", HTTP_GET, []() {
    webServer.send(200, "text/html", buildEsp32ModePage());
  });
  webServer.on("/python", HTTP_GET, []() {
    webServer.send(200, "text/html", buildPythonModePage());
  });
  webServer.on("/status", HTTP_GET, []() {
    String payload = String("{\"ssid\":\"") + WIFI_AP_SSID + "\",\"stream_port\":" + WIFI_STREAM_PORT
      + ",\"imu\":" + (imuAvailable ? "true" : "false") + "}";
    webServer.send(200, "application/json", payload);
  });
  webServer.begin();
}

void sendJsonLine(const String& payload) {
  // Keep serial output for development workflows.
  Serial.println(payload);

  // Stream to Wi-Fi client when connected.
  if (dataClient && dataClient.connected()) {
    dataClient.println(payload);
  }
}

void serviceWifiClient() {
  if (dataClient && !dataClient.connected()) {
    dataClient.stop();
  }
  if (!dataClient || !dataClient.connected()) {
    WiFiClient candidate = dataServer.available();
    if (candidate) {
      dataClient = candidate;
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  sendJsonLine("{\"status\":\"initializing\"}");

  // Start AP for default wireless mode.
  WiFi.mode(WIFI_AP);
  WiFi.softAP(WIFI_AP_SSID, WIFI_AP_PASSWORD);
  dataServer.begin();
  setupWebServer();
  String wifiReady = String("{\"status\":\"wifi_ap_ready\",\"ssid\":\"")
    + WIFI_AP_SSID
    + "\",\"ip\":\""
    + WiFi.softAPIP().toString()
    + "\",\"port\":"
    + WIFI_STREAM_PORT
    + ",\"web\":\"http://"
    + WiFi.softAPIP().toString()
    + "\""
    + "}";
  sendJsonLine(wifiReady);

  // Enable I2C on sensor by setting LPn HIGH
  pinMode(LPN_PIN, OUTPUT);
  digitalWrite(LPN_PIN, HIGH);
  delay(10);

  // Initialize I2C with specified pins and speed
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(I2C_SPEED);

  sendJsonLine("{\"status\":\"i2c_ready\"}");

  // Initialize sensor
  if (!sensor.begin()) {
    sendJsonLine("{\"error\":\"sensor_init_failed\"}");
    while (1) {
      delay(1000);
    }
  }

  sendJsonLine("{\"status\":\"sensor_found\"}");

  // Configure sensor for 8x8 resolution
  sensor.setResolution(64);  // 64 zones = 8x8

  // Set ranging frequency to 15Hz (stable for continuous streaming)
  sensor.setRangingFrequency(15);

  // Start ranging
  sensor.startRanging();

  sendJsonLine("{\"status\":\"ranging_started\",\"resolution\":\"8x8\",\"frequency_hz\":15}");

  // Initialize BNO08X IMU (shares I2C bus with VL53L5CX)
  // Try default address 0x4A first, then alternate 0x4B
  if (imu.begin(0x4A, Wire)) {
    imuAvailable = true;
  } else if (imu.begin(0x4B, Wire)) {
    imuAvailable = true;
  }

  if (imuAvailable) {
    // Enable game rotation vector at 10ms interval (100Hz)
    // Game rotation uses accel+gyro only (no magnetometer) - immune to magnetic interference
    imu.enableGameRotationVector(10);
    sendJsonLine("{\"status\":\"imu_ready\",\"mode\":\"game_rotation_vector\",\"frequency_hz\":100}");
  } else {
    sendJsonLine("{\"status\":\"imu_not_found\"}");
  }
}

void loop() {
  webServer.handleClient();
  serviceWifiClient();

  // Poll IMU for new orientation data (non-blocking)
  if (imuAvailable && imu.wasReset()) {
    // Re-enable game rotation vector if IMU was reset
    imu.enableGameRotationVector(10);
  }

  if (imuAvailable && imu.getSensorEvent()) {
    if (imu.getSensorEventID() == SENSOR_REPORTID_GAME_ROTATION_VECTOR) {
      quatW = imu.getQuatReal();
      quatX = imu.getQuatI();
      quatY = imu.getQuatJ();
      quatZ = imu.getQuatK();
    }
  }

  // Check if new ToF data is available
  if (sensor.isDataReady()) {
    if (sensor.getRangingData(&measurementData)) {
      // Output JSON with distance and quaternion data
      String payload = "{\"distances\":[";

      for (int i = 0; i < 64; i++) {
        // Distance in mm
        payload += measurementData.distance_mm[i];
        if (i < 63) payload += ",";
      }

      payload += "],\"status\":[";

      for (int i = 0; i < 64; i++) {
        // Target status (5 = valid, others = various error states)
        payload += measurementData.target_status[i];
        if (i < 63) payload += ",";
      }

      // Add quaternion (wxyz format) with 6 decimal places for accuracy
      payload += "],\"quat\":[";
      payload += String(quatW, 6) + ",";
      payload += String(quatX, 6) + ",";
      payload += String(quatY, 6) + ",";
      payload += String(quatZ, 6);
      payload += "],\"v\":\"";
      payload += VERSION;
      payload += "\"}";
      sendJsonLine(payload);
    }
  }

  // Small delay to prevent overwhelming the serial buffer
  delay(1);
}

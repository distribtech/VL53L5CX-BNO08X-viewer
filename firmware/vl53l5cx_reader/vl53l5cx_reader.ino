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
String lastFramePayload = "{\"status\":\"waiting_for_first_frame\"}";
uint32_t lastFrameMillis = 0;
uint32_t frameCounter = 0;

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
  html += F("<a class='btn' href='/app'>Use ESP32 web interface (default)</a>");
  html += F("<a class='btn secondary' href='/python'>Use Python <code>-m viewer</code></a>");
  html += F("<h2>Connection details</h2><ul>");
  html += F("<li>ESP32 web UI: <code>http://192.168.4.1/</code></li>");
  html += F("<li>ESP32 Three.js view: <code>http://192.168.4.1/app</code></li>");
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
  html.reserve(9800);
  html += F("<!doctype html><html><head><meta charset='utf-8'>");
  html += F("<meta name='viewport' content='width=device-width,initial-scale=1'>");
  html += F("<title>ESP32 ToF 3D Viewer</title>");
  html += F("<style>");
  html += F(":root{color-scheme:dark;}*{box-sizing:border-box;}body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,sans-serif;background:radial-gradient(circle at 20% 10%,#1f2937,#030712 65%);color:#f9fafb;}");
  html += F("#hud{position:fixed;top:12px;left:12px;right:12px;display:flex;gap:10px;flex-wrap:wrap;z-index:2;}");
  html += F(".card{background:rgba(17,24,39,.78);border:1px solid rgba(148,163,184,.35);border-radius:12px;padding:10px 12px;backdrop-filter:blur(6px);}");
  html += F(".title{font-weight:700;font-size:14px;margin-bottom:2px;} .small{font-size:12px;color:#cbd5e1;}");
  html += F("#view{position:fixed;inset:0;}a{color:#93c5fd;text-decoration:none;} .warn{color:#fca5a5;}");
  html += F("</style>");
  html += F("</head><body>");
  html += F("<div id='hud'>");
  html += F("<div class='card'><div class='title'>ESP32 ToF Viewer</div><div class='small'>AP: <code>VL53L5CX-Viewer</code></div></div>");
  html += F("<div class='card'><div class='small'>Frame: <span id='frame'>-</span></div><div class='small'>Age: <span id='age'>-</span> ms</div></div>");
  html += F("<div class='card'><div class='small'>Valid zones: <span id='valid'>-</span>/64</div><div class='small'>Min/Max: <span id='minmax'>-</span></div></div>");
  html += F("<div class='card'><div class='small'>Sources: <a href='/latest'>/latest</a> | TCP <code>192.168.4.1:");
  html += String(WIFI_STREAM_PORT);
  html += F("</code></div><div class='small'><a href='/'>Mode selection</a> | <a href='/python'>Python mode</a></div></div>");
  html += F("<div class='card'><div class='small warn' id='net'>Waiting for sensor stream...</div></div>");
  html += F("</div>");
  html += F("<div id='view'></div>");
  html += F("<script type='importmap'>{\"imports\":{\"three\":\"https://unpkg.com/three@0.160.0/build/three.module.js\"}}</script>");
  html += F("<script type='module'>");
  html += F("import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js';");
  html += F("import { OrbitControls } from 'https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js';");
  html += F("const netEl=document.getElementById('net');const frameEl=document.getElementById('frame');const ageEl=document.getElementById('age');const validEl=document.getElementById('valid');const minmaxEl=document.getElementById('minmax');");
  html += F("const container=document.getElementById('view');const scene=new THREE.Scene();scene.fog=new THREE.Fog(0x030712,0.6,3.5);");
  html += F("const camera=new THREE.PerspectiveCamera(60,window.innerWidth/window.innerHeight,0.01,20);camera.position.set(0.0,0.55,0.85);");
  html += F("const renderer=new THREE.WebGLRenderer({antialias:true});renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));renderer.setSize(window.innerWidth,window.innerHeight);renderer.setClearColor(0x030712,1);container.appendChild(renderer.domElement);");
  html += F("const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.target.set(0,0.16,0);");
  html += F("scene.add(new THREE.AmbientLight(0xffffff,0.6));const dl=new THREE.DirectionalLight(0xffffff,0.9);dl.position.set(1.1,1.5,0.7);scene.add(dl);");
  html += F("const floor=new THREE.GridHelper(1.2,12,0x334155,0x1f2937);floor.position.y=-0.02;scene.add(floor);");
  html += F("const cloud=new THREE.Group();scene.add(cloud);const rotRoot=new THREE.Group();rotRoot.add(cloud);scene.add(rotRoot);");
  html += F("const rays=new THREE.Group();rotRoot.add(rays);");
  html += F("const points=[];const geom=new THREE.SphereGeometry(0.011,12,10);for(let i=0;i<64;i++){const mat=new THREE.MeshStandardMaterial({color:0x60a5fa,roughness:0.3,metalness:0.05,emissive:0x0b1220});const m=new THREE.Mesh(geom,mat);cloud.add(m);points.push(m);} ");
  html += F("const rayLines=[];for(let i=0;i<64;i++){const g=new THREE.BufferGeometry();const pos=new Float32Array([0,0,0,0,0,0]);g.setAttribute('position',new THREE.BufferAttribute(pos,3));const l=new THREE.Line(g,new THREE.LineBasicMaterial({color:0x334155,transparent:true,opacity:0.7}));rays.add(l);rayLines.push(l);} ");
  html += F("function heatColor(mm){const t=Math.max(0,Math.min(1,(mm-120)/1900));const r=Math.round(255*Math.min(1,1.7*t));const g=Math.round(255*(1-Math.abs(t-0.45)*1.5));const b=Math.round(255*(1-t));return (r<<16)|(g<<8)|b;} ");
  html += F("const fov=65*Math.PI/180;const scale=Math.tan(fov/2);");
  html += F("function zoneDir(idx){const row=Math.floor(idx/8),col=idx%8;const nx=(col-3.5)/3.5,ny=(row-3.5)/3.5;const x=nx*scale,y=ny*scale,z=1;const l=Math.hypot(x,y,z)||1;return [x/l,-y/l,z/l];}");
  html += F("const dirs=Array.from({length:64},(_,i)=>zoneDir(i));");
  html += F("function quatToEuler(q){const [w,x,y,z]=q;const ys=2*(w*y-x*z);const pitch=Math.asin(Math.max(-1,Math.min(1,ys)));const roll=Math.atan2(2*(w*x+y*z),1-2*(x*x+y*y));const yaw=Math.atan2(2*(w*z+x*y),1-2*(y*y+z*z));return {roll,pitch,yaw};}");
  html += F("async function tick(){try{const r=await fetch('/latest',{cache:'no-store'});if(!r.ok)throw new Error('http '+r.status);const d=await r.json();if(!d.distances||d.distances.length!==64)throw new Error('no frame');");
  html += F("let valid=0,min=1e9,max=0;for(let i=0;i<64;i++){const mm=Number(d.distances[i]||0);const ok=(d.status&&d.status[i]===5&&mm>0);const p=points[i];const l=rayLines[i];if(!ok){p.visible=false;l.visible=false;continue;}valid++;min=Math.min(min,mm);max=Math.max(max,mm);const m=Math.min(mm,3500)/1000;const v=dirs[i];const x=v[0]*m,y=v[1]*m,z=v[2]*m;p.visible=true;p.position.set(x,y,z);p.material.color.setHex(heatColor(mm));const arr=l.geometry.attributes.position.array;arr[3]=x;arr[4]=y;arr[5]=z;l.geometry.attributes.position.needsUpdate=true;l.visible=true;l.material.color.setHex(heatColor(mm));}");
  html += F("if(Array.isArray(d.quat)&&d.quat.length===4){const e=quatToEuler(d.quat);rotRoot.rotation.set(e.roll,e.pitch,e.yaw,'XYZ');}");
  html += F("frameEl.textContent=d.frame??'-';ageEl.textContent=(d.age_ms??'-').toString();validEl.textContent=String(valid);minmaxEl.textContent=valid?`${Math.round(min)} / ${Math.round(max)} mm`:'-';netEl.textContent='Live stream active';netEl.style.color='#86efac';");
  html += F("}catch(err){netEl.textContent='Stream error: '+err.message;netEl.style.color='#fca5a5';}}");
  html += F("setInterval(tick,120);tick();");
  html += F("window.addEventListener('resize',()=>{camera.aspect=window.innerWidth/window.innerHeight;camera.updateProjectionMatrix();renderer.setSize(window.innerWidth,window.innerHeight);});");
  html += F("(function anim(){requestAnimationFrame(anim);controls.update();renderer.render(scene,camera);})();");
  html += F("</script></body></html>");
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
  webServer.on("/app", HTTP_GET, []() {
    webServer.send(200, "text/html", buildEsp32ModePage());
  });
  webServer.on("/esp32", HTTP_GET, []() {
    webServer.send(200, "text/html", buildEsp32ModePage());
  });
  webServer.on("/python", HTTP_GET, []() {
    webServer.send(200, "text/html", buildPythonModePage());
  });
  webServer.on("/latest", HTTP_GET, []() {
    String payload;
    payload.reserve(lastFramePayload.length() + 96);
    payload += "{\"frame\":";
    payload += String(frameCounter);
    payload += ",\"age_ms\":";
    payload += String(millis() - lastFrameMillis);
    payload += ",\"data\":";
    payload += lastFramePayload;
    payload += "}";
    // Keep legacy response shape by defaulting to frame JSON where possible.
    if (lastFramePayload.startsWith("{\"distances\":")) {
      // Merge metadata into the same object for frontend simplicity.
      int insertPos = lastFramePayload.lastIndexOf('}');
      if (insertPos > 0) {
        String merged = lastFramePayload.substring(0, insertPos);
        merged += ",\"frame\":";
        merged += String(frameCounter);
        merged += ",\"age_ms\":";
        merged += String(millis() - lastFrameMillis);
        merged += "}";
        webServer.send(200, "application/json", merged);
        return;
      }
    }
    webServer.send(200, "application/json", payload);
  });
  webServer.on("/status", HTTP_GET, []() {
    String payload = String("{\"ssid\":\"") + WIFI_AP_SSID + "\",\"stream_port\":" + WIFI_STREAM_PORT
      + ",\"imu\":" + (imuAvailable ? "true" : "false")
      + ",\"latest\":\"/latest\",\"app\":\"/app\"}";
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
      lastFramePayload = payload;
      lastFrameMillis = millis();
      frameCounter++;
      sendJsonLine(payload);
    }
  }

  // Small delay to prevent overwhelming the serial buffer
  delay(1);
}

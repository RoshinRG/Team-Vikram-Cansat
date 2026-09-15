/* ════════════════ STATE ════════════════ */
let velChart, altChart, trajChart, tempChart, pressChart, altProfilChart, descentRateChart;
let missionStartTime = null;
let darkModeEnabled = localStorage.getItem('darkMode') === 'true';
let tData = [], vData = [], aData = [], rData = [], azData = [], tempData = [], pressData = [], distData = [], altProfilData = [], descentRateData = [];
let altProfilMeta = [];
let lastAltitude = 0; // for descent rate calculation
let scene3, cam3, ren3, rocket3;
let acX = 0, acY = 0, acZ = 1;
let mapReady = false, threeReady = false;
let map, lfMarker, lfPolyline, flightPath = [];
let simInterval = null;
// GPS stats
let totalDist = 0, maxAlt = -Infinity, maxSpd = 0, lastLatLng = null;
// Simulate state
let simRunning = false, simStep = 0;
// Command & Control state
let systemArmed = false, lastCommandTime = null, lastCommandType = null, lastPacketId = 0;
// Anomaly tracking
let anomalies = [];
// Upgrades state
let phaseMarkers = [];
let lossHistory = [];
let sseSource = null;
let usePollingFallback = false;
let lastServerOkAt = Date.now();
let wasServerOk = null;
let wasSerialConnected = null;
let opsMode = false;
let pollTimer = null;
let sseFailCount = 0;
let lastResetToastAt = 0;
let lastAiFetchAt = 0;
const TAB_ORDER = ['dashboard', 'map', 'orientation', 'analysis', 'commands', 'events', 'components', 'simulated', 'video'];
const GAUGE_CIRC = 2 * Math.PI * 30; // r=30
const GS_TOKEN = localStorage.getItem('gsToken') || 'vikram-gs-local';
const CHART_POINT_CAP = 500;
let telemetryCursorTs = null;
let lastKnownSessionId = null;
let chartGpsCtx = null;
let simTrackCtx = null;

function gsHeaders(extra) {
    return Object.assign({ 'Content-Type': 'application/json', 'X-GS-Token': GS_TOKEN }, extra || {});
}

function escapeHtml(str) {
    return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function hasGpsFix(lat, lon) {
    if (!lat && !lon) return false;
    if (lat == null || lon == null) return false;
    const la = Number(lat), lo = Number(lon);
    if (!Number.isFinite(la) || !Number.isFinite(lo)) return false;
    if (la === 0 && lo === 0) return false;
    return true;
}

function gpsBearing(lat1, lon1, lat2, lon2) {
    const φ1 = lat1 * Math.PI / 180, φ2 = lat2 * Math.PI / 180;
    const Δλ = (lon2 - lon1) * Math.PI / 180;
    const y = Math.sin(Δλ) * Math.cos(φ2);
    const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
    return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

function applyLinkStatus(stat) {
    const live = !!(stat && stat.serial_connected);
    const hasData = !!(stat && (stat.has_telemetry || stat.packets_received));
    const dot = document.getElementById('dotInd');
    const el = document.getElementById('sText');
    if (dot) dot.classList.toggle('live', live);
    if (!el) return;
    if (live) el.textContent = 'CONNECTED';
    else if (hasData) el.textContent = 'DATA LOADED';
    else el.textContent = 'DISCONNECTED';
}

/* ════════════════ TABS ════════════════ */
document.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => {
        const id = t.dataset.t;
        document.querySelectorAll('.tab').forEach(x => x.classList.remove('on'));
        document.querySelectorAll('.panel').forEach(x => x.classList.remove('on'));
        t.classList.add('on');
        const panel = document.getElementById('p-' + id);
        if (panel) panel.classList.add('on');
        if (id === 'map') { initMap(); setTimeout(() => map && map.invalidateSize(), 200); }
        if (id === 'orientation' && !threeReady) init3D();
        if (id === 'simulated') loadSim();
        setTimeout(resizeCharts, 80);
    });
});

/* ════════════════ CHARTS ════════════════ */
function chartTheme() {
    const dark = document.body.classList.contains('dark');
    return {
        tick: dark ? '#d5dee8' : '#6b778a',
        grid: dark ? 'rgba(213,222,232,.28)' : 'rgba(11,21,36,.12)',
        border: dark ? 'rgba(213,222,232,.55)' : 'rgba(11,21,36,.32)',
        legend: dark ? '#d5dee8' : '#6b778a',
        infoLine: dark ? '#7ec8f0' : '#1a5f8a',
        infoLineSoft: dark ? 'rgba(126,200,240,.35)' : 'rgba(26,95,138,.45)'
    };
}

function scaleOpts(yLbl, extra = {}) {
    const t = chartTheme();
    const withAnnotations = extra.annotations === true;
    const beginAtZero = extra.beginAtZero === true;
    return {
        responsive: true,
        maintainAspectRatio: false,
        resizeDelay: 80,
        animation: { duration: 200 },
        plugins: {
            legend: { display: false },
            annotation: withAnnotations ? { annotations: {} } : undefined
        },
        layout: { padding: { top: 8, right: 10, bottom: 4, left: 8 } },
        scales: {
            y: {
                title: { display: true, text: yLbl, color: t.tick, font: { size: 10, family: "'IBM Plex Sans', sans-serif" } },
                beginAtZero: beginAtZero,
                grid: { color: t.grid, display: true },
                border: { display: true, color: t.border },
                ticks: { color: t.tick, maxTicksLimit: 6 }
            },
            x: {
                grid: { color: t.grid, display: true },
                border: { display: true, color: t.border },
                ticks: { color: t.tick, maxTicksLimit: 8, maxRotation: 0, autoSkip: true }
            }
        }
    };
}

function allCharts() {
    return [velChart, altChart, trajChart, tempChart, pressChart, altProfilChart, descentRateChart].filter(Boolean);
}

function resizeCharts() {
    requestAnimationFrame(() => {
        allCharts().forEach(c => { try { c.resize(); } catch (e) { /* ignore */ } });
        if (map) { try { map.invalidateSize(); } catch (e) { /* ignore */ } }
    });
}

function applyChartTheme() {
    const t = chartTheme();
    allCharts().forEach(chart => {
        Object.values(chart.options.scales || {}).forEach(s => {
            if (!s) return;
            if (s.ticks) s.ticks.color = t.tick;
            if (s.grid) {
                s.grid.color = t.grid;
                s.grid.display = true;
            }
            if (s.title) s.title.color = t.tick;
            s.border = s.border || {};
            s.border.display = true;
            s.border.color = t.border;
        });
        if (chart.options.plugins?.legend?.labels) chart.options.plugins.legend.labels.color = t.legend;
        (chart.data.datasets || []).forEach(ds => {
            const c = ds.borderColor;
            const isNavy = c === '#1a5f8a' || c === '#7ec8f0' || ds._vikramInfoLine;
            const isSoftNavy = (typeof c === 'string' && c.indexOf('26,95,138') !== -1) || c === t.infoLineSoft || ds._vikramInfoSoft;
            if (isNavy) {
                ds._vikramInfoLine = true;
                ds.borderColor = t.infoLine;
                ds.pointBackgroundColor = t.infoLine;
                ds.pointBorderColor = t.infoLine;
            } else if (isSoftNavy) {
                ds._vikramInfoSoft = true;
                ds.borderColor = t.infoLineSoft;
                ds.pointBackgroundColor = t.infoLineSoft;
                ds.pointBorderColor = t.infoLineSoft;
            }
        });
        chart.update('none');
    });
}

function initCharts() {
    if (typeof Chart !== 'undefined') {
        const plugin = window.ChartAnnotation || window['chartjs-plugin-annotation'];
        if (plugin) { try { Chart.register(plugin); } catch (e) { /* already registered */ } }
    }
    velChart = new Chart(document.getElementById('velChart'), {
        type: 'line',
        data: {
            labels: tData, datasets: [{
                label: 'Velocity', data: vData,
                borderColor: '#c45e12', backgroundColor: 'rgba(196,94,18,.08)',
                borderWidth: 2, fill: true, tension: .4, pointRadius: 0
            }]
        },
        options: scaleOpts('Accel Magnitude (g)', { beginAtZero: true })
    });
    altChart = new Chart(document.getElementById('altChart'), {
        type: 'line',
        data: {
            labels: tData, datasets: [{
                label: 'Altitude', data: aData,
                borderColor: '#c62828', backgroundColor: 'rgba(198,40,40,.08)',
                borderWidth: 2, fill: true, tension: .4, pointRadius: 0
            }]
        },
        options: scaleOpts('Altitude (m)', { beginAtZero: true, annotations: true })
    });
    trajChart = new Chart(document.getElementById('trajChart'), {
        type: 'line',
        data: {
            labels: tData,
            datasets: [
                {
                    label: 'Ground range (km)', data: rData, yAxisID: 'y',
                    borderColor: '#1a5f8a', backgroundColor: 'rgba(26,95,138,.08)',
                    borderWidth: 2, fill: false, tension: 0.15, pointRadius: 0
                },
                {
                    label: 'Heading (deg)', data: azData, yAxisID: 'y2',
                    borderColor: '#c45e12', backgroundColor: 'transparent',
                    borderWidth: 2, fill: false, tension: 0.15, pointRadius: 0
                }
            ]
        },
        options: (() => {
            const t = chartTheme();
            return {
                responsive: true, maintainAspectRatio: false, resizeDelay: 80,
                animation: { duration: 200 },
                plugins: {
                    legend: { display: true, labels: { color: t.legend, boxWidth: 12, font: { size: 10 } } }
                },
                scales: {
                    x: {
                        title: { display: true, text: 'Time (s)', color: t.tick },
                        grid: { color: t.grid }, ticks: { color: t.tick, maxTicksLimit: 8 }
                    },
                    y: {
                        title: { display: true, text: 'Ground range (km)', color: t.tick },
                        grid: { color: t.grid }, ticks: { color: t.tick }, beginAtZero: true
                    },
                    y2: {
                        position: 'right',
                        title: { display: true, text: 'Heading (deg)', color: t.tick },
                        min: 0, max: 360,
                        grid: { drawOnChartArea: false }, ticks: { color: t.tick }
                    }
                }
            };
        })()
    });
    tempChart = new Chart(document.getElementById('tempChart'), {
        type: 'line',
        data: {
            labels: tData, datasets: [{
                label: 'Temperature', data: tempData,
                borderColor: '#c45e12', backgroundColor: 'rgba(196,94,18,.08)',
                borderWidth: 2, fill: true, tension: .4, pointRadius: 0
            }]
        },
        options: scaleOpts('Temperature (°C)')
    });
    pressChart = new Chart(document.getElementById('pressChart'), {
        type: 'line',
        data: {
            labels: tData, datasets: [{
                label: 'Pressure', data: pressData,
                borderColor: '#1a5f8a', backgroundColor: 'rgba(26,95,138,.08)',
                borderWidth: 2, fill: true, tension: .4, pointRadius: 0
            }]
        },
        options: scaleOpts('Pressure (hPa)')
    });
    altProfilChart = new Chart(document.getElementById('altProfilChart'), {
        type: 'line',
        data: {
            datasets: [
                {
                    label: 'GPS-derived',
                    data: [],
                    borderColor: '#1a5f8a',
                    backgroundColor: 'rgba(26,95,138,.08)',
                    borderWidth: 2, fill: false, tension: 0,
                    pointRadius: 2.5, pointHoverRadius: 5, pointHitRadius: 8,
                    spanGaps: false
                },
                {
                    label: 'Estimated (no GPS)',
                    data: [],
                    borderColor: '#1a5f8a',
                    backgroundColor: 'transparent',
                    borderWidth: 2, fill: false, tension: 0,
                    borderDash: [6, 4],
                    pointRadius: 2.5, pointHoverRadius: 5, pointHitRadius: 8,
                    spanGaps: false
                },
                {
                    label: 'No GPS fix',
                    data: [],
                    borderColor: 'rgba(26,95,138,.45)',
                    backgroundColor: 'transparent',
                    borderWidth: 2, fill: false, tension: 0,
                    borderDash: [2, 4],
                    pointRadius: 2.5, pointHoverRadius: 5, pointHitRadius: 8,
                    spanGaps: false
                }
            ]
        },
        options: (() => {
            const t = chartTheme();
            return {
                responsive: true,
                maintainAspectRatio: false,
                resizeDelay: 80,
                animation: { duration: 200 },
                parsing: false,
                interaction: { mode: 'nearest', intersect: false, axis: 'x' },
                plugins: {
                    legend: {
                        display: true,
                        labels: { color: t.legend, boxWidth: 12, font: { size: 10 } }
                    },
                    annotation: { annotations: {} },
                    tooltip: {
                        enabled: true,
                        filter(item) {
                            return item && item.raw && item.raw.y != null;
                        },
                        callbacks: {
                            title(items) {
                                const raw = items[0] && items[0].raw;
                                if (!raw || raw.y == null) return '';
                                return `t = ${((raw.ts || 0) / 1000).toFixed(2)} s`;
                            },
                            label() { return ''; },
                            afterBody(items) {
                                const raw = items[0] && items[0].raw;
                                if (!raw || raw.y == null) return [];
                                const src = raw.source === 'gps' ? 'GPS-derived'
                                    : raw.source === 'estimated' ? 'Estimated (no GPS)'
                                    : 'No GPS fix';
                                const lines = [
                                    `Altitude: ${Number(raw.y).toFixed(1)} m`,
                                    `Distance along track: ${Number(raw.x).toFixed(1)} m`,
                                    `v_z: ${Number(raw.vz || 0).toFixed(2)} m/s`,
                                    src
                                ];
                                if (raw.phase) lines.push(`Phase: ${String(raw.phase).replace(/_/g, ' ')}`);
                                return lines;
                            }
                        }
                    }
                },
                layout: { padding: { top: 8, right: 10, bottom: 4, left: 8 } },
                scales: {
                    x: {
                        type: 'linear',
                        title: { display: true, text: 'Distance along track (m)', color: t.tick, font: { size: 10, family: "'IBM Plex Sans', sans-serif" } },
                        grid: { color: t.grid, display: true },
                        border: { display: true, color: t.border },
                        ticks: { color: t.tick, maxTicksLimit: 8, callback: (v) => Number(v).toFixed(0) }
                    },
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Altitude (m)', color: t.tick, font: { size: 10, family: "'IBM Plex Sans', sans-serif" } },
                        grid: { color: t.grid, display: true },
                        border: { display: true, color: t.border },
                        ticks: { color: t.tick, maxTicksLimit: 6 }
                    }
                }
            };
        })()
    });
    descentRateChart = new Chart(document.getElementById('descentRateChart'), {
        type: 'line',
        data: {
            labels: tData, datasets: [{
                label: 'Vertical Velocity', data: descentRateData,
                borderColor: '#0f7a4f', backgroundColor: 'rgba(15,122,79,.08)',
                borderWidth: 2, fill: true, tension: .4, pointRadius: 0
            }]
        },
        options: scaleOpts('Vertical Velocity (m/s)', { annotations: true })
    });
    window.addEventListener('resize', resizeCharts);
}

/* ════════════════ LEAFLET MAP ════════════════ */
function initMap() {
    if (mapReady) return true;
    const el = document.getElementById('liveMap');
    if (!el) return false;
    mapReady = true;

    // Default centre: Sriperumbudur (your location area)
    const defaultLat = 12.9626, defaultLon = 79.9541;

    try {
        map = L.map('liveMap', { zoomControl: true, attributionControl: true })
            .setView([defaultLat, defaultLon], 15);
    } catch (e) {
        mapReady = false;
        lfPolyline = null;
        return false;
    }

    // OpenStreetMap tile layer
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© <a href="https://openstreetmap.org">OpenStreetMap</a> contributors',
        maxZoom: 19
    }).addTo(map);

    // Position marker
    const markerHtml = `
    <div style="
      width:16px;height:16px;
      background:#c62828;
      border:2.5px solid #fff;
      border-radius:50%;
      box-shadow:0 2px 8px rgba(11,21,36,.35);
    "></div>`;
    const icon = L.divIcon({ className: '', html: markerHtml, iconSize: [16, 16], iconAnchor: [8, 8] });

    lfMarker = L.marker([defaultLat, defaultLon], { icon }).addTo(map)
        .bindPopup('<b style="color:#1a5f8a;font-family:IBM Plex Mono,monospace">CanSat · NEO-7M</b><br><span id="popupCoords">Waiting for GPS…</span>');

    lfPolyline = L.polyline([], { color: '#1a5f8a', weight: 2.5, opacity: .85 }).addTo(map);

    // Trail toggle
    document.getElementById('chkTrail').addEventListener('change', e => {
        if (e.target.checked) map.addLayer(lfPolyline);
        else map.removeLayer(lfPolyline);
    });

    // Clear trail
    document.getElementById('btnClearTrail').addEventListener('click', () => {
        flightPath = []; lfPolyline.setLatLngs([]);
        totalDist = 0; maxAlt = -Infinity; maxSpd = 0; lastLatLng = null;
        updateGPSStats(0, 0, 0);
    });

    // Simulate GPS button (on map tab)
    document.getElementById('btnSimMap').addEventListener('click', toggleSimFlight);
    return true;
}

/* ── updateMap called with live or simulated GPS data ── */
function updateMap(lat, lon, alt, spd, hdg, sats, hdop) {
    if (!mapReady || !hasGpsFix(lat, lon)) return;

    const ll = [lat, lon];
    lfMarker.setLatLng(ll);
    lfMarker.getPopup() && lfMarker.setPopupContent(
        `<b style="color:#00e5ff;font-family:monospace">CanSat · NEO-7M</b><br>
        <span style="font-family:monospace;font-size:.85em;color:#cce8f4">
        ${lat.toFixed(6)}, ${lon.toFixed(6)}<br>Alt: ${(alt || 0).toFixed(1)} m
        </span>`
    );

    flightPath.push(ll);
    lfPolyline.setLatLngs(flightPath);

    // Auto pan only for first 8 points or big jump
    if (flightPath.length <= 8) {
        map.panTo(ll, { animate: true, duration: .5 });
    }

    // Distance calculation
    if (lastLatLng) {
        const d = haversine(lastLatLng[0], lastLatLng[1], lat, lon);
        totalDist += d;
    }
    lastLatLng = ll;
    if (alt > maxAlt) maxAlt = alt;
    if (spd > maxSpd) maxSpd = spd;

    // Compass
    if (hdg != null) {
        document.getElementById('needle').style.transform = `translateX(-50%) rotate(${hdg}deg)`;
        document.getElementById('mHdg').textContent = hdg.toFixed(1) + '°';
    }

    // Stats
    document.getElementById('mLat').textContent = lat.toFixed(6);
    document.getElementById('mLon').textContent = lon.toFixed(6);
    document.getElementById('mAlt').textContent = (alt || 0).toFixed(1);
    document.getElementById('mSpd').textContent = (spd || 0).toFixed(2);
    document.getElementById('gSat').textContent = sats || '--';
    document.getElementById('gHdop').textContent = hdop || '--';
    updateGPSStats(totalDist, maxAlt, maxSpd);

    // Sidebar
    document.getElementById('sLat').textContent = lat.toFixed(5);
    document.getElementById('sLon').textContent = lon.toFixed(5);
    document.getElementById('sGAlt').textContent = (alt || 0).toFixed(1);
}

function updateGPSStats(dist, mAlt, mSpd) {
    document.getElementById('gDist').textContent = dist > 1000 ? (dist / 1000).toFixed(2) + ' km' : dist.toFixed(0) + ' m';
    document.getElementById('gMaxAlt').textContent = mAlt === (-Infinity) ? '--' : mAlt.toFixed(1) + ' m';
    document.getElementById('gMaxSpd').textContent = mSpd ? mSpd.toFixed(2) + ' m/s' : '--';
    document.getElementById('gPts').textContent = flightPath.length;
}

function haversine(lat1, lon1, lat2, lon2) {
    const R = 6371000, dLat = (lat2 - lat1) * Math.PI / 180, dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/* ════════════════ SIMULATE FLIGHT ════════════════ */
// Simulates a CanSat launch arc near Sriperumbudur
const SIM_BASE_LAT = 12.9626, SIM_BASE_LON = 79.9541;
function toggleSimFlight() {
    if (simRunning) {
        clearInterval(simInterval);
        simRunning = false; simStep = 0;
        const bSim = document.getElementById('btnSim');
        const bMap = document.getElementById('btnSimMap');
        if (bSim) bSim.textContent = 'Simulate Flight';
        if (bMap) bMap.textContent = 'Simulate GPS';
        return;
    }
    simRunning = true; simStep = 0;
    lastAltitude = 0;
    simTrackCtx = newTrackCtx();
    resetChartBuffers();
    try { initMap(); } catch (e) { console.warn('Map not ready for sim', e); }
    if (lfPolyline) {
        flightPath = [];
        try { lfPolyline.setLatLngs([]); } catch (e) { /* */ }
    }
    totalDist = 0; maxAlt = -Infinity; maxSpd = 0; lastLatLng = null;
    const bSim = document.getElementById('btnSim');
    const bMap = document.getElementById('btnSimMap');
    if (bSim) bSim.textContent = 'Stop Simulation';
    if (bMap) bMap.textContent = 'Stop Simulation';
    setTimeout(() => map && map.invalidateSize(), 100);

    simInterval = setInterval(() => {
        try {
            simStep++;
            const t = simStep * 0.5;
            const alt = Math.max(0, 500 * Math.sin(Math.PI * t / 60));
            const lat = SIM_BASE_LAT + (simStep * 0.00008);
            const lon = SIM_BASE_LON + (simStep * 0.00005);
            const spd = simStep < 60 ? 8 + simStep * 0.3 : Math.max(0, 30 - simStep * 0.3);
            const hdg = 35 + (Math.sin(t * 0.3) * 15);
            const ax = (Math.random() - .5) * 0.3;
            const ay = (Math.random() - .5) * 0.3;
            const az = 0.9 + alt / 5000;
            const gx = (Math.random() - .5) * 2, gy = (Math.random() - .5) * 2, gz = (Math.random() - .5) * 2;
            const gpsDrop = simStep >= 25 && simStep <= 35;
            const gpsLat = gpsDrop ? null : lat;
            const gpsLon = gpsDrop ? null : lon;
            const vz = (alt - lastAltitude) / 0.5;

            try { updateMap(gpsLat, gpsLon, alt, spd, hdg, gpsDrop ? 0 : 8, gpsDrop ? null : 1.2); } catch (e) { /* */ }
            const simPkt = {
                ts: simStep * 500, alt, ax, ay, az, gx, gy, gz,
                lat: gpsLat, lon: gpsLon, gps_alt: alt, speed: spd, heading: hdg,
                launched: simStep > 3, ejected: simStep > 55, v_z: vz,
                phase: simStep > 55 ? 'RECOVERY'
                    : simStep <= 3 ? 'PRE-LAUNCH'
                    : vz < -0.5 ? 'DESCENT'
                    : (Math.abs(vz) < 0.5 && alt > 450) ? 'APOGEE'
                    : vz > 0.5 ? 'POWERED_ASCENT' : 'COAST'
            };
            try { updateTelemetryUI(simPkt); } catch (e) { /* */ }

            const aM = Math.sqrt(ax * ax + ay * ay + az * az);
            const descentRate = -vz;
            lastAltitude = alt;

            tData.push(t.toFixed(1)); vData.push(aM); aData.push(alt);
            descentRateData.push(descentRate);
            const rngKm = totalDist / 1000;
            rData.push(rngKm);
            azData.push(hdg);
            if (!simTrackCtx) simTrackCtx = newTrackCtx();
            attachDistanceAlongTrack(simPkt, simTrackCtx);
            pushAltProfilSample(simPkt);
            tempData.push(20 + Math.sin(t * 0.05) * 10);
            pressData.push((101325 - alt * 12) / 100);
            if (tData.length > 120) {
                tData.shift(); vData.shift(); aData.shift(); descentRateData.shift();
                rData.shift(); azData.shift(); tempData.shift(); pressData.shift();
                distData.shift(); altProfilData.shift(); altProfilMeta.shift();
            }
            refreshCharts();

            if (alt <= 0 && simStep > 30) {
                clearInterval(simInterval); simRunning = false; simStep = 0;
                if (bSim) bSim.textContent = 'Simulate Flight';
                if (bMap) bMap.textContent = 'Simulate GPS';
            }
        } catch (err) {
            console.error('Simulate step failed', err);
        }
    }, 500);
}

/* ════════════════ TELEMETRY UI UPDATE ════════════════ */
function updateTelemetryUI(pt) {
    const ax = pt.ax || 0, ay = pt.ay || 0, az = pt.az || 0;
    const gx = pt.gx || 0, gy = pt.gy || 0, gz = pt.gz || 0;
    const aM = Math.sqrt(ax * ax + ay * ay + az * az);
    const gM = Math.sqrt(gx * gx + gy * gy + gz * gz);

    document.getElementById('paramTime').textContent = ((pt.ts || 0) / 1000).toFixed(2);
    document.getElementById('paramAlt').textContent = (pt.alt || 0).toFixed(2);
    document.getElementById('paramAcc').textContent = aM.toFixed(3);
    document.getElementById('paramGyr').textContent = gM.toFixed(3);

    // New sensor metrics
    document.getElementById('paramTemp').textContent = (pt.temp || 25).toFixed(1);
    document.getElementById('paramPress').textContent = ((pt.pressure || 101325) / 100).toFixed(1);
    document.getElementById('paramHumid').textContent = (pt.humidity || 50).toFixed(0);
    document.getElementById('paramBatt').textContent = (pt.batt_v || 3.7).toFixed(2);
    document.getElementById('paramRssi').textContent = (pt.rssi || -100).toFixed(0);
    document.getElementById('paramSnr').textContent = (pt.snr || 0).toFixed(1);
    updateGauges(pt.batt_v || 3.7, pt.rssi || -100);

    document.getElementById('ax').textContent = ax.toFixed(4); document.getElementById('ay').textContent = ay.toFixed(4);
    document.getElementById('az').textContent = az.toFixed(4); document.getElementById('aM').textContent = aM.toFixed(4);
    document.getElementById('gx').textContent = gx.toFixed(4); document.getElementById('gy').textContent = gy.toFixed(4);
    document.getElementById('gz').textContent = gz.toFixed(4); document.getElementById('gM').textContent = gM.toFixed(4);

    document.getElementById('dAlt').textContent = (pt.alt || 0).toFixed(2) + ' m';
    document.getElementById('dTs').textContent = ((pt.ts || 0) / 1000).toFixed(2) + ' s';

    const bl = document.getElementById('bLaunch'), be = document.getElementById('bEject');
    bl.textContent = 'LAUNCHED: ' + (pt.launched ? 'YES' : 'NO'); bl.className = 'badge ' + (pt.launched ? 'ok' : 'off');
    be.textContent = 'EJECTED: ' + (pt.ejected ? 'YES' : 'NO'); be.className = 'badge ' + (pt.ejected ? 'warn' : 'off');

    acX = ax * 2; acY = ay * 2; acZ = az;
    const rx = (acX * 57.3).toFixed(1) + '°', ry = (acY * 57.3).toFixed(1) + '°', rz = (acZ * 57.3).toFixed(1) + '°';
    document.getElementById('rollA').textContent = rx;
    document.getElementById('pitchA').textContent = ry;
    document.getElementById('yawA').textContent = rz;
    document.getElementById('roR').textContent = rx;
    document.getElementById('roP').textContent = ry;
    document.getElementById('roY').textContent = rz;

    // M6: Link margin calculations
    calculateLinkMetrics(pt);

    // M5: Start mission clock on first packet
    if (!missionStartTime && pt.launched) {
        missionStartTime = Date.now();
    }
}

/* ════════════════ M6: LINK MARGIN CALCULATION ════════════════ */
function calculateLinkMetrics(pt) {
    const rssi = pt.rssi || -100;
    const snr = pt.snr || 0;

    // LoRa path loss estimate (simplified Friis equation for 433 MHz)
    // Path Loss = 20 * log10(distance_km) + 20 * log10(freq_MHz) - 87.35
    // Assume max range ~10km at typical LoRa sensitivity
    const freq = 433; // MHz
    const txPower = 14; // dBm typical for LoRa
    const rxSensitivity = -137; // dBm typical for LoRa SF7

    // Estimated path loss from RSSI
    const estimatedPathLoss = txPower - rssi;

    // Link margin = received signal - required signal
    const linkMargin = rssi - rxSensitivity;

    // SNR quality assessment
    let snrQuality = 'UNKNOWN';
    if (snr >= 10) snrQuality = 'EXCELLENT';
    else if (snr >= 5) snrQuality = 'GOOD';
    else if (snr >= 0) snrQuality = 'FAIR';
    else snrQuality = 'POOR';

    document.getElementById('pathLoss').textContent = estimatedPathLoss.toFixed(1) + ' dB';
    document.getElementById('linkMargin').textContent = linkMargin.toFixed(1) + ' dB';
    document.getElementById('snrQuality').textContent = snrQuality;
}

function calculatePacketLoss(totalPackets, receivedPackets) {
    if (totalPackets === 0) return 0;
    return ((1 - (receivedPackets / totalPackets)) * 100).toFixed(1);
}

function capChartSeries() {
    while (tData.length > CHART_POINT_CAP) {
        tData.shift(); vData.shift(); aData.shift(); descentRateData.shift();
        rData.shift(); azData.shift(); tempData.shift(); pressData.shift();
        distData.shift(); altProfilData.shift(); altProfilMeta.shift();
    }
}

function newTrackCtx() {
    return {
        lastLat: null, lastLon: null, lastTs: null, lastAlt: null,
        trackLastLat: null, trackLastLon: null,
        distanceM: 0, hadGps: false, trackSource: 'none'
    };
}

function attachDistanceAlongTrack(p, ctx) {
    const ts = p.ts || 0;
    const alt = p.alt || 0;
    if (typeof p.v_z !== 'number' || !Number.isFinite(p.v_z)) {
        if (ctx.lastAlt != null && ctx.lastTs != null && (ts - ctx.lastTs) > 0) {
            p.v_z = (alt - ctx.lastAlt) / ((ts - ctx.lastTs) / 1000);
        } else {
            p.v_z = 0;
        }
    }
    const serverDist = p.distance_along_track;
    if (typeof serverDist === 'number' && Number.isFinite(serverDist) && p.track_source) {
        ctx.distanceM = serverDist;
        ctx.trackSource = p.track_source;
    } else {
        const gps = hasGpsFix(p.lat, p.lon);
        const prevGps = hasGpsFix(ctx.trackLastLat, ctx.trackLastLon);
        if (gps && prevGps) {
            ctx.distanceM += haversine(ctx.trackLastLat, ctx.trackLastLon, Number(p.lat), Number(p.lon));
            ctx.trackSource = 'gps';
        } else if (gps) {
            ctx.trackSource = 'gps';
        } else {
            const dt = ctx.lastTs != null ? Math.max(0, (ts - ctx.lastTs) / 1000) : 0;
            const spd = Number(p.speed);
            if (ctx.hadGps && dt > 0 && Number.isFinite(spd) && spd > 0) {
                ctx.distanceM += spd * dt;
                ctx.trackSource = 'estimated';
            } else {
                ctx.trackSource = 'none';
            }
        }
        p.distance_along_track = ctx.distanceM;
        p.track_source = ctx.trackSource;
    }
    if (hasGpsFix(p.lat, p.lon)) {
        ctx.trackLastLat = Number(p.lat);
        ctx.trackLastLon = Number(p.lon);
        ctx.hadGps = true;
    } else {
        ctx.trackLastLat = null;
        ctx.trackLastLon = null;
    }
    ctx.lastTs = ts;
    ctx.lastAlt = alt;
    return p;
}

function pushAltProfilSample(p) {
    altProfilMeta.push({
        ts: p.ts || 0,
        alt: p.alt || 0,
        distM: Number(p.distance_along_track) || 0,
        vz: Number(p.v_z) || 0,
        source: p.track_source || 'none',
        phase: p.phase || ''
    });
    distData.push((((p.distance_along_track) || 0) / 1000).toFixed(3));
    altProfilData.push(p.alt || 0);
}

function buildAltProfilPoint(m, include) {
    return {
        x: m.distM,
        y: include ? m.alt : null,
        ts: m.ts,
        vz: m.vz,
        source: m.source,
        phase: m.phase
    };
}

function refreshAltProfilChart() {
    if (!altProfilChart) return;
    altProfilChart.data.datasets[0].data = altProfilMeta.map(m => buildAltProfilPoint(m, m.source === 'gps'));
    altProfilChart.data.datasets[1].data = altProfilMeta.map(m => buildAltProfilPoint(m, m.source === 'estimated'));
    altProfilChart.data.datasets[2].data = altProfilMeta.map(m => buildAltProfilPoint(m, m.source === 'none'));
    applyAltProfilPhaseMarkers();
    altProfilChart.update('none');
}

function applyAltProfilPhaseMarkers() {
    if (!altProfilChart) return;
    const colorFor = (ph) => {
        const p = (ph || '').toUpperCase();
        if (p.includes('APOGEE')) return '#c45e12';
        if (p.includes('RECOVERY') || p.includes('EJECT')) return '#0f7a4f';
        if (p.includes('DESCENT')) return '#1a5f8a';
        return '#6b778a';
    };
    const annotations = {};
    const addAtDist = (key, distM, phase) => {
        annotations[key] = {
            type: 'line',
            xMin: distM,
            xMax: distM,
            borderColor: colorFor(phase),
            borderWidth: 2,
            borderDash: [4, 3],
            label: {
                display: true,
                content: String(phase || '').replace(/_/g, ' '),
                position: 'start',
                backgroundColor: colorFor(phase),
                color: '#fff',
                font: { size: 9, family: "'Space Grotesk', sans-serif" }
            }
        };
    };
    let lastPh = '';
    altProfilMeta.forEach((m, i) => {
        const ph = (m.phase || '').toUpperCase();
        if (ph && ph !== lastPh) {
            addAtDist('apm' + i, m.distM, m.phase);
            lastPh = ph;
        }
    });
    phaseMarkers.forEach((m, i) => {
        const tSec = (m.timestamp_ms || 0) / 1000;
        if (!altProfilMeta.length) return;
        let best = 0, bestD = Infinity;
        altProfilMeta.forEach((s, idx) => {
            const d = Math.abs((s.ts || 0) / 1000 - tSec);
            if (d < bestD) { bestD = d; best = idx; }
        });
        addAtDist('ph' + i, altProfilMeta[best].distM, m.phase);
    });
    if (!altProfilChart.options.plugins) altProfilChart.options.plugins = {};
    altProfilChart.options.plugins.annotation = { annotations };
}

function setGpsChartPlaceholder(show) {
    const canvas = document.getElementById('trajChart');
    if (!canvas || !canvas.parentElement) return;
    let box = document.getElementById('trajGpsPlaceholder');
    if (!box) {
        box = document.createElement('div');
        box.id = 'trajGpsPlaceholder';
        box.style.cssText = 'position:absolute;inset:0;display:none;align-items:center;justify-content:center;color:var(--dim);font-size:13px;pointer-events:none;text-align:center;padding:16px;z-index:2';
        canvas.parentElement.style.position = 'relative';
        canvas.parentElement.appendChild(box);
    }
    box.textContent = 'Insufficient GPS data — ground range and heading unavailable';
    box.style.display = show ? 'flex' : 'none';
    if (canvas) canvas.style.opacity = show ? '0.15' : '1';
}

function resetChartBuffers() {
    tData = []; vData = []; aData = []; rData = []; azData = [];
    tempData = []; pressData = []; distData = []; altProfilData = []; descentRateData = [];
    altProfilMeta = [];
    telemetryCursorTs = null;
}

function ingestPacketForCharts(p, i, ctx) {
    const t = (p.ts || i) / 1000;
    tData.push(t.toFixed(2));
    const ax = p.ax || 0, ay = p.ay || 0, az = p.az || 0;
    vData.push(Math.sqrt(ax * ax + ay * ay + az * az));
    const alt = p.alt || 0;
    aData.push(alt);
    tempData.push(p.temp || 25);
    pressData.push((p.pressure || 101325) / 100);

    let descentRate = 0;
    if (ctx.lastTime != null) {
        const timeDiff = t - ctx.lastTime;
        if (timeDiff > 0) descentRate = (ctx.lastAlt - alt) / timeDiff;
    }
    descentRateData.push(descentRate);
    attachDistanceAlongTrack(p, ctx);
    ctx.lastAlt = alt;
    ctx.lastTime = t;

    const lat = p.lat, lon = p.lon;
    if (hasGpsFix(ctx.lastLat, ctx.lastLon) && hasGpsFix(lat, lon)) {
        ctx.cumulativeDist += haversine(ctx.lastLat, ctx.lastLon, lat, lon);
        ctx.lastHeading = gpsBearing(ctx.lastLat, ctx.lastLon, lat, lon);
        ctx.gpsFixes += 1;
    } else if (hasGpsFix(lat, lon) && !hasGpsFix(ctx.lastLat, ctx.lastLon)) {
        ctx.gpsFixes += 1;
    }
    if (hasGpsFix(lat, lon)) {
        ctx.lastLat = Number(lat);
        ctx.lastLon = Number(lon);
    }
    rData.push(ctx.cumulativeDist / 1000);
    azData.push(ctx.lastHeading);
    pushAltProfilSample(p);
    if (p.ts != null && p.ts > (telemetryCursorTs || 0)) telemetryCursorTs = p.ts;
}

function newChartIngestCtx() {
    const n = tData.length;
    return {
        cumulativeDist: n ? (Number(distData[n - 1]) || 0) * 1000 : 0,
        lastLat: null,
        lastLon: null,
        lastAlt: n ? aData[n - 1] : 0,
        lastTime: n ? Number(tData[n - 1]) : null,
        lastHeading: n ? azData[n - 1] : 0,
        gpsFixes: rData.filter((r, i) => i > 0 && r > rData[i - 1]).length,
        distanceM: n && altProfilMeta[n - 1] ? altProfilMeta[n - 1].distM : 0,
        hadGps: altProfilMeta.some(m => m.source === 'gps'),
        trackSource: n && altProfilMeta[n - 1] ? altProfilMeta[n - 1].source : 'none',
        lastTs: n && altProfilMeta[n - 1] ? altProfilMeta[n - 1].ts : (n ? Number(tData[n - 1]) * 1000 : null),
        trackLastLat: null,
        trackLastLon: null
    };
}

function refreshCharts() {
    if (!velChart) return;
    velChart.data.labels = tData; velChart.data.datasets[0].data = vData; velChart.update('none');
    altChart.data.labels = tData; altChart.data.datasets[0].data = aData; altChart.update('none');
    tempChart.data.labels = tData; tempChart.data.datasets[0].data = tempData; tempChart.update('none');
    pressChart.data.labels = tData; pressChart.data.datasets[0].data = pressData; pressChart.update('none');
    descentRateChart.data.labels = tData; descentRateChart.data.datasets[0].data = descentRateData; descentRateChart.update('none');
    refreshAltProfilChart();
    if (trajChart) {
        trajChart.data.labels = tData;
        trajChart.data.datasets[0].data = rData;
        trajChart.data.datasets[1].data = azData;
        trajChart.update('none');
    }
    const validGpsPts = rData.filter((r, i) => i > 0 && r !== rData[i - 1]).length;
    setGpsChartPlaceholder(validGpsPts < 1 && tData.length > 2);
}

/* ════════════════ LIVE TELEMETRY FETCH ════════════════ */
async function fetchTelemetry() {
    if (simRunning) return; // Skip live fetch during simulation
    try {
        const wantsAi = document.querySelector('.tab.on')?.dataset?.t === 'analysis'
            || (Date.now() - lastAiFetchAt > 15000);
        const telUrl = telemetryCursorTs != null
            ? `/telemetry/all?since_ts=${encodeURIComponent(telemetryCursorTs)}`
            : '/telemetry/all';
        const fetches = [fetch(telUrl), fetch('/status')];
        if (wantsAi) fetches.push(fetch('/api/ai-analysis/latest'));
        const results = await Promise.all(fetches);
        let data = await results[0].json(), stat = await results[1].json();
        if (!Array.isArray(data)) return;
        let aiData = {};
        if (results[2]) {
            lastAiFetchAt = Date.now();
            try { aiData = await results[2].json(); } catch (e) { }
        } else if (stat.flight_phase) {
            aiData = { phase: stat.flight_phase };
        }

        const live = !!stat.serial_connected;
        applyLinkStatus(stat);
        document.getElementById('pkts').textContent = stat.packets_received || 0;
        handleResetInfo(stat.reset);
        if (wasSerialConnected !== null && wasSerialConnected !== live) {
            toast(live ? 'Link up' : 'Link down',
                live ? `Receiving on ${stat.port || 'serial'}` : 'No live serial telemetry',
                live ? 'ok' : 'warn');
        }
        wasSerialConnected = live;
        setLinkHealthy(true);

        const sessionChanged = !!(stat.session_id && lastKnownSessionId && stat.session_id !== lastKnownSessionId);
        lastKnownSessionId = stat.session_id || lastKnownSessionId;
        if (sessionChanged) {
            resetChartBuffers();
            chartGpsCtx = null;
            const fullR = await fetch('/telemetry/all');
            const full = await fullR.json();
            if (Array.isArray(full)) data = full;
        }

        if (missionStartTime === null && (data.length > 0 || tData.length > 0)) {
            missionStartTime = Date.now();
        }
        updateMissionClock();

        if (aiData.phase) {
            document.getElementById('fsmState').textContent = aiData.phase;
            const fsm2 = document.getElementById('fsmState2');
            if (fsm2) fsm2.textContent = aiData.phase;
        }

        if (!chartGpsCtx || tData.length === 0) {
            if (tData.length === 0) {
                chartGpsCtx = Object.assign(newTrackCtx(), { cumulativeDist: 0, lastHeading: 0, gpsFixes: 0, lastTime: null });
            } else {
                chartGpsCtx = newChartIngestCtx();
            }
        }
        data.forEach((p, i) => ingestPacketForCharts(p, tData.length + i, chartGpsCtx));
        capChartSeries();
        const dpts = document.getElementById('dpts');
        if (dpts) dpts.textContent = tData.length;

        if (data.length > 0) {
            const pt = data[data.length - 1];
            updateTelemetryUI(pt);
            detectAnomalies(data);
            updateMap(pt.lat, pt.lon, pt.gps_alt || pt.alt || 0, pt.speed || 0, pt.heading || 0, pt.satellites || null, pt.hdop || null);
            if (stat.packets_received && tData.length) {
                const packetLossPercent = calculatePacketLoss(stat.packets_received, tData.length);
                document.getElementById('packetLoss').textContent = packetLossPercent + '%';
            }
        }
        refreshCharts();
        applyPhaseMarkers();
        setLinkHealthy(true);
    } catch (e) {
        setLinkHealthy(false, e && e.message);
    }
}

/* ════════════════ 3D ROCKET ════════════════ */
function init3D() {
    if (threeReady) return; threeReady = true;
    scene3 = new THREE.Scene(); scene3.background = new THREE.Color(0x040d18);
    cam3 = new THREE.PerspectiveCamera(60, 2, .1, 1000); cam3.position.set(0, 0, 8);
    const cv = document.getElementById('canvas3d');
    const W = cv.parentElement.clientWidth;
    ren3 = new THREE.WebGLRenderer({ canvas: cv, antialias: true });
    ren3.setSize(W, 360); ren3.setPixelRatio(window.devicePixelRatio);
    cam3.aspect = W / 360; cam3.updateProjectionMatrix();
    scene3.add(new THREE.AmbientLight(0xffffff, .8));
    const dl = new THREE.DirectionalLight(0xffffff, 1.2); dl.position.set(10, 10, 5); scene3.add(dl);
    const pl = new THREE.PointLight(0x00e5ff, .8); pl.position.set(-10, 10, 10); scene3.add(pl);
    rocket3 = mkRocket(); scene3.add(rocket3);
    animate3D();
    window.addEventListener('resize', () => {
        const w = cv.parentElement.clientWidth;
        cam3.aspect = w / 360; cam3.updateProjectionMatrix(); ren3.setSize(w, 360);
    });
}
function mkRocket() {
    const g = new THREE.Group();
    const loader = new THREE.STLLoader();
    loader.load('/api/model-stl', function (geometry) {
        geometry.computeBoundingBox();
        const bbox = geometry.boundingBox;
        const center = new THREE.Vector3();
        bbox.getCenter(center);
        geometry.translate(-center.x, -center.y, -center.z);
        const size = new THREE.Vector3();
        bbox.getSize(size);
        const scale = 5 / Math.max(size.x, size.y, size.z);
        geometry.scale(scale, scale, scale);
        const material = new THREE.MeshPhongMaterial({ color: 0x00e5ff, shininess: 100 });
        const mesh = new THREE.Mesh(geometry, material);
        g.clear();
        g.add(mesh);
    }, function (progress) {
        if (progress && progress.total) {
            console.log('STL loading:', (progress.loaded / progress.total * 100).toFixed(0) + '%');
        }
    }, function (error) {
        console.error('STL load error:', error);
        // Fallback to procedural rocket if STL fails
        g.add(Object.assign(new THREE.Mesh(new THREE.CylinderGeometry(.7, .7, 5, 32), new THREE.MeshPhongMaterial({ color: 0xff3333, shininess: 130 }))));
        const nose = new THREE.Mesh(new THREE.ConeGeometry(.7, 2.2, 32), new THREE.MeshPhongMaterial({ color: 0xffd700, shininess: 130 }));
        nose.position.y = 3.6; g.add(nose);
    });
    return g;
}
function animate3D() {
    requestAnimationFrame(animate3D);
    if (rocket3) {
        const tx = Math.atan2(acY, acZ), tz = -Math.atan2(acX, acZ);
        rocket3.rotation.x += (tx - rocket3.rotation.x) * .1;
        rocket3.rotation.z += (tz - rocket3.rotation.z) * .1;
        if (Math.abs(acX) < .1 && Math.abs(acY) < .1) rocket3.rotation.y += .006;
    }
    if (ren3) ren3.render(scene3, cam3);
}

/* ════════════════ SIMULATED OUTPUTS ════════════════ */
async function loadSim() {
    const c = document.getElementById('simOut');
    try {
        const r = await fetch('/api/simulated'); const d = await r.json();
        if (d.images && d.images.length) {
            c.innerHTML = '';
            d.images.forEach(fn => {
                const w = document.createElement('div'); w.className = 'sim-wrap';
                const img = document.createElement('img'); img.src = `/api/simulated/${fn}`; img.alt = fn;
                const l = document.createElement('div'); l.className = 'sl'; l.textContent = fn;
                w.appendChild(img); w.appendChild(l); c.appendChild(w);
            });
        } else c.innerHTML = '<div style="color:var(--dim);padding:20px">No simulated outputs found.</div>';
    } catch (e) { c.innerHTML = '<div style="color:var(--red);padding:20px">Error scanning outputs.</div>'; }
}

/* ════════════════ M4: COMMAND CONTROL ════════════════ */
async function sendCommand(cmd) {
    try {
        const payload = { command: cmd, packet_id: ++lastPacketId, timestamp: Date.now() };
        const r = await fetch('/cmd', { method: 'POST', headers: gsHeaders(), body: JSON.stringify(payload) });
        const result = await r.json();
        if (result.status === 'sent') {
            lastCommandTime = new Date().toLocaleTimeString();
            lastCommandType = cmd;
            playAudio('success');
            return true;
        }
    } catch (e) { playAudio('error'); }
    return false;
}

function updateCommandUI() {
    const armBtn = document.getElementById('btnArm'), deployBtn = document.getElementById('btnDeploy'), abortBtn = document.getElementById('btnAbort');
    const statusSpan = document.getElementById('cmdStatus');
    if (systemArmed) {
        statusSpan.textContent = 'ARMED';
        statusSpan.style.color = 'var(--green)';
        armBtn.textContent = 'Armed';
        armBtn.disabled = true;
        deployBtn.disabled = false;
        abortBtn.disabled = false;
    } else {
        statusSpan.textContent = 'DISARMED';
        statusSpan.style.color = 'var(--red)';
        armBtn.textContent = 'Arm System';
        armBtn.disabled = false;
        deployBtn.disabled = true;
        abortBtn.disabled = true;
    }
    document.getElementById('cmdLast').textContent = lastCommandType || '--';
    document.getElementById('cmdPacketId').textContent = lastPacketId || '--';
}

/* ════════════════ M5: DARK MODE & MISSION CLOCK ════════════════ */
function toggleDarkMode() {
    darkModeEnabled = !darkModeEnabled;
    localStorage.setItem('darkMode', darkModeEnabled);
    document.body.classList.toggle('dark', darkModeEnabled);
    document.getElementById('btnDarkMode').textContent = darkModeEnabled ? 'Light' : 'Dark';
    applyChartTheme();
}

function updateMissionClock() {
    if (!missionStartTime) {
        document.getElementById('missionClock').textContent = '00:00:00';
        return;
    }
    const elapsed = Math.floor((Date.now() - missionStartTime) / 1000);
    const h = Math.floor(elapsed / 3600), m = Math.floor((elapsed % 3600) / 60), s = elapsed % 60;
    document.getElementById('missionClock').textContent = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

/* ════════════════ M5: ANOMALY DETECTION & FSM ════════════════ */
function detectAnomalies(telemetry) {
    anomalies = [];
    if (!telemetry || telemetry.length === 0) return;

    const latest = telemetry[telemetry.length - 1];

    // Temperature anomaly
    if (latest.temp > 60 || latest.temp < 0) anomalies.push({ type: 'TEMP', severity: 'HIGH', value: latest.temp });

    // Pressure anomaly (losing altitude rapidly)
    if (telemetry.length > 3) {
        const pressureTrend = telemetry.slice(-3).map(t => t.pressure || 101325);
        if (Math.max(...pressureTrend) - Math.min(...pressureTrend) > 5000) {
            anomalies.push({ type: 'RAPID_DESCENT', severity: 'HIGH', value: 'Pressure drop detected' });
        }
    }

    // Low battery
    if (latest.batt_v < 3.2) anomalies.push({ type: 'LOW_BATTERY', severity: 'WARN', value: latest.batt_v });

    // Signal loss
    if (latest.rssi < -110) anomalies.push({ type: 'WEAK_SIGNAL', severity: 'WARN', value: latest.rssi });

    updateAnomalyTimeline();
}

function updateAnomalyTimeline() {
    const timeline = document.getElementById('anomalyTimeline');
    if (!timeline) return;

    if (anomalies.length === 0) {
        timeline.innerHTML = '<div style="color:var(--dim)">No anomalies detected</div>';
    } else {
        timeline.innerHTML = anomalies.map(a =>
            `<div style="padding:6px;background:${a.severity === 'HIGH' ? 'rgba(255,76,106,.1)' : 'rgba(255,215,0,.1)'};border-left:3px solid ${a.severity === 'HIGH' ? 'var(--red)' : 'var(--gold)'};border-radius:2px;margin:4px 0;font-size:.85em">
                <span style="color:${a.severity === 'HIGH' ? 'var(--red)' : 'var(--gold)'}"><strong>${a.type}</strong></span> - ${a.value}
            </div>`
        ).join('');
    }
}

/* ════════════════ AUDIO ALERTS ════════════════ */
function playAudio(type) {
    // Use Web Audio API for simple tones
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator(), gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);

        if (type === 'success') { osc.frequency.value = 800; gain.gain.setValueAtTime(0.1, ctx.currentTime); gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.1); osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.1); }
        else if (type === 'error') { osc.frequency.value = 300; gain.gain.setValueAtTime(0.1, ctx.currentTime); gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3); osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.3); }
        else if (type === 'ejection') {
            osc.frequency.value = 1000; gain.gain.setValueAtTime(0.15, ctx.currentTime);
            osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.05);
            setTimeout(() => { const o2 = ctx.createOscillator(), g2 = ctx.createGain(); o2.connect(g2); g2.connect(ctx.destination); o2.frequency.value = 800; g2.gain.setValueAtTime(0.15, ctx.currentTime); g2.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.1); o2.start(ctx.currentTime); o2.stop(ctx.currentTime + 0.1); }, 100);
        }
    } catch (e) { /* Web Audio not available */ }
}

/* ════════════════ AI ANALYSIS ════════════════ */
async function runAi() {
    const btn = document.getElementById('btnAi'), out = document.getElementById('aiOut');
    btn.disabled = true; btn.textContent = 'Analysing…';
    out.innerHTML = '<em>Connecting to flight intelligence…</em>';
    try {
        const r = await fetch('/api/ai-analysis'); const d = await r.json();
        if (d.analysis) out.innerHTML = escapeHtml(d.analysis).replace(/\n/g, '<br>');
        else if (d.error) out.innerHTML = `<span style="color:var(--red)">Analysis failed: ${escapeHtml(d.error)}</span>`;
    } catch (e) {
        out.innerHTML = '<span style="color:var(--red)">Could not reach AI engine. Is app.py running?</span>';
    } finally { btn.disabled = false; btn.textContent = 'Generate Live Report'; }
}

/* ════════════════ PDR ENDPOINTS ════════════════ */
async function fetchCommandLog() {
    try {
        const filter = document.getElementById('cmdFilter')?.value || '';
        const url = filter ? `/api/commands/log?filter=${encodeURIComponent(filter)}` : '/api/commands/log';
        const r = await fetch(url);
        const d = await r.json();
        const logDiv = document.getElementById('cmdLog');

        if (!d.commands || d.commands.length === 0) {
            logDiv.innerHTML = '<div class="empty-msg">No commands logged</div>';
        } else {
            logDiv.innerHTML = d.commands.map((cmd, i) => `
                <div class="log-entry">
                    <div class="log-entry-title">${i+1}. ${escapeHtml(cmd.command)}</div>
                    <div class="log-entry-meta">Status: ${escapeHtml(cmd.status)} | Packet: ${escapeHtml(cmd.packet_id)} | Sent: ${escapeHtml(cmd.sent_at)}</div>
                    ${cmd.acked_at ? `<div class="log-entry-ack">ACK at ${escapeHtml(cmd.acked_at)}</div>` : ''}
                </div>
            `).join('');
        }
    } catch (e) {
        document.getElementById('cmdLog').innerHTML = `<div class="error-msg">Error: ${escapeHtml(e.message)}</div>`;
    }
}

async function fetchMissionEvents() {
    try {
        const filter = document.getElementById('eventFilter')?.value || '';
        const url = filter ? `/api/events?type=${filter}` : '/api/events';
        const r = await fetch(url);
        const d = await r.json();
        const logDiv = document.getElementById('eventLog');

        if (!d.events || d.events.length === 0) {
            logDiv.innerHTML = '<div class="empty-msg">No events logged</div>';
        } else {
            logDiv.innerHTML = d.events.map((evt, i) => `
                <div class="log-entry log-entry--event">
                    <div class="log-entry-title log-entry-title--green">${i+1}. ${escapeHtml(evt.event_type)}</div>
                    <div class="log-entry-meta">Altitude: ${evt.altitude?.toFixed(1) || 0}m | Packet: ${escapeHtml(evt.packet_id)} | Time: ${escapeHtml(evt.created_at)}</div>
                    ${evt.event_note ? `<div class="log-entry-note">Note: ${escapeHtml(evt.event_note)}</div>` : ''}
                </div>
            `).join('');
        }
    } catch (e) {
        document.getElementById('eventLog').innerHTML = `<div class="error-msg">Error: ${escapeHtml(e.message)}</div>`;
    }
}

async function createEvent() {
    try {
        const type = document.getElementById('eventType')?.value || 'MANUAL';
        const note = document.getElementById('eventNote')?.value || '';

        const r = await fetch('/api/events', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ event_type: type, event_note: note })
        });

        if (r.ok) {
            const d = await r.json();
            playAudio('success');
            alert(`✓ Event logged: ${type}`);
            document.getElementById('eventNote').value = '';
            fetchMissionEvents();
        } else {
            alert('Failed to create event');
        }
    } catch (e) {
        alert(`Error: ${e.message}`);
    }
}

async function updateAnomaliesFromAPI() {
    try {
        const r = await fetch('/api/anomalies?limit=20');
        const d = await r.json();
        const timeline = document.getElementById('anomalyTimeline');

        if (!timeline) return;

        if (!d.anomalies || d.anomalies.length === 0) {
            timeline.innerHTML = '<div class="empty-msg">No anomalies detected</div>';
        } else {
            timeline.innerHTML = d.anomalies.map(a => {
                const sevClass = a.severity === 'HIGH' ? 'anomaly-item--high' : 'anomaly-item--warn';
                return `
                    <div class="anomaly-item ${sevClass}">
                        <span class="anomaly-type"><strong>${a.anomaly_type}</strong></span> - ${a.description}
                    </div>
                `;
            }).join('');
        }
    } catch (e) {
        /* Silently fail if API not ready */
    }
}

async function updateLinkMarginFromAPI() {
    try {
        const r = await fetch('/api/link-margin');
        const d = await r.json();

        if (d.rssi_dbm !== undefined) {
            document.getElementById('pathLoss').textContent = `${d.path_loss_db?.toFixed(1) || 0} dB`;
            document.getElementById('linkMargin').textContent = `${d.link_margin_db?.toFixed(1) || 0} dB`;
            document.getElementById('snrQuality').textContent = d.snr_quality || '—';
        }
    } catch (e) {
        /* Silently fail if API not ready */
    }
}

/* ════════════════ UPGRADES: TOASTS / LINK / GAUGES ════════════════ */
function toast(title, body, kind = 'ok', ms = 3200) {
    const host = document.getElementById('toastHost');
    if (!host) return;
    const el = document.createElement('div');
    el.className = `toast toast--${kind}`;
    el.innerHTML = `<div class="toast-title">${escapeHtml(title)}</div><div class="toast-body">${escapeHtml(body)}</div>`;
    host.appendChild(el);
    setTimeout(() => {
        el.style.opacity = '0';
        el.style.transform = 'translateX(12px)';
        el.style.transition = 'all .25s ease';
        setTimeout(() => el.remove(), 280);
    }, ms);
}

function setLinkHealthy(ok, detail) {
    const banner = document.getElementById('linkBanner');
    const lastEl = document.getElementById('linkLastGood');
    if (ok) {
        lastServerOkAt = Date.now();
        if (banner) banner.hidden = true;
        if (wasServerOk === false) toast('Link restored', 'Telemetry stream is back online', 'ok');
        wasServerOk = true;
    } else {
        if (banner) {
            banner.hidden = false;
            const age = Math.round((Date.now() - lastServerOkAt) / 1000);
            if (lastEl) lastEl.textContent = `Last good: ${age}s ago`;
            const txt = document.getElementById('linkBannerText');
            if (txt) txt.textContent = detail ? `Reconnecting… (${detail})` : 'No telemetry from server. Reconnecting…';
        }
        if (wasServerOk !== false) toast('Link lost', 'Dashboard cannot reach the ground station server', 'err', 4200);
        wasServerOk = false;
    }
}

function updateGauges(battV, rssi) {
    const battArc = document.getElementById('battArc');
    const rssiArc = document.getElementById('rssiArc');
    if (!battArc || !rssiArc) return;
    // Battery 3.0–4.2 V
    const battPct = Math.max(0, Math.min(1, (battV - 3.0) / 1.2));
    battArc.style.strokeDasharray = String(GAUGE_CIRC);
    battArc.style.strokeDashoffset = String(GAUGE_CIRC * (1 - battPct));
    battArc.style.stroke = battPct < 0.25 ? 'var(--alert)' : battPct < 0.5 ? 'var(--signal)' : 'var(--live)';
    // RSSI -120 … -50 dBm
    const rssiPct = Math.max(0, Math.min(1, (rssi + 120) / 70));
    rssiArc.style.strokeDasharray = String(GAUGE_CIRC);
    rssiArc.style.strokeDashoffset = String(GAUGE_CIRC * (1 - rssiPct));
    rssiArc.style.stroke = rssiPct < 0.3 ? 'var(--alert)' : rssiPct < 0.55 ? 'var(--signal)' : 'var(--info)';
}

function drawLossSpark() {
    const cv = document.getElementById('lossSpark');
    if (!cv || !cv.getContext) return;
    const ctx = cv.getContext('2d');
    const w = cv.width, h = cv.height;
    ctx.clearRect(0, 0, w, h);
    if (lossHistory.length < 2) return;
    const max = Math.max(5, ...lossHistory);
    ctx.beginPath();
    lossHistory.forEach((v, i) => {
        const x = (i / (lossHistory.length - 1)) * (w - 2) + 1;
        const y = h - 2 - (v / max) * (h - 6);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = getComputedStyle(document.body).getPropertyValue('--signal').trim() || '#c45e12';
    ctx.lineWidth = 1.5;
    ctx.stroke();
}

async function refreshMissionSummary() {
    try {
        const r = await fetch('/api/mission-summary');
        if (!r.ok) throw new Error('summary');
        applyMissionSummary(await r.json());
    } catch (e) { /* ignore */ }
}

function formatDuration(sec) {
    const s = Math.max(0, Math.floor(sec));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    if (h) return `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
    return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
}

/* ════════════════ PHASE MARKERS ON CHARTS ════════════════ */
async function loadPhaseMarkers() {
    try {
        const r = await fetch('/api/phases');
        const d = await r.json();
        phaseMarkers = d.phases || [];
        applyPhaseMarkers();
    } catch (e) { phaseMarkers = []; }
}

function applyPhaseMarkers() {
    if (!altChart || !descentRateChart) return;
    const annotations = {};
    const colorFor = (ph) => {
        const p = (ph || '').toUpperCase();
        if (p.includes('APOGEE')) return '#c45e12';
        if (p.includes('RECOVERY') || p.includes('EJECT')) return '#0f7a4f';
        if (p.includes('DESCENT')) return '#1a5f8a';
        return '#6b778a';
    };
    phaseMarkers.forEach((m, i) => {
        const tSec = ((m.timestamp_ms || 0) / 1000);
        // Find nearest label index
        let xVal = tSec.toFixed(2);
        if (tData.length) {
            let best = 0, bestD = Infinity;
            tData.forEach((lab, idx) => {
                const d = Math.abs(parseFloat(lab) - tSec);
                if (d < bestD) { bestD = d; best = idx; }
            });
            xVal = tData[best];
        }
        const key = `ph${i}`;
        annotations[key] = {
            type: 'line',
            xMin: xVal,
            xMax: xVal,
            borderColor: colorFor(m.phase),
            borderWidth: 2,
            borderDash: [4, 3],
            label: {
                display: true,
                content: (m.phase || '').replace(/_/g, ' '),
                position: 'start',
                backgroundColor: colorFor(m.phase),
                color: '#fff',
                font: { size: 9, family: "'Space Grotesk', sans-serif" }
            }
        };
    });
    if (altChart.options.plugins) {
        altChart.options.plugins.annotation = { annotations };
        try { altChart.update('none'); } catch (e) { /* annotation plugin missing */ }
    }
    if (descentRateChart.options.plugins) {
        descentRateChart.options.plugins.annotation = { annotations };
        try { descentRateChart.update('none'); } catch (e) { /* annotation plugin missing */ }
    }
    applyAltProfilPhaseMarkers();
    if (altProfilChart) {
        try { altProfilChart.update('none'); } catch (e) { /* */ }
    }
}

/* ════════════════ SESSIONS / EXPORT / COM PORT ════════════════ */
async function loadSessions() {
    const sel = document.getElementById('sessionSelect');
    if (!sel) return;
    try {
        const r = await fetch('/api/sessions');
        const d = await r.json();
        const current = d.current;
        const prev = sel.value;
        sel.innerHTML = '';
        const live = document.createElement('option');
        live.value = current || '';
        live.textContent = `Live · ${current || 'default'}`;
        sel.appendChild(live);
        (d.sessions || []).forEach(s => {
            if (s.session_id === current) return;
            const opt = document.createElement('option');
            opt.value = s.session_id;
            const peak = s.peak_altitude != null ? ` · ${Number(s.peak_altitude).toFixed(0)}m` : '';
            opt.textContent = `${s.session_id}${peak}`;
            sel.appendChild(opt);
        });
        if (prev) sel.value = prev;
    } catch (e) { /* ignore */ }
}

function exportCsv() {
    const sel = document.getElementById('sessionSelect');
    const sid = sel && sel.value;
    if (sid) {
        window.open(`/api/sessions/${encodeURIComponent(sid)}/export`, '_blank');
        toast('CSV export', `Downloading session ${sid}`, 'ok');
    } else {
        window.open('/db/export', '_blank');
        toast('CSV export', 'Downloading full database export', 'ok');
    }
}

function exportPdfReport() {
    window.open('/report', '_blank');
    toast('Flight report', 'Opened print-ready report — use Print → Save as PDF', 'ok');
}

async function switchComPort(port) {
    if (!port) return;
    try {
        const r = await fetch('/api/port', {
            method: 'POST',
            headers: gsHeaders(),
            body: JSON.stringify({ port })
        });
        const d = await r.json();
        if (!r.ok) {
            toast('Port switch failed', d.error || 'Unknown error', 'err');
            return;
        }
        toast('COM port', `Switched to ${d.port}`, 'ok');
        // Refresh labels
        const sel = document.getElementById('comPortSelect');
        if (sel) {
            [...sel.options].forEach(o => {
                o.textContent = o.value + (o.value === d.port ? ' (ACTIVE)' : '');
            });
        }
    } catch (e) {
        toast('Port switch failed', e.message, 'err');
    }
}

function handleResetInfo(reset) {
    const banner = document.getElementById('resetBanner');
    if (!banner) return;
    if (reset && reset.detected) {
        banner.hidden = false;
        const t = document.getElementById('resetBannerText');
        if (t) {
            t.textContent = `Boot counter restarted (${reset.from_ts}→${reset.to_ts} ms). Session ${reset.session_id || ''}`;
        }
        const at = reset.at || Date.now() / 1000;
        if (at !== lastResetToastAt) {
            lastResetToastAt = at;
            toast('CanSat reset', 'New session started after reboot', 'warn', 5000);
        }
    }
}

async function ackReset() {
    try { await fetch('/api/reset/ack', { method: 'POST' }); } catch (e) { /* */ }
    const banner = document.getElementById('resetBanner');
    if (banner) banner.hidden = true;
}

/* ════════════════ SSE LIVE STREAM ════════════════ */
function startLiveTransport() {
    if (typeof EventSource === 'undefined') {
        usePollingFallback = true;
        startPolling(500);
        return;
    }
    try {
        if (sseSource) {
            try { sseSource.close(); } catch (e) { /* */ }
        }
        sseSource = new EventSource('/stream');
        sseFailCount = 0;
        sseSource.onopen = () => {
            sseFailCount = 0;
            usePollingFallback = false;
            setLinkHealthy(true);
        };
        sseSource.onmessage = (ev) => {
            try {
                const msg = JSON.parse(ev.data);
                sseFailCount = 0;
                setLinkHealthy(true);
                handleSsePayload(msg);
            } catch (e) { /* bad frame */ }
        };
        sseSource.onerror = () => {
            sseFailCount += 1;
            // EventSource auto-retries; only fall back after repeated failures
            if (sseFailCount >= 5 && !usePollingFallback) {
                usePollingFallback = true;
                try { sseSource.close(); } catch (e) { /* */ }
                sseSource = null;
                toast('Live stream', 'Falling back to polling', 'warn');
                startPolling(500);
                setLinkHealthy(false, 'SSE offline');
            }
        };
        fetchTelemetry();
        // Light full-sync while SSE pushes latest packet UI
        startPolling(5000);
    } catch (e) {
        usePollingFallback = true;
        startPolling(500);
    }
}

function startPolling(intervalMs = 500) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => {
        if (!simRunning) fetchTelemetry();
    }, intervalMs);
}

function applyMissionSummary(s) {
    if (!s) return;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('sumMaxAlt', `${s.max_altitude_m ?? 0} m`);
    set('sumMaxSpd', `${s.max_speed_ms ?? 0} m/s`);
    set('sumDuration', formatDuration(s.duration_s || 0));
    set('sumPktSuccess', `${s.packet_success_pct ?? 100}%`);
    set('sumPhase', s.phase || '—');
    set('sumPktLoss', `${s.packet_loss_pct ?? 0}%`);
    lossHistory.push(Number(s.packet_loss_pct) || 0);
    if (lossHistory.length > 40) lossHistory.shift();
    drawLossSpark();
    const pktLossEl = document.getElementById('packetLoss');
    if (pktLossEl) pktLossEl.textContent = `${s.packet_loss_pct ?? 0}%`;
}

function handleSsePayload(msg) {
    if (!msg || msg.type !== 'telemetry') return;
    document.getElementById('dotInd').classList.toggle('live', !!msg.serial_connected);
    applyLinkStatus(msg);
    if (msg.packets_received != null) document.getElementById('pkts').textContent = msg.packets_received;
    if (msg.phase) {
        const fsm = document.getElementById('fsmState');
        const fsm2 = document.getElementById('fsmState2');
        if (fsm) fsm.textContent = msg.phase;
        if (fsm2) fsm2.textContent = msg.phase;
        const sumPh = document.getElementById('sumPhase');
        if (sumPh) sumPh.textContent = msg.phase;
    }
    handleResetInfo(msg.reset);
    if (msg.summary) applyMissionSummary(msg.summary);
    if (msg.latest && Object.keys(msg.latest).length && !simRunning) {
        updateTelemetryUI(msg.latest);
        const pt = msg.latest;
        updateMap(pt.lat, pt.lon, pt.gps_alt || pt.alt || 0, pt.speed || 0, pt.heading || 0, pt.satellites || null, pt.hdop || null);
    }
}

/* ════════════════ OPS MODE + KEYBOARD ════════════════ */
function toggleOpsMode(force) {
    opsMode = typeof force === 'boolean' ? force : !opsMode;
    document.body.classList.toggle('ops-mode', opsMode);
    const btn = document.getElementById('btnOpsMode');
    if (btn) btn.textContent = opsMode ? 'Exit Ops' : 'Ops';
    // Prefer dashboard + enlarge map optionally
    if (opsMode) {
        const dashTab = document.querySelector('.tab[data-t="dashboard"]');
        if (dashTab) dashTab.click();
        toast('Ops mode', 'Fullscreen mission view — press Esc or F to exit', 'ok');
        try { document.documentElement.requestFullscreen?.(); } catch (e) { /* */ }
    } else {
        try { if (document.fullscreenElement) document.exitFullscreen?.(); } catch (e) { /* */ }
    }
    setTimeout(() => {
        map && map.invalidateSize();
        resizeCharts();
    }, 200);
}

function activateTabByIndex(idx) {
    const id = TAB_ORDER[idx];
    if (!id) return;
    const tab = document.querySelector(`.tab[data-t="${id}"]`);
    if (tab) tab.click();
}

function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        const tag = (e.target && e.target.tagName) || '';
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        if (e.key >= '1' && e.key <= '8') {
            activateTabByIndex(parseInt(e.key, 10) - 1);
            e.preventDefault();
        } else if (e.key === 't' || e.key === 'T') {
            document.getElementById('btnTest')?.click();
            e.preventDefault();
        } else if (e.key === 's' || e.key === 'S') {
            document.getElementById('btnSim')?.click();
            e.preventDefault();
        } else if (e.key === 'f' || e.key === 'F') {
            toggleOpsMode();
            e.preventDefault();
        } else if (e.key === 'd' || e.key === 'D') {
            toggleDarkMode();
            e.preventDefault();
        } else if (e.key === 'Escape' && opsMode) {
            toggleOpsMode(false);
            e.preventDefault();
        }
    });
}

function wireUpgradeControls() {
    document.getElementById('btnOpsMode')?.addEventListener('click', () => toggleOpsMode());
    document.getElementById('btnExportCsv')?.addEventListener('click', exportCsv);
    document.getElementById('btnExportPdf')?.addEventListener('click', exportPdfReport);
    document.getElementById('btnAckReset')?.addEventListener('click', ackReset);
    document.getElementById('comPortSelect')?.addEventListener('change', (e) => {
        if (e.target.value) switchComPort(e.target.value);
    });
    document.getElementById('sessionSelect')?.addEventListener('change', () => {
        toast('Session', `Selected ${document.getElementById('sessionSelect').value || 'live'}`, 'ok');
    });
    setupKeyboardShortcuts();
}

/* ════════════════ BOOT ════════════════ */
window.addEventListener('load', async () => {
    if (darkModeEnabled) {
        document.body.classList.add('dark');
        document.getElementById('btnDarkMode').textContent = 'Light';
    } else {
        document.getElementById('btnDarkMode').textContent = 'Dark';
    }
    try { initCharts(); applyChartTheme(); } catch (e) { console.error('Chart init failed', e); }
    try { initMap(); } catch (e) { console.warn('Map init failed', e); }
    setTimeout(resizeCharts, 120);

    try {
        const pr = await fetch('/ports');
        const pd = await pr.json();
        const portSelect = document.getElementById('comPortSelect');
        if (pd.available_ports && pd.available_ports.length > 0) {
            pd.available_ports.forEach(port => {
                const opt = document.createElement('option');
                opt.value = port;
                opt.textContent = port + (port === pd.current_port ? ' (ACTIVE)' : '');
                if (port === pd.current_port) opt.selected = true;
                portSelect.appendChild(opt);
            });
        }
    } catch (e) { console.log('Could not load COM ports'); }

    document.getElementById('btnAi').addEventListener('click', runAi);
    document.getElementById('btnDarkMode').addEventListener('click', toggleDarkMode);
    document.getElementById('btnTest').addEventListener('click', async () => {
        try {
            const r = await fetch('/test-data', { method: 'POST', headers: gsHeaders() });
            let d = {};
            try { d = await r.json(); } catch (e) { /* */ }
            if (!r.ok) {
                toast('Inject failed', d.error || d.message || `HTTP ${r.status}`, 'err');
                return;
            }
            resetChartBuffers();
            chartGpsCtx = null;
            toast('Test data', `${d.packets_added || 0} sample packets loaded`, 'ok');
            await fetchTelemetry();
            loadPhaseMarkers();
            refreshMissionSummary();
        } catch (e) {
            toast('Server offline', 'Start app.py and open the dashboard from that URL', 'err');
        }
    });
    document.getElementById('btnSim').addEventListener('click', toggleSimFlight);

    document.getElementById('btnArm').addEventListener('click', async () => {
        if (!systemArmed) {
            const result = await sendCommand('ARM');
            if (result) {
                systemArmed = true;
                updateCommandUI();
                playAudio('success');
                toast('Uplink', 'ARM acknowledged', 'ok');
            } else toast('Uplink', 'ARM failed', 'err');
        }
    });
    document.getElementById('btnDeploy').addEventListener('click', async () => {
        if (systemArmed && confirm('Deploy parachute? This action cannot be undone.')) {
            const result = await sendCommand('DEPLOY');
            if (result) {
                systemArmed = false;
                updateCommandUI();
                playAudio('ejection');
                toast('Uplink', 'DEPLOY sent', 'warn');
            }
        }
    });
    document.getElementById('btnAbort').addEventListener('click', async () => {
        if (systemArmed && confirm('Abort mission? This will trigger emergency procedures.')) {
            const result = await sendCommand('ABORT');
            if (result) {
                systemArmed = false;
                updateCommandUI();
                playAudio('error');
                toast('Uplink', 'ABORT sent', 'err');
            }
        }
    });
    updateCommandUI();

    document.getElementById('btnRefreshCmdLog').addEventListener('click', fetchCommandLog);
    document.getElementById('btnRefreshEvents').addEventListener('click', fetchMissionEvents);
    document.getElementById('btnCreateEvent').addEventListener('click', createEvent);

    wireUpgradeControls();
    loadSessions();
    loadPhaseMarkers();
    refreshMissionSummary();
    startLiveTransport();

    fetchCommandLog();
    fetchMissionEvents();
    updateAnomaliesFromAPI();
    updateLinkMarginFromAPI();
    setInterval(updateMissionClock, 100);
    setInterval(updateAnomaliesFromAPI, 5000);
    setInterval(updateLinkMarginFromAPI, 5000);
    setInterval(refreshMissionSummary, 4000);
    setInterval(loadPhaseMarkers, 8000);
    setInterval(loadSessions, 20000);
    setTimeout(() => map && map.invalidateSize(), 500);
    toast('VIKRAM online', 'Mission control ready', 'ok');

    // ── Video feed buttons ──────────────────────────────────────────
    document.getElementById('btnVidUSB').addEventListener('click', () => {
        switchVideoSource('/video_feed');
    });
    document.getElementById('btnVidESP').addEventListener('click', () => {
        switchVideoSource('/video_feed_esp');
    });
    document.getElementById('btnVidSnap').addEventListener('click', takeVideoSnapshot);
});

/* ════════════════ VIDEO FEED ════════════════ */
let _vidRetryTimer = null;

/**
 * Called by the <img> onerror attribute when the stream breaks.
 * Shows the offline overlay and schedules a retry in 5 s.
 */
function handleVideoError() {
    const overlay = document.getElementById('vidOverlay');
    const badge   = document.getElementById('vidBadge');
    if (overlay) { overlay.classList.remove('hidden'); }
    if (badge)   { badge.textContent = 'OFFLINE'; badge.classList.add('badge-offline'); }

    clearTimeout(_vidRetryTimer);
    _vidRetryTimer = setTimeout(() => {
        const img = document.getElementById('liveFeed');
        if (!img) return;
        const base = img.src.split('?')[0];
        img.src = base + '?t=' + Date.now();
    }, 5000);
}

/**
 * Called by the <img> onload attribute when a frame arrives successfully.
 * Hides the offline overlay.
 */
function handleVideoLoad() {
    const overlay = document.getElementById('vidOverlay');
    const badge   = document.getElementById('vidBadge');
    if (overlay) { overlay.classList.add('hidden'); }
    if (badge)   { badge.textContent = 'LIVE'; badge.classList.remove('badge-offline'); }
    clearTimeout(_vidRetryTimer);
}

/**
 * Switch between USB-cam (/video_feed) and ESP32-CAM (/video_feed_esp).
 * @param {string} url - the Flask route to stream from
 */
function switchVideoSource(url) {
    clearTimeout(_vidRetryTimer);
    const img = document.getElementById('liveFeed');
    if (!img) return;
    img.src = url + '?t=' + Date.now();
    toast('Video', 'Switching to ' + url, 'ok');
}

/**
 * Take a snapshot of the current MJPEG frame by drawing the <img> onto a
 * canvas, then triggering a download.
 */
function takeVideoSnapshot() {
    const img = document.getElementById('liveFeed');
    if (!img || !img.complete || img.naturalWidth === 0) {
        toast('Video', 'No live frame to capture', 'warn');
        return;
    }
    const canvas = document.createElement('canvas');
    canvas.width  = img.naturalWidth  || img.width;
    canvas.height = img.naturalHeight || img.height;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const ts   = new Date().toISOString().replace(/[:.]/g, '-');
    const link = document.createElement('a');
    link.download = 'cansat-snap-' + ts + '.jpg';
    link.href = canvas.toDataURL('image/jpeg', 0.92);
    link.click();
    toast('Video', 'Snapshot saved', 'ok');
}


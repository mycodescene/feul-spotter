// Fuel Route Optimizer Interactive Application

let map;
let routeLayer;
let markersLayer;
let markersMap = {};

document.addEventListener("DOMContentLoaded", () => {
  initMap();
  setupEventListeners();
  // Auto-load Denver to Dallas on launch
  planRoute("Denver, CO", "Dallas, TX");
});

function initMap() {
  // Center roughly in central USA
  map = L.map("map", {
    zoomControl: false,
  }).setView([37.0902, -95.7129], 5);

  L.control.zoom({ position: "bottomright" }).addTo(map);

  // Free Dark Mode Basemap (No Key, No Signup, Zero Watermark)
  const darkBasemap = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    {
      attribution: '&copy; <a href="https://www.esri.com/">Esri</a>, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 16,
    }
  );

  // Standard OpenStreetMap (No Key, No Signup)
  const osmBasemap = L.tileLayer(
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }
  );

  darkBasemap.addTo(map);

  // Layer control for user convenience
  const baseMaps = {
    "🌙 Dark Mode": darkBasemap,
    "🗺️ OpenStreetMap": osmBasemap,
  };
  L.control.layers(baseMaps, null, { position: "topright" }).addTo(map);

  markersLayer = L.layerGroup().addTo(map);
}

function setupEventListeners() {
  const form = document.getElementById("routeForm");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const start = document.getElementById("startLocation").value.trim();
    const finish = document.getElementById("finishLocation").value.trim();
    if (start && finish) {
      planRoute(start, finish);
    }
  });

  // Preset chips
  document.querySelectorAll(".preset-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const start = chip.getAttribute("data-start");
      const finish = chip.getAttribute("data-finish");
      document.getElementById("startLocation").value = start;
      document.getElementById("finishLocation").value = finish;
      planRoute(start, finish);
    });
  });

  window.addEventListener("resize", () => {
    map.invalidateSize();
    if (routeLayer) {
      map.fitBounds(routeLayer.getBounds(), { padding: [50, 50] });
    }
  });
}

function setBtnLoading(loading) {
  const btn = document.getElementById("planBtn");
  const btnText = document.getElementById("btnText");
  const spinner = document.getElementById("btnSpinner");
  if (loading) {
    btn.disabled = true;
    btnText.textContent = "Optimizing Route...";
    spinner.style.display = "block";
  } else {
    btn.disabled = false;
    btnText.textContent = "Optimize Fuel Stops";
    spinner.style.display = "none";
  }
}

function showError(msg) {
  const alertBox = document.getElementById("errorAlert");
  alertBox.textContent = msg;
  alertBox.style.display = "block";
}

function clearError() {
  const alertBox = document.getElementById("errorAlert");
  alertBox.style.display = "none";
  alertBox.textContent = "";
}

async function planRoute(startLocation, finishLocation) {
  clearError();
  setBtnLoading(true);

  try {
    const response = await fetch("/api/route/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        start_location: startLocation,
        finish_location: finishLocation,
      }),
    });

    const data = await response.json();

    if (!response.ok) {
      const errorMsg = data.error || (data.start_location && data.start_location[0]) || (data.finish_location && data.finish_location[0]) || "Failed to calculate route.";
      showError(errorMsg);
      setBtnLoading(false);
      return;
    }

    renderRoute(data);
  } catch (err) {
    showError("Network or server connection failed: " + err.message);
  } finally {
    setBtnLoading(false);
  }
}

function renderRoute(data) {
  // Update KPI Counters
  animateCounter("totalDistance", data.total_distance_miles);
  animateCounter("totalCost", data.total_fuel_cost_usd, "$");
  animateCounter("gallonsUsed", data.gallons_used);
  animateCounter("stopsCount", data.fuel_stops.length, "", 0);

  // Clear previous map layers
  if (routeLayer) {
    map.removeLayer(routeLayer);
  }
  markersLayer.clearLayers();
  markersMap = {};

  // Render GeoJSON Route
  if (data.route_geometry && data.route_geometry.coordinates && data.route_geometry.coordinates.length > 0) {
    // Leaflet GeoJSON expects coordinates in [lon, lat] format
    routeLayer = L.geoJSON(data.route_geometry, {
      style: {
        color: "#6366f1",
        weight: 5,
        opacity: 0.85,
        lineCap: "round",
        lineJoin: "round",
      },
    }).addTo(map);

    // Add Start and Destination markers
    const coords = data.route_geometry.coordinates;
    const startCoord = [coords[0][1], coords[0][0]];
    const finishCoord = [coords[coords.length - 1][1], coords[coords.length - 1][0]];

    L.marker(startCoord, {
      icon: createPinIcon("#10b981", "S"),
    }).bindPopup(`<strong>Origin:</strong> ${data.start_location}`).addTo(markersLayer);

    L.marker(finishCoord, {
      icon: createPinIcon("#f43f5e", "F"),
    }).bindPopup(`<strong>Destination:</strong> ${data.finish_location}`).addTo(markersLayer);

    // Fit map bounds on the route geometry
    map.invalidateSize();
    map.fitBounds(routeLayer.getBounds(), { padding: [50, 50] });
  }

  // Render Fuel Stop Markers
  data.fuel_stops.forEach((stop) => {
    const stopCoord = [stop.latitude, stop.longitude];
    const marker = L.marker(stopCoord, {
      icon: createFuelIcon(stop.stop_number),
    }).bindPopup(`
      <div style="font-family: var(--font-body); min-width: 180px;">
        <div style="font-weight: 700; color: #fff; font-size: 1rem; margin-bottom: 2px;">${stop.truckstop_name}</div>
        <div style="color: #94a3b8; font-size: 0.82rem; margin-bottom: 8px;">${stop.address}, ${stop.city}, ${stop.state}</div>
        <div style="display: flex; justify-content: space-between; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 6px;">
          <span style="color: #cbd5e1;">Price:</span>
          <strong style="color: #34d399;">$${stop.retail_price_per_gallon.toFixed(3)}/gal</strong>
        </div>
        <div style="display: flex; justify-content: space-between; margin-top: 4px;">
          <span style="color: #cbd5e1;">Fuel:</span>
          <strong style="color: #fff;">${stop.gallons_purchased.toFixed(2)} gal</strong>
        </div>
        <div style="display: flex; justify-content: space-between; margin-top: 4px;">
          <span style="color: #cbd5e1;">Cost:</span>
          <strong style="color: #f59e0b;">$${stop.cost_usd.toFixed(2)}</strong>
        </div>
      </div>
    `).addTo(markersLayer);

    markersMap[stop.stop_number] = marker;
  });

  // Render Itinerary Timeline
  renderTimeline(data.fuel_stops);

  // Sync map size and re-fit bounds after DOM timeline cards render
  requestAnimationFrame(() => {
    map.invalidateSize();
    if (routeLayer) {
      map.fitBounds(routeLayer.getBounds(), { padding: [50, 50] });
    }
  });
}

function renderTimeline(fuelStops) {
  const container = document.getElementById("timelineContainer");
  container.innerHTML = "";

  if (!fuelStops || fuelStops.length === 0) {
    container.innerHTML = `
      <div style="padding: 1.5rem; text-align: center; color: var(--text-muted); background: rgba(15,23,42,0.4); border-radius: var(--radius-lg);">
        <span style="font-size: 1.5rem;">🎉</span>
        <div style="margin-top: 0.5rem; font-weight: 600;">No refueling stops needed!</div>
        <div style="font-size: 0.85rem; color: var(--text-dim); margin-top: 0.25rem;">The trip is within initial tank capacity (500 miles).</div>
      </div>
    `;
    return;
  }

  fuelStops.forEach((stop) => {
    const card = document.createElement("div");
    card.className = "timeline-card";
    card.innerHTML = `
      <div class="timeline-header">
        <span class="stop-tag">Stop #${stop.stop_number}</span>
        <span class="stop-price-badge">$${stop.retail_price_per_gallon.toFixed(3)} / gal</span>
      </div>
      <div class="stop-name">${stop.truckstop_name}</div>
      <div class="stop-address">${stop.address}, ${stop.city}, ${stop.state}</div>
      <div class="stop-stats">
        <div class="stop-stat-item">Distance: <strong>${stop.distance_from_start_miles.toFixed(1)} mi</strong></div>
        <div class="stop-stat-item">Fuel: <strong>${stop.gallons_purchased.toFixed(2)} gal</strong></div>
        <div class="stop-stat-item">Cost: <strong>$${stop.cost_usd.toFixed(2)}</strong></div>
      </div>
    `;

    // Hover or click to zoom into map marker
    card.addEventListener("click", () => {
      const marker = markersMap[stop.stop_number];
      if (marker) {
        map.setView(marker.getLatLng(), 11, { animate: true });
        marker.openPopup();
      }
    });

    container.appendChild(card);
  });
}

// Icon Generators
function createPinIcon(bgColor, label) {
  return L.divIcon({
    className: "custom-pin",
    html: `
      <div style="
        background: ${bgColor};
        color: #fff;
        width: 32px;
        height: 32px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 800;
        font-family: var(--font-heading);
        border: 2px solid #fff;
        box-shadow: 0 4px 12px rgba(0,0,0,0.5);
      ">${label}</div>
    `,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
  });
}

function createFuelIcon(stopNumber) {
  return L.divIcon({
    className: "custom-fuel-pin",
    html: `
      <div style="
        background: #f59e0b;
        color: #0f172a;
        width: 34px;
        height: 34px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 800;
        font-family: var(--font-mono);
        border: 2px solid #fff;
        box-shadow: 0 0 15px rgba(245, 158, 11, 0.6);
        animation: pulseGlow 3s infinite;
      ">#${stopNumber}</div>
    `,
    iconSize: [34, 34],
    iconAnchor: [17, 17],
  });
}

// Number animation helper
function animateCounter(id, targetValue, prefix = "", decimals = null) {
  const el = document.getElementById(id);
  if (!el) return;
  const start = 0;
  const duration = 800;
  const startTime = performance.now();
  const fracDigits = decimals !== null ? decimals : (prefix ? 2 : 1);

  function update(currentTime) {
    const elapsed = currentTime - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const easeProgress = 1 - Math.pow(1 - progress, 3); // Ease out cubic
    const current = start + (targetValue - start) * easeProgress;
    el.textContent = `${prefix}${current.toLocaleString(undefined, {
      minimumFractionDigits: fracDigits,
      maximumFractionDigits: fracDigits,
    })}`;

    if (progress < 1) {
      requestAnimationFrame(update);
    }
  }

  requestAnimationFrame(update);
}

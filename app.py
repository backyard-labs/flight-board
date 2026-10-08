import json
import math
import os
import threading
import time
import urllib.parse
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opensky_auth import open_authenticated

def load_location():
    name = os.environ.get("FLIGHT_BOARD_LOCATION", "Seattle, WA").strip()
    if not name or len(name) > 100:
        raise ValueError("FLIGHT_BOARD_LOCATION must be 1 to 100 characters")

    def number(variable, default):
        raw = os.environ.get(variable, default)
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{variable} must be a number") from exc
        if not math.isfinite(value):
            raise ValueError(f"{variable} must be finite")
        return value

    lat = number("FLIGHT_BOARD_LATITUDE", "47.6062")
    lon = number("FLIGHT_BOARD_LONGITUDE", "-122.3321")
    radius = number("FLIGHT_BOARD_RADIUS_MILES", "25")
    if not -90 <= lat <= 90:
        raise ValueError("FLIGHT_BOARD_LATITUDE must be between -90 and 90")
    if not -180 <= lon <= 180:
        raise ValueError("FLIGHT_BOARD_LONGITUDE must be between -180 and 180")
    if not 0 < radius <= 1000:
        raise ValueError("FLIGHT_BOARD_RADIUS_MILES must be greater than 0 and at most 1000")
    return {"name": name, "latitude": lat, "longitude": lon, "radiusMiles": radius}


LOCATION = load_location()
CENTER_LAT = LOCATION["latitude"]
CENTER_LON = LOCATION["longitude"]
RADIUS_MI = LOCATION["radiusMiles"]
REFRESH_SECONDS = 30

EARTH_RADIUS_MILES = 3958.7613


def opensky_urls(location=LOCATION):
    """Return spherical bounding boxes that contain the configured circle."""
    lat, lon, radius = location["latitude"], location["longitude"], location["radiusMiles"]
    angular_radius = min(math.pi, radius / EARTH_RADIUS_MILES)
    center_lat = math.radians(lat)
    lamin = max(-math.pi / 2, center_lat - angular_radius)
    lamax = min(math.pi / 2, center_lat + angular_radius)

    # A spherical cap contains a pole when its angular radius reaches it.
    if angular_radius >= math.pi / 2 - abs(center_lat):
        lon_delta = math.pi
    else:
        ratio = min(1.0, max(-1.0, math.sin(angular_radius) / math.cos(center_lat)))
        lon_delta = math.asin(ratio)

    lamin, lamax = math.degrees(lamin), math.degrees(lamax)
    west, east = lon - math.degrees(lon_delta), lon + math.degrees(lon_delta)
    if lon_delta >= math.pi:
        boxes = [(-180.0, 180.0)]
    elif west < -180:
        boxes = [(-180.0, east), (west + 360.0, 180.0)]
    elif east > 180:
        boxes = [(-180.0, east - 360.0), (west, 180.0)]
    else:
        boxes = [(west, east)]

    urls = []
    for lomin, lomax in boxes:
        params = {
            "lamin": f"{lamin:.10f}",
            "lomin": f"{lomin:.10f}",
            "lamax": f"{lamax:.10f}",
            "lomax": f"{lomax:.10f}",
        }
        urls.append("https://opensky-network.org/api/states/all?" + urllib.parse.urlencode(params))
    return urls


URLS = opensky_urls()

lock = threading.Lock()
cache = {
    "flights": [],
    "updated": None,
    "error": None,
    "remaining": None,
    "location": LOCATION,
}


def distance_miles(lat, lon):
    lat1, lat2 = map(math.radians, (CENTER_LAT, lat))
    dlat = lat2 - lat1
    dlon = math.radians(lon - CENTER_LON)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(min(1, max(0, a))))


def fetch_loop():
    while True:
        delay = REFRESH_SECONDS

        try:
            flights = []
            observed_at = None
            remaining = None

            for url in URLS:
                with open_authenticated(url, timeout=25) as response:
                    data = json.load(response)
                    remaining = response.headers.get("X-Rate-Limit-Remaining")

                response_time = data.get("time")
                if not isinstance(response_time, (int, float)):
                    raise ValueError("Missing observation timestamp")
                observed_at = max(observed_at or response_time, response_time)

                for state in data.get("states") or []:
                    if len(state) < 14:
                        continue

                    # OpenSky: longitude=5, latitude=6, on_ground=8.
                    lon, lat = state[5], state[6]
                    position_time = state[3]

                    if lon is None or lat is None or state[8]:
                        continue

                    if position_time is None or observed_at - position_time > 120:
                        continue

                    distance = distance_miles(lat, lon)
                    if distance > RADIUS_MI:
                        continue

                    altitude = state[7]  # Barometric altitude in meters.
                    speed = state[9]     # Ground speed in meters per second.

                    flights.append({
                        "id": state[0],
                        "callsign": (state[1] or "").strip() or state[0].upper(),
                        "altitude": (
                            f"{round(altitude * 3.28084):,} ft"
                            if altitude is not None else "Unavailable"
                        ),
                        "speed": (
                            f"{round(speed * 1.94384)} kt"
                            if speed is not None else "Unavailable"
                        ),
                        "distance": f"{distance:.1f} mi",
                        "miles": distance,
                    })

            unique = {flight["id"]: flight for flight in flights}
            flights = list(unique.values())

            flights.sort(key=lambda flight: flight["miles"])

            with lock:
                cache.update(
                    flights=flights,
                    updated=observed_at,
                    error=None,
                    remaining=remaining,
                )

            print(
                f"Updated: {len(flights)} aircraft in range "
                f"| Credits remaining: {remaining}",
                flush=True,
            )

        except urllib.error.HTTPError as exc:
            delay = 300
            message = f"OpenSky returned HTTP {exc.code}"

            if exc.code == 429:
                retry = exc.headers.get("X-Rate-Limit-Retry-After-Seconds")
                try:
                    delay = max(300, int(retry) + 5)
                except (TypeError, ValueError):
                    delay = 3600

                message += "; rate limit reached"
            elif exc.code in (401, 403):
                message += "; authentication or access failed"

            message += f"; retrying in {delay} seconds"

            with lock:
                cache["error"] = message

            print(message, flush=True)

        except Exception as exc:
            delay = 300

            with lock:
                cache["error"] = (
                    "Live-data request failed; retrying in 5 minutes"
                )

            # Log only the exception type, avoiding sensitive response content.
            print(
                f"Fetch failed: {type(exc).__name__}; retrying in 5 minutes",
                flush=True,
            )

        time.sleep(delay)


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local Flight Board</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: #080e17;
  color: #e8f0fa;
  font-family: system-ui, sans-serif;
}
main { width: min(900px, 100%); }
header, footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
h1 { font-size: 21px; }
#status {
  padding: 8px 12px;
  border: 1px solid #42566b;
  border-radius: 6px;
  font-size: 12px;
  color: #ffd28a;
}
.board {
  margin: 20px 0;
  padding: clamp(24px, 5vw, 48px);
  border: 1px solid #344252;
  border-radius: 14px;
  background-color: #101923;
  background: #101923;
}
.label {
  color: #91a6bb;
  font-size: 12px;
  letter-spacing: 2px;
}
#callsign {
  margin: 20px 0;
  color: #83efc1;
  font: bold clamp(32px, 8vw, 76px) monospace;
  overflow-wrap: anywhere;
}
#detail, footer, .note {
  color: #91a6bb;
  font-size: 13px;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
  margin-top: 30px;
  padding-top: 24px;
  border-top: 1px solid #344252;
}
.value {
  margin-top: 12px;
  font: bold 25px monospace;
}
button {
  padding: 10px 16px;
  border: 1px solid #42566b;
  border-radius: 6px;
  background: #172535;
  color: #e8f0fa;
  cursor: pointer;
}
.note { line-height: 1.7; }
#error { color: #ffd28a; }
@media(max-width: 520px) {
  .metrics { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<main>
<header>
  <h1>Local Flight Board</h1>
  <span id="status">CONNECTING</span>
</header>

<section class="board">
  <div class="label">
    AIRCRAFT IN RANGE · <span id="location-name"></span>
  </div>
  <div id="callsign">Waiting…</div>
  <div id="detail">Connecting to OpenSky</div>

  <div class="metrics">
    <div>
      <div class="label">BAROMETRIC ALTITUDE</div>
      <div class="value" id="altitude">—</div>
    </div>
    <div>
      <div class="label">GROUND SPEED</div>
      <div class="value" id="speed">—</div>
    </div>
    <div>
      <div class="label">DISTANCE FROM CENTER</div>
      <div class="value" id="distance">—</div>
    </div>
  </div>
</section>

<footer>
  <span id="updated">Waiting for first observation</span>
  <button id="next" type="button">Next aircraft</button>
</footer>

<p id="error" role="status"></p>

<p class="note">
  Source: OpenSky Network · Data fetched every 30 seconds.<br>
  Aircraft cycle every 10 seconds. Positions are from the last observation;
  distance is horizontal distance from <span class="location-name"></span>.<br>
  Routes, aircraft models, and arrival times are not included in this feed.
</p>
</main>

<script>
let data = {flights: [], updated: null, error: null};
let index = 0;
let disconnected = false;
let boardLocation = {name: "Seattle, WA", radiusMiles: 25};
const el = id => document.getElementById(id);

function render() {
  const age = data.updated
    ? Math.max(0, Math.floor(Date.now() / 1000 - data.updated))
    : null;

  const stale = age !== null && age > 90;

  el("status").textContent = disconnected
    ? "BOARD CONNECTION LOST"
    : data.error
      ? "DATA SOURCE ERROR"
      : stale
        ? "STALE DATA"
        : data.updated
          ? "LIVE FEED · 30-SEC UPDATES"
          : "CONNECTING";

  el("error").textContent = disconnected
    ? "Cannot reach the local server. Retrying…"
    : data.error || "";

  el("updated").textContent = data.updated
    ? `Observed ${new Date(data.updated * 1000).toLocaleTimeString()} · ${age}s ago`
    : "Waiting for first observation";

  const flights = data.flights || [];
  boardLocation = data.location || boardLocation;
  el("location-name").textContent = `${boardLocation.name} · ${boardLocation.radiusMiles} MILES`;
  document.querySelectorAll(".location-name").forEach(node => {
    node.textContent = boardLocation.name;
  });

  if (!flights.length) {
    el("callsign").textContent = data.updated ? "No aircraft" : "Waiting…";
    el("detail").textContent = data.updated
      ? "No aircraft with recent positions in range at the last update"
      : "Waiting for live aircraft data";

    for (const key of ["altitude", "speed", "distance"]) {
      el(key).textContent = "—";
    }
    return;
  }

  index = index % flights.length;
  const flight = flights[index];

  for (const key of ["callsign", "altitude", "speed", "distance"]) {
    el(key).textContent = flight[key];
  }

  el("detail").textContent =
    `Aircraft ${index + 1} of ${flights.length} · ICAO ${flight.id.toUpperCase()}`
    + (stale || data.error || disconnected
      ? " · Last available observation"
      : "");
}

async function refresh() {
  try {
    const response = await fetch("/api/flights", {
      cache: "no-store",
      signal: AbortSignal.timeout(8000)
    });

    if (!response.ok) {
      throw new Error("Server unavailable");
    }

    const nextData = await response.json();

    // Keep the selected aircraft when possible as the list updates.
    const currentId = data.flights?.[index]?.id;
    data = nextData;

    if (currentId) {
      const newIndex = data.flights.findIndex(
        flight => flight.id === currentId
      );
      index = newIndex >= 0 ? newIndex : 0;
    }

    disconnected = false;
  } catch {
    disconnected = true;
  }

  render();
}

function next() {
  index += 1;
  render();
}

el("next").addEventListener("click", next);
setInterval(next, 10000);
setInterval(refresh, 15000);
setInterval(render, 1000);
refresh();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/flights":
            with lock:
                content = json.dumps(cache).encode("utf-8")
            content_type = "application/json"

        elif self.path == "/health":
            content = b"ok"
            content_type = "text/plain"

        elif self.path in ("/", "/index.html"):
            content = PAGE.encode("utf-8")
            content_type = "text/html; charset=utf-8"

        else:
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    threading.Thread(target=fetch_loop, daemon=True).start()
    print("Flight board listening on port 8000", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()

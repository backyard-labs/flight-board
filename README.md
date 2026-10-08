# Local Flight Board

Local Flight Board is an AI-assisted hobby project that displays nearby aircraft in a browser on a locally running PC. It is an experiment for personal use, not a production service.

I did not design the software or write its code. ChatGPT generated the initial application's design and code, and Codex assisted with preparing it for sharing. I provided the goals, ran it locally, tested its behavior, and gave feedback on appearance and usability.

The idea started after I saw a physical LED flight-tracking display and wondered whether a browser-based version could run on my existing PC.

## Inspiration and attribution

The physical display that sparked the idea is available from [TheFlightWall](https://theflightwall.com/). People interested in building their own can explore [TheFlightWall open-source DIY repository](https://github.com/AxisNimble/TheFlightWall_OSS).

This is a separate, AI-generated browser implementation. No code from TheFlightWall repository was incorporated into this project. Local Flight Board is not affiliated with or endorsed by TheFlightWall. This project does not use their code, logos, images, or marketing text.

## Features

- Authenticated OpenSky Network data with automatic token renewal.
- A shared backend feed that polls every 30 seconds regardless of how many browser tabs are open.
- Dashboard and clean LED views, with four aircraft per page, manual paging, and optional automatic paging.
- Callsigns, barometric altitude, ground speed, distance from the configured center, last observation time, and remaining-credit display when OpenSky supplies it.
- Configurable location name, coordinates, and radius.
- Localhost-only web access on port 18473, with a read-only container filesystem, dropped Linux capabilities, no-new-privileges, and an unprivileged container user.

## Limitations

The board shows current or recently observed state-vector data. It does not provide flight routes, arrival times, aircraft models, airline names, a map, or aircraft pinning. Positions and measurements may be unavailable or stale. The small UI refreshes its display separately from backend data polling.

OpenSky credit use depends on polling frequency and bounding-box size. A 30-second interval is approximately 2,880 requests per day for the usual one-box area. The Seattle defaults use one request per polling cycle. An area crossing the antimeridian uses two requests; a search circle that includes a pole uses one box spanning all longitudes to preserve coverage. Those cases use more queried area and may cost more credits. The one-credit-per-request assumption applies to this project's small default area, not every possible configured radius. Provider quotas and credit rules may change; check OpenSky's current terms and account information.

## Requirements

- **Windows or macOS:** Docker Desktop installed and running. On Windows, configure Docker Desktop to use Linux containers.
- **Linux:** Docker Engine with the Docker Compose plugin, installed and running.
- No separate Python installation is needed for Docker deployment; the container uses `python:3.12-slim`.
- An OpenSky account and API client credentials.
- A PC that can reach the OpenSky authentication and API endpoints.

## Configure OpenSky credentials

Create an API client through your OpenSky account and put its client ID and client secret in a local `credentials.json` file at the project root:

```json
{
  "clientId": "YOUR_CLIENT_ID",
  "clientSecret": "YOUR_CLIENT_SECRET"
}
```

The optional `credentials.example.json` is placeholder-only. From the project directory, copy it to `credentials.json` and replace both placeholders with your own values. In PowerShell, use `Copy-Item credentials.example.json credentials.json`; on Linux or macOS, use `cp credentials.example.json credentials.json`. Compose mounts the local file read-only at `/run/secrets/opensky.json`. Credentials stay on your machine and must never be committed or shared. `.gitignore` excludes `credentials.json` and `.env`.

## Configure your location

The public defaults are Seattle, WA, centered at latitude `47.6062`, longitude `-122.3321`, with a radius of 25 miles. From the project directory, copy `.env.example` to `.env` and customize it if desired. In PowerShell, use `Copy-Item .env.example .env`; on Linux or macOS, use `cp .env.example .env`. Compose reads `.env`; Git ignores it.

To use the public Seattle defaults, create or edit `.env` in the project root with:

```dotenv
FLIGHT_BOARD_LOCATION=Seattle, WA
FLIGHT_BOARD_LATITUDE=47.6062
FLIGHT_BOARD_LONGITUDE=-122.3321
FLIGHT_BOARD_RADIUS_MILES=25
```

Run the commands below from the project directory. Start or recreate the service after changing location settings so Compose passes the new values into the container. The board is available at <http://localhost:18473>, bound to localhost on the host computer. The computer and Docker must remain running for updates. Closing the browser does not stop background OpenSky polling.

## Run the board

From the project directory:

```powershell
docker compose up -d
```

On the first run, Docker downloads the `python:3.12-slim` image. The project itself uses only Python's standard library.

Useful commands:

```powershell
docker compose logs -f
docker compose restart
docker compose stop flight-board
docker compose start flight-board
docker compose down
```

After changing `.env` or another environment setting, apply it with `docker compose up -d --force-recreate`; `docker compose restart` alone does not reload environment settings. `docker compose restart` is appropriate after changing mounted project files. The `unless-stopped` restart policy can restart the container when Docker starts, but it does not launch Docker Desktop after a computer restart; start Docker Desktop yourself on Windows or macOS.

## Troubleshooting

- **Container exits at startup:** confirm `credentials.json` exists and contains valid JSON with `clientId` and `clientSecret` (or `client_id` and `client_secret`).
- **Authentication or access error:** verify the API client values and OpenSky account access; inspect `docker compose logs -f`.
- **Rate limit message:** the app backs off automatically after provider errors. Wait for the retry window and review your OpenSky account's current quota.
- **No aircraft:** confirm the location values, radius, and that aircraft with recent positions are present in range.
- **Cannot open the page:** confirm Docker is running and visit <http://localhost:18473>. The service binds only to localhost.

## Data source and license

Flight data comes from [OpenSky Network](https://opensky-network.org/) through its [REST API documentation](https://openskynetwork.github.io/opensky-api/). The MIT software license covers this project's code only. It does not license OpenSky data or other third-party material; users must follow OpenSky's applicable terms and any data-use requirements.

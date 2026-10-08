# Authentication Frontend

A React and Vite frontend for account registration, login, and password recovery.

## Features

- Login and signup screens connected to the backend authentication API.
- Backend-issued access token and user details stored after successful login.
- Forgot-password, reset-password, and password-reset success screens.
- Responsive authentication layouts.

## Technology

- React 18
- Vite 5
- Nginx for the production Docker image
- Node.js 20 or newer for local development

## Prerequisites

Install the following before running the project:

- Node.js 20 or newer and npm
- Docker Desktop with Docker Compose, if using Docker

## Project Structure

```text
.
├── compose.yml             # Docker Compose service and port mapping
├── Dockerfile              # Multi-stage frontend build and Nginx image
├── nginx.conf              # Nginx static hosting configuration
├── package.json            # npm scripts and dependencies
└── src/
	├── app/                # Application entry and routing
	├── pages/              # Home and authentication pages
	├── services/           # Authentication API helpers
	└── styles/             # Global, home, and authentication styles
```

## Install Dependencies

From the project root:

```bash
npm install
```

For a clean, lockfile-based install in CI or a reproducible environment:

```bash
npm ci
```

## Run Locally in Development

Start the Vite development server:

```bash
npm run dev
```

The development server runs at:

```text
http://localhost:5173
```

The home page uses local browser state. Login, signup, and password recovery require the configured backend API.

Stop the local Vite process with `Ctrl+C` in the terminal where it is running.

## Run with Docker

The Docker image builds the frontend with Node.js, then serves the generated files with Nginx. The container listens on port `80`; Docker publishes it on host port `5173`. Run either Docker or the Vite dev server on this port, not both at once.

### Build and start

Run these commands from the project root:

```bash
docker compose up --build -d
```

Open the application at:

```text
http://localhost:5173
```

The `--build` option rebuilds the image so source or dependency changes are included. The `-d` option runs the container in the background.

### Start an existing image

If the image is already current:

```bash
docker compose up -d
```

### Check container status

```bash
docker compose ps
```

The frontend should show a port mapping similar to `0.0.0.0:5173->80/tcp`.

### View logs

```bash
docker compose logs -f frontend
```

Press `Ctrl+C` to stop following the logs. This does not stop the container.

### Stop the container

To stop the service and keep its container available for a later start:

```bash
docker compose stop
```

Start it again later with:

```bash
docker compose start
```

### Stop and remove the Compose container

To stop the service and remove the container and network:

```bash
docker compose down
```

The image is kept, so a later `docker compose up -d` can reuse it.

### Rebuild from scratch

Use this when dependencies or the Docker build cache may be stale:

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

## Available npm Scripts

| Command | Purpose |
| --- | --- |
| `npm run dev` | Start the Vite development server on port `5173` |
| `npm run build` | Create a production build in `dist/` |
| `npm run preview` | Preview the production build locally |

To preview the generated build on the same port used by development:

```bash
npm run build
npm run preview -- --port 5173
```

## Production Build

Create and inspect a local production build:

```bash
npm run build
npm run preview -- --port 5173
```

The generated files are written to `dist/`. The Dockerfile copies this directory into Nginx and uses the SPA fallback so client-side routes resolve to `index.html`.

## Troubleshooting

### Port 5173 is already in use

Stop the existing Compose service:

```bash
docker compose down
```

On Windows PowerShell, find the process using the port:

```powershell
Get-NetTCPConnection -LocalPort 5173
```

You can also run the Vite server on another temporary port:

```bash
npm run dev -- --port 5174
```

### Docker changes are not visible

Rebuild the image and recreate the service:

```bash
docker compose down
docker compose up --build -d
```
## Authentication

The login and signup pages are available at `/login` and `/signup`. `/register` redirects to `/signup` for compatibility. Login and signup call `POST /auth/login` and `POST /auth/register` using `VITE_API_BASE_URL`; successful login stores the backend-issued access token and user details in local storage, then opens `/home`.

The home screen uses only browser state for its navigation and project-creation demo; it does not call backend APIs.

Password recovery screens are available at `/forgot-password`, `/reset-password`, and `/password-reset-success`. The forgot-password and reset-password forms call their respective backend APIs.

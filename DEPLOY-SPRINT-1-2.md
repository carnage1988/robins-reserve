# Sprint 1 & 2 — Separated Dashboard Deployment

## Final development layout

- `dev.robinhub.co.uk`: static frontend on IONOS.
- `api-dev.robinhub.co.uk`: dashboard API on Marble.
- Discord bot remains on Marble.
- API and bot share the same private Docker data volume.

## Security model

- No bot, Google or Squarespace secrets are present in the frontend.
- The API only permits the configured frontend origin.
- Unsafe requests from an untrusted Origin are rejected.
- Discord OAuth and staff-role checks are performed by the API.
- Session cookies are HttpOnly, Secure and SameSite=Lax.
- The API validates its public Host header.

## 1. Prepare the development environment on Marble

Create the external network used by Nginx Proxy Manager if it does not already exist:

```bash
docker network create proxy
```

Copy the environment template:

```bash
cp deploy/marble/.env.dev.example .env.dev
nano .env.dev
```

Generate a session secret:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Start the development bot and API:

```bash
docker compose -f deploy/marble/compose.dev.yaml up -d --build
docker compose -f deploy/marble/compose.dev.yaml logs -f
```

## 2. Publish the API through Nginx Proxy Manager

Create a Proxy Host:

- Domain: `api-dev.robinhub.co.uk`
- Scheme: `http`
- Forward hostname: `robins-api-dev`
- Forward port: `8000`
- Enable Websockets
- Block common exploits
- Request an SSL certificate and force SSL

Nginx Proxy Manager must be attached to the external `proxy` Docker network.

For improved home-host security, put Cloudflare in front of NPM or replace the public port-forward with Cloudflare Tunnel. Never expose port 8000 directly.

## 3. DNS

Create `api-dev.robinhub.co.uk` pointing to the public API ingress (Cloudflare/NPM). Create `dev.robinhub.co.uk` in IONOS and assign it to the directory containing the static frontend files.

## 4. Discord OAuth

In the Discord developer application, add exactly:

```text
https://api-dev.robinhub.co.uk/auth/discord/callback
```

It must match `DISCORD_REDIRECT_URI` byte-for-byte.

## 5. Upload the frontend to IONOS

Upload the contents, not the containing folder, of:

```text
deploy/ionos/dev.robinhub.co.uk/
```

to the document root assigned to `dev.robinhub.co.uk`. Ensure hidden files are included so `.htaccess` is uploaded.

Expected files:

```text
index.html
styles.css
app.js
config.js
.htaccess
```

## 6. Test

1. Open `https://api-dev.robinhub.co.uk/health`.
2. Open `https://dev.robinhub.co.uk`.
3. Sign in with Discord.
4. Confirm the Discord avatar loads.
5. Confirm health cards populate.
6. Approve a disposable preorder and verify Discord reaction + customer DM.
7. Start a disposable League event and verify the Discord channel announcement.
8. Log out and confirm protected API calls return 401.

## Production later

The production frontend is prebuilt in `deploy/ionos/app.robinhub.co.uk`. Do not upload it until development testing is signed off. Production must use a separate `.env`, session secret, OAuth callback and preferably separate bot/application identity.

# Upgrade to v1.3.1

1. Back up the current development directory and persistent data.
2. Copy this release over the project while preserving `.env`, `secrets/`, Docker volumes and `.git/`.
3. Run `python -m compileall -q .`.
4. Stop the existing container for at least 60 seconds if a Google Sheets 429 quota window is active.
5. Rebuild with `docker compose up -d --build --force-recreate`.
6. Confirm startup logs show the resilience layer, Discord login and slash-command sync.
7. Run `/health`, then smoke-test preorder, League and RobinCon customer flows.
8. Test staff reporting before using `/robincon-checkin` on a disposable ticket.

No Google Sheet headers or environment variable names changed in this stability release.

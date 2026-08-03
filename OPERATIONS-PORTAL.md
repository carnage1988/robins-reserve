# Robins Reserve Operations Portal

The original custom staff dashboard has been rebuilt against the refactored service layer. Budibase is not used.

## Modules

- Operations overview
- Pokémon preorder reservations and products
- Pokémon League status and attendance
- RobinCon summary, capacity and T-shirt totals
- RobinCon order and family-ticket lookup
- Ticket search and registration details
- Manual check-in / undo check-in
- Staff ticket edits using the existing audited service methods

## Local development

Add the Discord OAuth settings from `.env.example`, then run:

```bash
docker compose up -d --build --force-recreate
```

Open `http://localhost:10000`.

The Discord OAuth application callback must be exactly the value of `DISCORD_REDIRECT_URI`. The logged-in member must belong to `DISCORD_GUILD_ID` and hold one of the IDs in `DISCORD_STAFF_ROLE_IDS` (or `STAFF_ROLE_ID` as fallback).

## Architecture

```text
Browser -> Nginx frontend -> FastAPI -> shared service layer -> Google Sheets
Discord bot --------------------------^ 
```

No dashboard route writes directly to Google Sheets. RobinCon writes use `RobinConStaffService`, preserving capacity checks and Audit Log entries.

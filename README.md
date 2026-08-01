# Robin's Reserve

> A production-ready Discord bot for managing Pokémon TCG preorders for independent hobby stores.

Robin's Reserve automates the preorder process by allowing customers to submit requests via Discord Direct Messages while staff approve, reject, cancel and fulfil reservations using Google Sheets as the backend database.

The bot manages the complete preorder lifecycle from reservation through to collection, cancellation or rejection while maintaining accurate stock levels and a full audit trail.

---

## Quick Start

```bash
git clone https://github.com/carnage1988/robins-reserve.git
cd robins-reserve
cp .env.example .env
docker compose up -d --build
```

---

# Current Status

**Version:** v1.3.1

**Status:** Production

---

# Features

## Customer Workflow

- Submit preorder requests via Discord DM
- Supports multiple products in a single order
- Automatic collection PIN generation
- Customer notifications when an order is approved
- Customer notifications when an order is collected
- Cancel pending reservations
- Cancel approved reservations using the pickup PIN

## Staff Workflow

- Approve orders using Discord reactions
- Reject orders using Discord reactions
- Cancel approved reservations
- Automatic stock management
- Lookup reservations using:

```text
!lookup <PIN>
```

- Mark orders as collected using:

```text
!collect <PIN>
```
- Cancel approved reservations using:

```text
!cancel <PIN> [reason]
```

- Automatic archiving of:
  - Collected orders
  - Cancelled orders
  - Rejected orders

## RobinCon Staff Operations

Staff commands require the configured `STAFF_ROLE_ID` and must be run inside the Discord server.

```text
/robincon-ticket <ticket ID>
/robincon-find <query>
/robincon-order <order number>
/robincon-summary
/robincon-tshirts
/robincon-capacity
/robincon-attendees <Saturday|Sunday>
/robincon-checkin <ticket ID>
/robincon-uncheckin <ticket ID>
/robincon-edit <ticket ID> <field> <value>
```

Staff edits support attendee names, enabled T-shirt sizes, and active Saturday or Sunday premium events. Event changes enforce capacity and update both the Tickets and Event Registrations worksheets. Every write is recorded in the RobinCon Audit Log.

## Backend

- Google Sheets integration
- Persistent pending requests
- Structured logging
- Environment variable configuration
- Docker support
- Docker Compose deployment
- Order lifecycle management
- Centralised OrderManager

---

## Operational health commands

Staff can inspect the running service without searching container logs:

```text
/health
/cache-status
/cache-clear
```

`/health` reports service availability, League task state, Google Sheets request/retry counts and cache effectiveness. `/cache-clear` is administrator-only and should be used after urgent manual workbook changes when waiting for the normal cache expiry is undesirable.

## Google Sheets resilience

All gspread HTTP requests use bounded retry with exponential backoff for HTTP 429 and transient 5xx failures. Frequently repeated worksheet reads are cached briefly and invalidated immediately after writes. League role reconciliation uses one player read and one batched state write per cycle.


# Architecture

```
                    Customer
                        │
                        ▼
                 Discord Direct Message
                        │
                        ▼
               Robin's Reserve Bot
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
  Google Sheets   Pending Requests    Logs
         │
         ▼
 ┌───────────────────────────────┐
 │ Products                      │
 │ Preorders                     │
 │ Collected                     │
 │ Cancelled                     │
 │ Rejected                      │
 └───────────────────────────────┘
```

---

# Technologies Used

- Python 3.12
- discord.py
- Google Sheets API
- Docker
- Docker Compose

---

# Project Structure

```
discord-preorder-bot/
│
├── bot.py                 # Thin application entry point
├── config.py
├── app/                   # Runtime and dependency wiring
├── cogs/                  # Discord command/event modules
├── views/                 # Discord UI workflows
├── tasks/                 # Background task modules
├── services/              # Sheets, League and RobinCon services
├── utils/                 # Logging and shared helpers
├── Dockerfile
├── compose.yaml
├── requirements.txt
│
├── data/
│   └── pending_requests.json
│
├── logs/
│   └── robins_reserve.log
│
├── secrets/
│   ├── google-service-account.json
│   └── .env
│
└── docs/
```

---

# Docker Deployment

Clone the repository

```bash
git clone https://github.com/carnage1988/robins-reserve.git
```

Copy the example environment file

```bash
cp .env.example .env
```

Populate the following values:

- Discord Bot Token
- Google Sheet ID
- Staff Channel ID
- Staff Role ID
- League Guild ID
- League Channel ID
- League Player Role ID
- League attendance window
- League event duration

## Required Google Sheets

Robin's Reserve expects the following worksheets within the configured spreadsheet:

- Products
- Preorders
- Collected
- Cancelled
- Rejected
- League Players
- League Events
- League Attendance

Place the Google Service Account JSON inside:

```
secrets/
```

Build and start the application

```bash
docker compose up -d --build
```

View logs

```bash
docker compose logs -f
```

Stop the application

```bash
docker compose down
```

---

# Commands

## Staff Commands

Lookup an order

```text
!lookup <PIN>
```

Mark an order as collected

```text
!collect <PIN>
```

Cancel an approved order

```text
!cancel <PIN> [reason]
```

---

## Pokémon League Workflow

Staff manage in-store League sessions from the configured League channel:

```text
/league start
/league end
/league status
/league checkin @member
```

Players link their Play! Pokémon Player ID and check in using the code displayed inside Robins:

```text
/linkplayer <player_id>
/leaguecheckin <store_code>
/leaguestatus
/unlinkplayer
```

A successful check-in records attendance in Google Sheets and adds or renews the configured League Player role. A daily reconciliation removes the role when the player's last attendance falls outside the configured rolling window.

# Order Lifecycle

Each reservation progresses through a defined lifecycle while maintaining stock accuracy and a complete audit trail across Google Sheets.

```text
Pending
├── Approved
│   ├── Collected
│   └── Cancelled
└── Rejected
```

---

# Roadmap


## Future

- Improved search commands
- Enhanced reporting

- Reservation expiry
- Web dashboard

---

# Documentation

Additional documentation can be found inside the `docs/` directory.

---

# License

This project is licensed under the MIT License.

---

# Author

**Gavin Gillespie**

GitHub: https://github.com/carnage1988

---

# Acknowledgements

Robin's Reserve was developed as a real-world automation project to demonstrate practical software engineering, Docker deployment and DevOps practices while solving a genuine business workflow for an independent hobby store.

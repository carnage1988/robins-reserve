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

**Version:** v1.1.0

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
├── bot.py
├── config.py
├── sheets_service.py      # Google Sheets + OrderManager
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

## Required Google Sheets

Robin's Reserve expects the following worksheets within the configured spreadsheet:

- Products
- Preorders
- Collected
- Cancelled
- Rejected

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


## v1.2.0

- Improved search commands
- Enhanced reporting

## Future

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

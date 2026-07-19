# Robin's Reserve

> A production-ready Discord bot for managing Pokémon TCG preorders for independent hobby stores.

Robin's Reserve automates the preorder process by allowing customers to submit requests via Discord Direct Messages while staff approve, manage and fulfil reservations using Google Sheets as the backend database.

The project was designed to reduce manual administration, improve stock accuracy and provide a simple workflow that can be used by hobby stores without requiring additional software.

---

# Current Status

**Version:** v1.0.0

**Status:** Production

---

# Features

## Customer Workflow

- Submit preorder requests via Discord DM
- Supports multiple products in a single order
- Automatic collection PIN generation
- Customer notifications when an order is approved
- Customer notifications when an order is collected

## Staff Workflow

- Approve orders using Discord reactions
- Automatic stock deduction
- Lookup reservations using:

```text
!lookup <PIN>
```

- Mark orders as collected using:

```text
!collect <PIN>
```

- Automatic archive of collected orders

## Backend

- Google Sheets integration
- Persistent pending requests
- Structured logging
- Environment variable configuration
- Docker support
- Docker Compose deployment

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
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
 Google Sheets      Persistent Data     Logs
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
├── sheets_service.py
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

---

# Roadmap

## v1.1.0

- Order decline workflow
- Order cancellation
- Stock restoration
- Improved staff notifications

## Future

- Inventory management commands
- Customer self-cancellation
- Payment integration
- Reservation expiry
- Web dashboard
- Multi-store support

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

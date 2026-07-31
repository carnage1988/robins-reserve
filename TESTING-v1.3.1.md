# v1.3.1 Stability Test Plan

## Startup
- `python -m compileall -q .` returns no output.
- Container remains running for at least five minutes.
- No unhandled task exception appears.
- `/health` shows Preorder Sheets, League and RobinCon as available.

## Existing workflows
- `!ping` and `!products` respond.
- One disposable preorder can be reserved and cancelled.
- `/leaguestatus` responds and League commands retain their channel/role rules.
- `/robincon-status` and a completed `/robincon-register` summary respond.

## Reliability
- Repeating `/health` and `/cache-status` increases cache hits without excessive API reads.
- `/cache-clear` removes cached entries and the next read repopulates them.
- A departed League member does not stop reconciliation.
- Logs contain one reconciliation summary per run.

## RobinCon staff
- Ticket lookup and search return the expected test ticket.
- Capacity, attendee and T-shirt reports match the workbook.
- Disposable ticket check-in writes `Checked In`, `Checked In At` and an Audit Log row.
- Repeating check-in is rejected.

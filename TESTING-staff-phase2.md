# RobinCon Staff Administration Phase 2 Test Plan

Use the development Discord server and development RobinCon workbook.
All commands must be run in the server by a member holding `STAFF_ROLE_ID`.

## Read-only commands

- `/robincon-ticket RC27-000001` shows holder, order, registration, events and check-in state.
- `/robincon-order TEST-1001` shows every ticket belonging to the order.
- `/robincon-find Gavin` returns matching tickets.
- `/robincon-summary` returns order, ticket, linked, registered, check-in, event and shirt totals.
- `/robincon-capacity` agrees with Event Registrations.
- `/robincon-tshirts` agrees with the Tickets sheet.
- `/robincon-attendees Saturday` and `Sunday` list the correct attendees.

## Check-in

1. Run `/robincon-checkin <test ticket>`.
2. Confirm `Checked In = TRUE` and `Checked In At` is populated.
3. Confirm an Audit Log row was written.
4. Run the same command again and confirm duplicate check-in is rejected.
5. Run `/robincon-uncheckin <test ticket>`.
6. Confirm `Checked In = FALSE`, timestamp is blank, and an Audit Log row exists.
7. Run uncheck-in again and confirm it is rejected.

## Staff edit

Run `/robincon-edit` with each field:

- Attendee name: enter a replacement name.
- T-shirt size: enter an enabled Size ID or exact Display Name.
- Saturday event: enter an active Saturday Event ID or exact Event Name.
- Sunday event: enter an active Sunday Event ID or exact Event Name.

Verify:

- The Tickets row is updated.
- A completed registration also updates the corresponding Event Registrations row.
- Moving to a full event is rejected without changing data.
- Every successful edit creates an Audit Log row.
- `/robincon-ticket`, `/robincon-capacity`, and `/robincon-attendees` reflect the new value.

## Permission test

Run a staff command from a user without `STAFF_ROLE_ID` and confirm access is rejected.
Run a staff command through DM and confirm access is rejected.

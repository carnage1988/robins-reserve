# v1.3.2 - RobinCon Staff Administration Phase 2

## Added

- `/robincon-order` family and group order lookup
- `/robincon-summary` operational totals
- `/robincon-uncheckin` to reverse accidental check-ins
- `/robincon-edit` for attendee, T-shirt and premium-event corrections
- Capacity validation for staff event changes
- Audit logging for staff edits and reversed check-ins
- Staff Phase 2 test plan

## Changed

- RobinCon staff commands now use non-blocking worker threads for Google Sheets operations
- Ticket lookup displays friendly registration and check-in states
- Existing staff commands have clearer error handling and output formatting

# Unreleased

## Changed

- A single Discord account can now manage and register multiple RobinCon tickets from family or group orders.
- Ticket linking records the attendee name separately from the purchaser email.
- `/robincon-register` now asks which attendee ticket to manage when more than one ticket is linked.

# v1.3.1 - 31/07/2026

## Added
- Modular cogs, views, services and runtime structure
- Central Google Sheets retry, backoff, caching and cache invalidation layer
- RobinCon staff ticket lookup, search, attendee, T-shirt, capacity and manual check-in commands

## Changed
- Reduced bot.py to application startup and command synchronisation
- Isolated preorder, League and RobinCon customer workflows into dedicated modules
- Hardened League role reconciliation so one Sheets or Discord error does not terminate the loop

# v1.2.0 - 27/07/2026

## Added

- Pokémon League attendance management
- Staff-only `/league start`, `/league end`, `/league status` and `/league checkin` commands
- Player `/linkplayer`, `/unlinkplayer`, `/leaguecheckin` and `/leaguestatus` commands
- Time-limited in-store check-in codes
- Dedicated League check-in Discord channel support
- Google Sheets-backed League players, events and attendance records
- Automatic League Player role assignment after a successful check-in
- Daily role reconciliation using a rolling attendance window
- Duplicate event check-in prevention
- Staff manual check-in fallback

## Changed

- Slash commands are now synchronised globally and to the configured Robins guild
- League event expiry and role retention periods are configurable through environment variables
- League command permission and channel validation is centralised
- League service now provides event status, attendance totals and role reconciliation data

## Fixed

- Expired League events are automatically marked inactive
- Closing an event now returns its updated end time and inactive state
- Removed duplicate unreachable return statement from the League service

# v1.1.0 - 22/07/2026

## Added

- Customer cancellation for pending reservations
- Customer cancellation by pickup PIN for approved reservations
- Staff cancellation command using `!cancel <PIN> [reason]`
- Staff rejection workflow using the 👎 reaction
- Dedicated `Cancelled` Google Sheets archive
- Dedicated `Rejected` Google Sheets archive
- Cancellation and rejection audit fields
- Order lookup across active and archived sheets
- Centralised `OrderManager` for order lifecycle handling

## Changed

- Cancelled orders are now archived to the `Cancelled` sheet
- Rejected orders are now archived to the `Rejected` sheet
- Multi-product baskets retain a single lifecycle throughout approval, collection and cancellation
- Stock management now follows the complete order lifecycle
- Order lookup now searches archived reservations as well as active preorders
- Order lifecycle has been refactored to improve maintainability and simplify future feature development

# v1.0.0 - 17/07/2026

## Added

- Docker support
- Docker Compose deployment
- Persistent data directories
- Environment variable configuration
- Structured logging
- Basket ordering
- Collection workflow
- Lookup command
- Google Sheets archive
- Production deployment

# v1.4.0 - Dashboard Operations Pass

## Added

- Fixed red-accented Operations Portal layout with sectioned, collapsible navigation
- Dashboard service-health lights with green online and red offline states
- Pending approval cards with itemised pricing and basket totals
- Dashboard-driven approval and decline actions integrated with Discord reactions and customer DMs
- Pickup-PIN order lookup with collection controls and automatic 60-second idle reset
- Live Pokémon League card and page with running state, store code, attendance and event times
- Orders-today, collections-today, pending-order and League-attendance metrics
- Recent preorder activity feed
- Itemised pricing and basket totals in Discord approval messages

## Changed

- Preorder and archive sheets now require `Unit Price` and `Subtotal` columns
- Dashboard approvals use the original Discord approval-message ID and normal reservation lifecycle
- Completed dashboard decisions are removed from the shared pending-request persistence file

## Preserved

- Existing RobinCon family/group ticket registration support

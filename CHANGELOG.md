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

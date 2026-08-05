"""Import paid Squarespace RobinCon orders into the existing workflow."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Permit: python tasks/squarespace_import.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.robincon_service import RobinConService
from services.squarespace_service import SquarespaceService
from services.internal_events import notify_internal_event


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("squarespace_import")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalise(value: Any) -> str:
    return str(value or "").strip().casefold()


def customer_name(order: dict[str, Any]) -> str:
    """Build the purchaser name from billing or shipping details."""

    for address_key in ("billingAddress", "shippingAddress"):
        address = order.get(address_key)
        if not isinstance(address, dict):
            continue

        name = " ".join(
            part.strip()
            for part in (
                str(address.get("firstName", "")),
                str(address.get("lastName", "")),
            )
            if part.strip()
        )

        if name:
            return name

    return "Squarespace Customer"


def active_sku_mapping(
    robincon: RobinConService,
) -> dict[str, dict[str, Any]]:
    """Map active Squarespace SKUs to RobinCon ticket definitions."""

    mapping: dict[str, dict[str, Any]] = {}

    for ticket_type in robincon.get_ticket_types():
        sku = str(ticket_type.get("SKU", "")).strip().upper()
        code = str(ticket_type.get("Ticket Type", "")).strip()

        if not sku or not code:
            continue

        mapping[sku] = ticket_type

    return mapping


def existing_order_rows(
    robincon: RobinConService,
) -> dict[tuple[str, str], tuple[int, dict[str, Any]]]:
    """Return existing rows keyed by order number and ticket type."""

    existing: dict[
        tuple[str, str],
        tuple[int, dict[str, Any]],
    ] = {}

    for row_number, record in enumerate(
        robincon.orders_sheet.get_all_records(),
        start=2,
    ):
        key = (
            normalise(record.get("Order Number", "")),
            normalise(record.get("Ticket Type", "")),
        )

        if key[0] and key[1]:
            existing[key] = (row_number, record)

    return existing


def update_existing_order(
    robincon: RobinConService,
    *,
    row_number: int,
    current: dict[str, Any],
    imported: dict[str, Any],
) -> dict[str, Any]:
    """Update safe mutable fields and return a current order record."""

    headers = robincon.orders_sheet.row_values(1)
    columns = {header: index + 1 for index, header in enumerate(headers)}

    updates = {
        "Customer Name": imported["Customer Name"],
        "Customer Email": imported["Customer Email"],
        "Payment Status": imported["Payment Status"],
        "Order Status": imported["Order Status"],
    }

    old_quantity = int(current.get("Quantity", 0) or 0)
    new_quantity = int(imported.get("Quantity", 0) or 0)

    # Never silently remove already-created tickets.
    if new_quantity > old_quantity:
        updates["Quantity"] = new_quantity

    result = dict(current)

    for header, value in updates.items():
        if header not in columns:
            continue

        if str(result.get(header, "")) == str(value):
            continue

        robincon.orders_sheet.update_cell(
            row_number,
            columns[header],
            value,
        )
        result[header] = value

    result["row_number"] = row_number
    return result


def append_order(
    robincon: RobinConService,
    record: dict[str, Any],
) -> dict[str, Any]:
    """Append a new Orders row and return it with its row number."""

    headers = robincon.orders_sheet.row_values(1)

    robincon.orders_sheet.append_row(
        [record.get(header, "") for header in headers],
        value_input_option="USER_ENTERED",
    )

    record = dict(record)
    record["row_number"] = len(
        robincon.orders_sheet.get_all_records()
    ) + 1

    return record


def import_orders(
    *,
    dry_run: bool,
    days: int,
    limit: int | None,
) -> int:
    robincon = RobinConService()
    squarespace = SquarespaceService()

    sku_mapping = active_sku_mapping(robincon)

    if not sku_mapping:
        raise RuntimeError(
            "No active RobinCon ticket types have a configured SKU."
        )

    logger.info(
        "Active Squarespace ticket mappings: %s",
        ", ".join(
            f"{sku} -> {definition.get('Ticket Type')}"
            for sku, definition in sorted(sku_mapping.items())
        ),
    )

    modified_after = squarespace.iso_utc_days_ago(days)
    orders = squarespace.list_recent_paid_orders(
	days=days,
    )

    if limit is not None:
        orders = orders[: max(0, limit)]

    existing = existing_order_rows(robincon)

    imported_count = 0
    matched_order_count = 0

    for order in orders:
        payment_state = str(order.get("paymentState", "")).upper()
        fulfillment_status = str(
            order.get("fulfillmentStatus", "")
        ).upper()

        if payment_state != "PAID":
            continue

        if fulfillment_status == "CANCELED":
            logger.info(
                "Skipping cancelled Squarespace order %s",
                order.get("orderNumber"),
            )
            continue

        order_number = str(order.get("orderNumber", "")).strip()
        email = str(order.get("customerEmail", "")).strip()

        if not order_number or not email:
            logger.warning(
                "Skipping Squarespace order with missing number/email: %s",
                order.get("id"),
            )
            continue

        quantities_by_ticket_type: dict[str, int] = defaultdict(int)

        line_items = order.get("lineItems", [])
        if not isinstance(line_items, list):
            continue

        for item in line_items:
            if not isinstance(item, dict):
                continue

            sku = str(item.get("sku", "")).strip().upper()
            definition = sku_mapping.get(sku)

            if definition is None:
                continue

            ticket_type = str(
                definition.get("Ticket Type", "")
            ).strip()

            try:
                quantity = int(item.get("quantity", 0) or 0)
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid quantity on order %s SKU %s",
                    order_number,
                    sku,
                )
                continue

            if quantity > 0:
                quantities_by_ticket_type[ticket_type] += quantity

        if not quantities_by_ticket_type:
            continue

        matched_order_count += 1

        for ticket_type, quantity in quantities_by_ticket_type.items():
            import_record = {
                "Order Number": order_number,
                "Order Date": str(order.get("createdOn", "")).strip(),
                "Customer Name": customer_name(order),
                "Customer Email": email,
                "Ticket Type": ticket_type,
                "Quantity": quantity,
                "Payment Status": "Paid",
                "Import Source": "Squarespace",
                "Import Timestamp": now_iso(),
                "Order Status": fulfillment_status.title() or "Pending",
                "Processed": "FALSE",
                "Processed Timestamp": "",
            }

            key = (
                normalise(order_number),
                normalise(ticket_type),
            )

            if dry_run:
                logger.info(
                    "[DRY RUN] Would import order=%s ticket_type=%s "
                    "quantity=%s customer=%s <%s>",
                    order_number,
                    ticket_type,
                    quantity,
                    import_record["Customer Name"],
                    email,
                )
                continue

            existing_entry = existing.get(key)

            if existing_entry:
                row_number, current = existing_entry
                saved_order = update_existing_order(
                    robincon,
                    row_number=row_number,
                    current=current,
                    imported=import_record,
                )
                logger.info(
                    "Order %s / %s already exists; ensuring ticket rows.",
                    order_number,
                    ticket_type,
                )
            else:
                saved_order = append_order(
                    robincon,
                    import_record,
                )
                existing[key] = (
                    int(saved_order["row_number"]),
                    saved_order,
                )
                imported_count += 1
                logger.info(
                    "Imported Squarespace order %s / %s x%s.",
                    order_number,
                    ticket_type,
                    quantity,
                )

            tickets = robincon.ensure_order_tickets(saved_order)

            logger.info(
                "Order %s now has %s RobinCon ticket row(s).",
                order_number,
                len(tickets),
            )

    if imported_count > 0 and not dry_run:
        notify_internal_event("squarespace.imported")

    logger.info(
        "Squarespace scan complete: %s API order(s), "
        "%s matching order(s), %s newly imported row(s).",
        len(orders),
        matched_order_count,
        imported_count,
    )

    return imported_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import paid Squarespace RobinCon orders.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect matching orders without writing to Google Sheets.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=int(os.getenv("SQUARESPACE_IMPORT_DAYS", "30")),
        help="Import orders modified within this many days.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only inspect the first N returned orders.",
    )

    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()

    try:
        import_orders(
            dry_run=arguments.dry_run,
            days=arguments.days,
            limit=arguments.limit,
        )
    except Exception:
        logger.exception("Squarespace RobinCon import failed.")
        raise SystemExit(1)

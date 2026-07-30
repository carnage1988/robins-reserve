import logging
import secrets
from datetime import datetime, timezone
from typing import Any

import gspread
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound

from config import GOOGLE_CREDENTIALS_FILE, GOOGLE_SHEET_ID


logger = logging.getLogger(__name__)


class OrderManager:
    """Coordinate safe order lifecycle transitions between worksheets."""

    def __init__(self, service: "SheetsService") -> None:
        self.service = service

    def approve(
        self,
        *,
        pickup_pin: str,
        approved_by: str,
        approval_message_id: int,
    ) -> dict[str, Any]:
        order = self.service.lookup_by_pin(pickup_pin)
        if order is None:
            raise ValueError("No pending preorder was found for that pickup PIN.")
        if order["sheet_name"] != "Preorders":
            raise ValueError("This preorder is no longer awaiting approval.")
        status = str(order["status"]).strip().casefold()
        if status != "pending":
            raise ValueError(
                f"This preorder cannot be approved because its status is "
                f"'{order['status']}'."
            )

        sheet = self.service.preorders_sheet
        headers = sheet.row_values(1)
        try:
            status_col = headers.index("Status") + 1
            approved_col = headers.index("Approved By") + 1
            message_col = headers.index("Approval Message ID") + 1
        except ValueError as exc:
            raise RuntimeError(
                "Preorders sheet is missing approval workflow headers."
            ) from exc

        changed: list[int] = []
        try:
            for item in order["items"]:
                row = int(item["row_number"])
                sheet.update_cell(row, status_col, "Approved")
                sheet.update_cell(row, approved_col, approved_by)
                sheet.update_cell(row, message_col, str(approval_message_id))
                changed.append(row)
        except Exception:
            for row in changed:
                sheet.update_cell(row, status_col, "Pending")
                sheet.update_cell(row, approved_col, "")
            raise

        order["status"] = "Approved"
        order["approved_by"] = approved_by
        order["approval_message_id"] = str(approval_message_id)
        for item in order["items"]:
            item["status"] = "Approved"
            item["approved_by"] = approved_by
            item["approval_message_id"] = str(approval_message_id)
        return order

    def archive(
        self,
        *,
        pickup_pin: str,
        destination_sheet: gspread.Worksheet,
        destination_name: str,
        final_status: str,
        allowed_statuses: set[str],
        actor_header: str,
        actor: str,
        timestamp_header: str,
        reason_header: str | None = None,
        reason: str = "",
        restore_stock: bool = False,
        discord_user_id: int | None = None,
    ) -> dict[str, Any]:
        order = self.service.lookup_by_pin(pickup_pin)
        if order is None:
            raise ValueError("No preorder was found for that pickup PIN.")
        if order["sheet_name"] != "Preorders":
            raise ValueError(
                f"This preorder is already archived in {order['sheet_name']}."
            )
        if discord_user_id is not None and (
            str(order["discord_user_id"]) != str(discord_user_id)
        ):
            raise ValueError(
                "This reservation does not belong to your Discord account."
            )

        current_status = str(order["status"]).strip().casefold()
        if current_status not in allowed_statuses:
            allowed = " or ".join(sorted(allowed_statuses))
            raise ValueError(
                f"This preorder cannot be changed from '{order['status']}'. "
                f"It must be {allowed}."
            )

        source = self.service.preorders_sheet
        source_headers = source.row_values(1)
        destination_headers = destination_sheet.row_values(1)
        occurred_at = datetime.now(timezone.utc).isoformat()
        archive_rows: list[list[Any]] = []

        for item in order["items"]:
            values = source.row_values(int(item["row_number"]))
            values += [""] * max(0, len(source_headers) - len(values))
            record = dict(zip(source_headers, values))
            record["Status"] = final_status
            record[timestamp_header] = occurred_at
            record[actor_header] = actor
            if reason_header:
                record[reason_header] = reason
            archive_rows.append([record.get(h, "") for h in destination_headers])

        stock_updates = (
            self.service._prepare_stock_restoration(order["items"])
            if restore_stock
            else []
        )
        appended = 0
        try:
            if stock_updates:
                self.service._apply_stock_updates(stock_updates)
            for row in archive_rows:
                destination_sheet.append_row(
                    row, value_input_option="USER_ENTERED"
                )
                appended += 1
            for row_number in sorted(
                (int(i["row_number"]) for i in order["items"]),
                reverse=True,
            ):
                source.delete_rows(row_number)
        except Exception:
            for _ in range(appended):
                destination_sheet.delete_rows(
                    len(destination_sheet.get_all_values())
                )
            if stock_updates:
                self.service._rollback_stock_updates(stock_updates)
            raise

        order["status"] = final_status
        order["sheet_name"] = destination_name
        order[self.service._key_for_header(timestamp_header)] = occurred_at
        order[self.service._key_for_header(actor_header)] = actor
        if reason_header:
            order[self.service._key_for_header(reason_header)] = reason
        for item in order["items"]:
            item["status"] = final_status
            item["sheet_name"] = destination_name
            item[self.service._key_for_header(timestamp_header)] = occurred_at
            item[self.service._key_for_header(actor_header)] = actor
            if reason_header:
                item[self.service._key_for_header(reason_header)] = reason
        return order


class SheetsService:
    """Read and write preorder information in Google Sheets."""

    BASE_ORDER_HEADERS = [
        "Timestamp",
        "Discord Username",
        "Discord User ID",
        "Product ID",
        "Product Name",
        "Quantity",
        "Status",
        "Approved By",
        "Approval Message ID",
        "Pickup PIN",
    ]
    COLLECTED_HEADERS = BASE_ORDER_HEADERS + ["Collected At", "Collected By"]
    CANCELLED_HEADERS = BASE_ORDER_HEADERS + [
        "Cancelled At", "Cancelled By", "Cancellation Reason"
    ]
    REJECTED_HEADERS = BASE_ORDER_HEADERS + [
        "Rejected At", "Rejected By", "Rejection Reason"
    ]
    ORDER_HEADERS = COLLECTED_HEADERS
    
    LEAGUE_PLAYER_HEADERS = [
        "Discord User ID",
        "Discord Name",
        "Player ID",
        "Last Attendance",
        "Role Active",
        "Linked At",
    ]

    LEAGUE_EVENT_HEADERS = [
        "Event ID",
        "Store Code",
        "Start Time",
        "End Time",
        "Active",
    ]

    LEAGUE_ATTENDANCE_HEADERS = [
        "Event ID",
        "Discord User ID",
        "Player ID",
        "Checked In At",
    ]

    def __init__(self) -> None:
        try:
            client = gspread.service_account(
                filename=str(GOOGLE_CREDENTIALS_FILE)
            )

            self.spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
            self.products_sheet = self.spreadsheet.worksheet("Products")
            self.preorders_sheet = self.spreadsheet.worksheet("Preorders")
            self.collected_sheet = self.spreadsheet.worksheet("Collected")
            self.cancelled_sheet = self.spreadsheet.worksheet("Cancelled")
            self.rejected_sheet = self.spreadsheet.worksheet("Rejected")

            self.league_players_sheet = self.spreadsheet.worksheet(
                "League Players"
            )
            self.league_events_sheet = self.spreadsheet.worksheet(
                "League Events"
            )
            self.league_attendance_sheet = self.spreadsheet.worksheet(
                "League Attendance"
            )

        except SpreadsheetNotFound as exc:
            raise RuntimeError(
                "The spreadsheet must contain Products, Preorders, Collected, "
                "Cancelled, Rejected, League Players, League Events and "
                "League Attendance tabs."
                
            ) from exc

        except WorksheetNotFound as exc:
            raise RuntimeError(
                "The spreadsheet must contain Products, Preorders, Collected, "
                "Cancelled and Rejected tabs."
            ) from exc

        except APIError as exc:
            raise RuntimeError(
                f"Google Sheets API error: {exc}"
            ) from exc

        self._validate_order_headers(
            self.preorders_sheet, "Preorders", self.BASE_ORDER_HEADERS
        )
        self._validate_order_headers(
            self.collected_sheet, "Collected", self.COLLECTED_HEADERS
        )
        self._validate_order_headers(
            self.cancelled_sheet, "Cancelled", self.CANCELLED_HEADERS
        )
        self._validate_order_headers(
            self.rejected_sheet, "Rejected", self.REJECTED_HEADERS
        )

        self._validate_order_headers(
            self.league_players_sheet,
            "League Players",
            self.LEAGUE_PLAYER_HEADERS,
        )
        self._validate_order_headers(
            self.league_events_sheet,
            "League Events",
            self.LEAGUE_EVENT_HEADERS,
        )
        self._validate_order_headers(
            self.league_attendance_sheet,
            "League Attendance",
            self.LEAGUE_ATTENDANCE_HEADERS,
        )

        self.order_manager = OrderManager(self) 
        
    def _validate_order_headers(
        self,
        worksheet: gspread.Worksheet,
        sheet_name: str,
        required_headers: list[str],
    ) -> None:
        """Ensure an order worksheet has the required header columns."""

        headers = worksheet.row_values(1)
        missing = [
            header
            for header in required_headers
            if header not in headers
        ]

        if missing:
            raise RuntimeError(
                f"{sheet_name} sheet is missing required headers: "
                f"{', '.join(missing)}"
            )

    @staticmethod
    def _key_for_header(header: str) -> str:
        return header.strip().lower().replace(" ", "_")

    def get_products(self, open_only: bool = False) -> list[dict[str, Any]]:
        """Return valid products from the Products tab."""

        records = self.products_sheet.get_all_records()
        products: list[dict[str, Any]] = []

        for row_number, record in enumerate(records, start=2):
            product_id = str(record.get("Product ID", "")).strip()
            product_name = str(record.get("Product Name", "")).strip()
            order_code = str(
                record.get(
                    "Order Code",
                    record.get("Trigger Phrase", ""),
                )
            ).strip()

            if not product_id or not product_name or not order_code:
                logger.warning(
                    "Ignoring incomplete product on row %s",
                    row_number,
                )
                continue

            try:
                stock = int(record.get("Stock", 0))
                customer_limit = int(record.get("Customer Limit", 0))
            except (TypeError, ValueError):
                logger.warning(
                    "Product %s has invalid stock or limit values.",
                    product_id,
                )
                continue

            open_value = str(
                record.get("Preorders Open", "")
            ).strip().upper()

            preorders_open = open_value in {
                "TRUE",
                "YES",
                "Y",
                "1",
            }

            league_only_value = str(
                record.get("League Only", "")
            ).strip().upper()

            league_only = league_only_value in {
                "TRUE",
                "YES",
                "Y",
                "1",
            }

            product = {
                "product_id": product_id,
                "product_name": product_name,
                "order_code": order_code,
                "trigger_phrase": order_code,
                "category": str(record.get("Category", "")).strip(),
                "stock": stock,
                "customer_limit": customer_limit,
                "preorders_open": preorders_open,
                "league_only": league_only,
            }

            if open_only and not preorders_open:
                continue

            products.append(product)

        return products

    def find_product_by_order_code(
        self,
        message_content: str,
    ) -> dict[str, Any] | None:
        """Find a product matching an exact order code."""

        customer_message = message_content.strip().casefold()

        for product in self.get_products(open_only=False):
            order_code = product["order_code"].strip().casefold()

            if order_code and customer_message == order_code:
                return product

        return None

    def find_product_by_trigger(
        self,
        message_content: str,
    ) -> dict[str, Any] | None:
        """Backward-compatible alias for order-code lookup."""

        return self.find_product_by_order_code(message_content)

    def find_products_by_partial_code(
        self,
        message_content: str,
    ) -> list[dict[str, Any]]:
        """Return open products whose order code contains the supplied text."""

        search_text = message_content.strip().casefold()

        if len(search_text) < 3:
            return []

        return [
            product
            for product in self.get_products(open_only=True)
            if search_text in product["order_code"].casefold()
        ]

    @staticmethod
    def _record_to_item(
        record: dict[str, Any],
        row_number: int,
        sheet_name: str,
    ) -> dict[str, Any]:
        """Convert one worksheet row into a normalized order item."""

        return {
            "row_number": row_number,
            "sheet_name": sheet_name,
            "timestamp": str(record.get("Timestamp", "")).strip(),
            "discord_username": str(
                record.get("Discord Username", "")
            ).strip(),
            "discord_user_id": str(
                record.get("Discord User ID", "")
            ).strip(),
            "product_id": str(record.get("Product ID", "")).strip(),
            "product_name": str(record.get("Product Name", "")).strip(),
            "quantity": int(record.get("Quantity", 0) or 0),
            "status": str(record.get("Status", "")).strip(),
            "approved_by": str(record.get("Approved By", "")).strip(),
            "approval_message_id": str(
                record.get("Approval Message ID", "")
            ).strip(),
            "pickup_pin": str(record.get("Pickup PIN", "")).strip(),
            "collected_at": str(
                record.get("Collected At", "")
            ).strip(),
            "collected_by": str(record.get("Collected By", "")).strip(),
            "cancelled_at": str(record.get("Cancelled At", "")).strip(),
            "cancelled_by": str(record.get("Cancelled By", "")).strip(),
            "cancellation_reason": str(
                record.get("Cancellation Reason", "")
            ).strip(),
            "rejected_at": str(record.get("Rejected At", "")).strip(),
            "rejected_by": str(record.get("Rejected By", "")).strip(),
            "rejection_reason": str(
                record.get("Rejection Reason", "")
            ).strip(),
        }

    def _find_items_in_sheet(
        self,
        worksheet: gspread.Worksheet,
        sheet_name: str,
        pickup_pin: str,
    ) -> list[dict[str, Any]]:
        """Find all rows in one worksheet sharing a pickup PIN."""

        pin = pickup_pin.strip()
        items: list[dict[str, Any]] = []

        for row_number, record in enumerate(
            worksheet.get_all_records(),
            start=2,
        ):
            recorded_pin = str(record.get("Pickup PIN", "")).strip()

            if recorded_pin == pin:
                items.append(
                    self._record_to_item(
                        record,
                        row_number,
                        sheet_name,
                    )
                )

        return items

    def lookup_by_pin(self, pickup_pin: str) -> dict[str, Any] | None:
        """Return a complete multi-product order for a pickup PIN."""

        pin = pickup_pin.strip()

        if not pin:
            return None

        items: list[dict[str, Any]] = []
        for worksheet, sheet_name in (
            (self.preorders_sheet, "Preorders"),
            (self.collected_sheet, "Collected"),
            (self.cancelled_sheet, "Cancelled"),
            (self.rejected_sheet, "Rejected"),
        ):
            items = self._find_items_in_sheet(worksheet, sheet_name, pin)
            if items:
                break

        if not items:
            return None

        first = items[0]

        return {
            "pickup_pin": pin,
            "discord_username": first["discord_username"],
            "discord_user_id": first["discord_user_id"],
            "status": first["status"],
            "approved_by": first["approved_by"],
            "approval_message_id": first["approval_message_id"],
            "timestamp": first["timestamp"],
            "collected_at": first["collected_at"],
            "collected_by": first["collected_by"],
            "cancelled_at": first["cancelled_at"],
            "cancelled_by": first["cancelled_by"],
            "cancellation_reason": first["cancellation_reason"],
            "rejected_at": first["rejected_at"],
            "rejected_by": first["rejected_by"],
            "rejection_reason": first["rejection_reason"],
            "sheet_name": first["sheet_name"],
            "items": items,
            "total_quantity": sum(item["quantity"] for item in items),
        }

    def get_pending_reservation_for_customer(
        self,
        discord_user_id: int,
    ) -> dict[str, Any] | None:
        """Return the customer's most recently created pending reservation."""

        target_user_id = str(discord_user_id)
        records = self.preorders_sheet.get_all_records()

        for record in reversed(records):
            recorded_user_id = str(
                record.get("Discord User ID", "")
            ).strip()
            status = str(record.get("Status", "")).strip().casefold()
            pickup_pin = str(record.get("Pickup PIN", "")).strip()

            if (
                recorded_user_id == target_user_id
                and status == "pending"
                and pickup_pin
            ):
                return self.lookup_by_pin(pickup_pin)

        return None

    def approval_already_processed(
        self,
        approval_message_id: int,
    ) -> bool:
        """Check both active and archived orders for an approval message."""

        target_id = str(approval_message_id)

        for worksheet in (
            self.preorders_sheet,
            self.collected_sheet,
            self.cancelled_sheet,
            self.rejected_sheet,
        ):
            for record in worksheet.get_all_records():
                recorded_id = str(
                    record.get("Approval Message ID", "")
                ).strip()

                if recorded_id == target_id:
                    return True

        return False

    def get_customer_product_total(
        self,
        discord_user_id: int,
        product_id: str,
    ) -> int:
        """Return the customer's pending, approved and collected quantity."""

        total = 0
        target_user_id = str(discord_user_id)

        for worksheet in (
            self.preorders_sheet,
            self.collected_sheet,
        ):
            for record in worksheet.get_all_records():
                recorded_user_id = str(
                    record.get("Discord User ID", "")
                ).strip()
                recorded_product_id = str(
                    record.get("Product ID", "")
                ).strip()
                status = str(
                    record.get("Status", "")
                ).strip().casefold()

                if (
                    recorded_user_id == target_user_id
                    and recorded_product_id == product_id
                    and status in {"pending", "approved", "collected"}
                ):
                    try:
                        total += int(record.get("Quantity", 0))
                    except (TypeError, ValueError):
                        logger.warning(
                            "Ignoring an invalid preorder quantity."
                        )

        return total

    def _generate_unique_pin(self) -> str:
        """Generate a six-digit PIN unused in active or archived orders."""

        while True:
            pickup_pin = f"{secrets.randbelow(900000) + 100000}"

            if self.lookup_by_pin(pickup_pin) is None:
                return pickup_pin

    def _prepare_stock_updates(
        self,
        normalized_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Determine the stock changes required for a basket."""

        headers = self.products_sheet.row_values(1)

        try:
            product_id_column = headers.index("Product ID") + 1
        except ValueError as exc:
            raise RuntimeError(
                "Products sheet is missing Product ID header."
            ) from exc

        stock_updates: list[dict[str, Any]] = []

        for item in normalized_items:
            product_cell = self.products_sheet.find(
                item["product_id"],
                in_column=product_id_column,
            )

            if product_cell is None:
                raise ValueError(
                    f"Could not locate {item['product_name']} in Products."
                )

            stock_updates.append(
                {
                    "row": product_cell.row,
                    "old_stock": item["stock"],
                    "new_stock": item["stock"] - item["quantity"],
                }
            )

        return stock_updates

    def _get_stock_column(self) -> int:
        """Return the one-based Stock column number."""

        headers = self.products_sheet.row_values(1)

        try:
            return headers.index("Stock") + 1
        except ValueError as exc:
            raise RuntimeError(
                "Products sheet is missing Stock header."
            ) from exc

    def _apply_stock_updates(
        self,
        stock_updates: list[dict[str, Any]],
    ) -> None:
        """Apply stock changes to the Products sheet."""

        stock_column = self._get_stock_column()

        for update in stock_updates:
            self.products_sheet.update_cell(
                update["row"],
                stock_column,
                update["new_stock"],
            )

    def _rollback_stock_updates(
        self,
        stock_updates: list[dict[str, Any]],
    ) -> None:
        """Restore stock values after a failed operation."""

        stock_column = self._get_stock_column()

        for update in stock_updates:
            self.products_sheet.update_cell(
                update["row"],
                stock_column,
                update["old_stock"],
            )


    def _prepare_stock_restoration(
        self,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Determine stock increases needed when releasing a reservation."""

        products = {
            product["product_id"]: product
            for product in self.get_products(open_only=False)
        }
        headers = self.products_sheet.row_values(1)

        try:
            product_id_column = headers.index("Product ID") + 1
        except ValueError as exc:
            raise RuntimeError(
                "Products sheet is missing Product ID header."
            ) from exc

        stock_updates: list[dict[str, Any]] = []

        for item in items:
            product_id = str(item["product_id"])
            product = products.get(product_id)

            if product is None:
                raise ValueError(
                    f"Could not locate {item['product_name']} in Products."
                )

            product_cell = self.products_sheet.find(
                product_id,
                in_column=product_id_column,
            )

            if product_cell is None:
                raise ValueError(
                    f"Could not locate {item['product_name']} in Products."
                )

            current_stock = int(product["stock"])
            quantity = int(item["quantity"])
            stock_updates.append(
                {
                    "row": product_cell.row,
                    "old_stock": current_stock,
                    "new_stock": current_stock + quantity,
                }
            )

        return stock_updates

    def reserve_basket(
        self,
        *,
        discord_username: str,
        discord_user_id: int,
        basket: list[dict[str, Any]],
        approval_message_id: int | None = None,
    ) -> dict[str, Any]:
        """Reserve stock and create a pending preorder under one pickup PIN."""

        if not basket:
            raise ValueError("The basket is empty.")

        products = {
            product["product_id"]: product
            for product in self.get_products()
        }

        normalized_items: list[dict[str, Any]] = []

        for basket_item in basket:
            product_id = str(basket_item["product_id"])
            quantity = int(basket_item["quantity"])

            if quantity < 1:
                raise ValueError("Quantity must be at least 1.")

            product = products.get(product_id)

            if product is None:
                raise ValueError(
                    f"Product {product_id} no longer exists."
                )

            if not product["preorders_open"]:
                raise ValueError(
                    f"Preorders are closed for "
                    f"{product['product_name']}."
                )

            if quantity > product["stock"]:
                raise ValueError(
                    f"Only {product['stock']} × "
                    f"{product['product_name']} remain."
                )

            current_total = self.get_customer_product_total(
                discord_user_id,
                product_id,
            )

            if current_total + quantity > product["customer_limit"]:
                remaining_limit = max(
                    product["customer_limit"] - current_total,
                    0,
                )
                raise ValueError(
                    f"The customer may only order {remaining_limit} more × "
                    f"{product['product_name']}."
                )

            normalized_items.append(
                {
                    **product,
                    "quantity": quantity,
                }
            )

        stock_updates = self._prepare_stock_updates(
            normalized_items
        )

        pickup_pin = self._generate_unique_pin()
        timestamp = datetime.now(timezone.utc).isoformat()
        recorded_message_id = (
            str(approval_message_id)
            if approval_message_id is not None
            else ""
        )
        appended_rows = 0

        try:
            self._apply_stock_updates(stock_updates)

            for item in normalized_items:
                self.preorders_sheet.append_row(
                    [
                        timestamp,
                        discord_username,
                        str(discord_user_id),
                        item["product_id"],
                        item["product_name"],
                        item["quantity"],
                        "Pending",
                        "",
                        recorded_message_id,
                        pickup_pin,
                        "",
                        "",
                    ],
                    value_input_option="USER_ENTERED",
                )
                appended_rows += 1

        except Exception:
            self._rollback_stock_updates(stock_updates)

            for _ in range(appended_rows):
                self.preorders_sheet.delete_rows(
                    len(self.preorders_sheet.get_all_values())
                )

            raise

        reserved_items = []

        for item, update in zip(normalized_items, stock_updates):
            reserved_items.append(
                {
                    **item,
                    "stock": update["new_stock"],
                    "status": "Pending",
                }
            )

        return {
            "pickup_pin": pickup_pin,
            "status": "Pending",
            "items": reserved_items,
            "total_quantity": sum(
                item["quantity"] for item in reserved_items
            ),
        }

    def approve_reservation(
        self,
        *,
        pickup_pin: str,
        approved_by: str,
        approval_message_id: int,
    ) -> dict[str, Any]:
        return self.order_manager.approve(
            pickup_pin=pickup_pin,
            approved_by=approved_by,
            approval_message_id=approval_message_id,
        )

    def decline_reservation(
        self,
        *,
        pickup_pin: str,
        declined_by: str,
        approval_message_id: int,
        reason: str = "Staff declined reservation",
    ) -> dict[str, Any]:
        del approval_message_id
        return self.order_manager.archive(
            pickup_pin=pickup_pin,
            destination_sheet=self.rejected_sheet,
            destination_name="Rejected",
            final_status="Rejected",
            allowed_statuses={"pending"},
            actor_header="Rejected By",
            actor=declined_by,
            timestamp_header="Rejected At",
            reason_header="Rejection Reason",
            reason=reason,
            restore_stock=True,
        )

    def cancel_reservation(
        self,
        *,
        pickup_pin: str,
        cancelled_by: str,
        reason: str = "Customer request",
        discord_user_id: int | None = None,
        allowed_statuses: set[str] | None = None,
    ) -> dict[str, Any]:
        return self.order_manager.archive(
            pickup_pin=pickup_pin,
            destination_sheet=self.cancelled_sheet,
            destination_name="Cancelled",
            final_status="Cancelled",
            allowed_statuses=allowed_statuses or {"pending", "approved"},
            actor_header="Cancelled By",
            actor=cancelled_by,
            timestamp_header="Cancelled At",
            reason_header="Cancellation Reason",
            reason=reason,
            restore_stock=True,
            discord_user_id=discord_user_id,
        )

    def approve_basket(
        self,
        *,
        discord_username: str,
        discord_user_id: int,
        basket: list[dict[str, Any]],
        approved_by: str,
        approval_message_id: int,
    ) -> dict[str, Any]:
        """Approve an entire basket under one pickup PIN."""

        if self.approval_already_processed(approval_message_id):
            raise ValueError(
                "This approval has already been processed."
            )

        if not basket:
            raise ValueError("The basket is empty.")

        products = {
            product["product_id"]: product
            for product in self.get_products()
        }

        normalized_items: list[dict[str, Any]] = []

        for basket_item in basket:
            product_id = str(basket_item["product_id"])
            quantity = int(basket_item["quantity"])

            if quantity < 1:
                raise ValueError("Quantity must be at least 1.")

            product = products.get(product_id)

            if product is None:
                raise ValueError(
                    f"Product {product_id} no longer exists."
                )

            if not product["preorders_open"]:
                raise ValueError(
                    f"Preorders are closed for "
                    f"{product['product_name']}."
                )

            if quantity > product["stock"]:
                raise ValueError(
                    f"Only {product['stock']} × "
                    f"{product['product_name']} remain."
                )

            current_total = self.get_customer_product_total(
                discord_user_id,
                product_id,
            )

            if current_total + quantity > product["customer_limit"]:
                remaining_limit = max(
                    product["customer_limit"] - current_total,
                    0,
                )
                raise ValueError(
                    f"The customer may only order {remaining_limit} more × "
                    f"{product['product_name']}."
                )

            normalized_items.append(
                {
                    **product,
                    "quantity": quantity,
                }
            )

        stock_updates = self._prepare_stock_updates(
            normalized_items
        )

        pickup_pin = self._generate_unique_pin()
        timestamp = datetime.now(timezone.utc).isoformat()
        appended_rows = 0

        try:
            self._apply_stock_updates(stock_updates)

            for item in normalized_items:
                self.preorders_sheet.append_row(
                    [
                        timestamp,
                        discord_username,
                        str(discord_user_id),
                        item["product_id"],
                        item["product_name"],
                        item["quantity"],
                        "Approved",
                        approved_by,
                        str(approval_message_id),
                        pickup_pin,
                        "",
                        "",
                    ],
                    value_input_option="USER_ENTERED",
                )
                appended_rows += 1

        except Exception:
            self._rollback_stock_updates(stock_updates)

            for _ in range(appended_rows):
                self.preorders_sheet.delete_rows(
                    len(self.preorders_sheet.get_all_values())
                )

            raise

        approved_items = []

        for item, update in zip(normalized_items, stock_updates):
            approved_items.append(
                {
                    **item,
                    "stock": update["new_stock"],
                }
            )

        return {
            "pickup_pin": pickup_pin,
            "items": approved_items,
            "total_quantity": sum(
                item["quantity"] for item in approved_items
            ),
        }

    def collect_order(
        self,
        *,
        pickup_pin: str,
        collected_by: str,
    ) -> dict[str, Any]:
        return self.order_manager.archive(
            pickup_pin=pickup_pin,
            destination_sheet=self.collected_sheet,
            destination_name="Collected",
            final_status="Collected",
            allowed_statuses={"approved"},
            actor_header="Collected By",
            actor=collected_by,
            timestamp_header="Collected At",
            restore_stock=False,
        )

    def connection_status(self) -> dict[str, Any]:
        """Return basic spreadsheet connection information."""

        products = self.get_products()

        return {
            "title": self.spreadsheet.title,
            "product_count": len(products),
        }

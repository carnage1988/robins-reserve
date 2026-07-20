import logging
import secrets
from datetime import datetime, timezone
from typing import Any

import gspread
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound

from config import GOOGLE_CREDENTIALS_FILE, GOOGLE_SHEET_ID


logger = logging.getLogger(__name__)


class SheetsService:
    """Read and write preorder information in Google Sheets."""

    ORDER_HEADERS = [
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
        "Collected At",
        "Collected By",
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

        except SpreadsheetNotFound as exc:
            raise RuntimeError(
                "Spreadsheet not found. Check GOOGLE_SHEET_ID and confirm "
                "the spreadsheet is shared with the service account."
            ) from exc

        except WorksheetNotFound as exc:
            raise RuntimeError(
                "The spreadsheet must contain Products, Preorders and "
                "Collected tabs."
            ) from exc

        except APIError as exc:
            raise RuntimeError(
                f"Google Sheets API error: {exc}"
            ) from exc

        self._validate_order_headers(self.preorders_sheet, "Preorders")
        self._validate_order_headers(self.collected_sheet, "Collected")

    def _validate_order_headers(
        self,
        worksheet: gspread.Worksheet,
        sheet_name: str,
    ) -> None:
        """Ensure an order worksheet has the required header columns."""

        headers = worksheet.row_values(1)
        missing = [
            header
            for header in self.ORDER_HEADERS
            if header not in headers
        ]

        if missing:
            raise RuntimeError(
                f"{sheet_name} sheet is missing required headers: "
                f"{', '.join(missing)}"
            )

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

            product = {
                "product_id": product_id,
                "product_name": product_name,
                "order_code": order_code,
                "trigger_phrase": order_code,
                "category": str(record.get("Category", "")).strip(),
                "stock": stock,
                "customer_limit": customer_limit,
                "preorders_open": preorders_open,
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
            "collected_by": str(
                record.get("Collected By", "")
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

        items = self._find_items_in_sheet(
            self.preorders_sheet,
            "Preorders",
            pin,
        )

        if not items:
            items = self._find_items_in_sheet(
                self.collected_sheet,
                "Collected",
                pin,
            )

        if not items:
            return None

        first = items[0]

        return {
            "pickup_pin": pin,
            "discord_username": first["discord_username"],
            "discord_user_id": first["discord_user_id"],
            "status": first["status"],
            "approved_by": first["approved_by"],
            "timestamp": first["timestamp"],
            "collected_at": first["collected_at"],
            "collected_by": first["collected_by"],
            "sheet_name": first["sheet_name"],
            "items": items,
            "total_quantity": sum(item["quantity"] for item in items),
        }

    def approval_already_processed(
        self,
        approval_message_id: int,
    ) -> bool:
        """Check both active and archived orders for an approval message."""

        target_id = str(approval_message_id)

        for worksheet in (
            self.preorders_sheet,
            self.collected_sheet,
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
        """Return the customer's approved and collected quantity."""

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
                    and status in {"approved", "collected"}
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
        """Archive all preorder rows sharing the supplied pickup PIN."""

        order = self.lookup_by_pin(pickup_pin)

        if order is None:
            raise ValueError("No preorder was found for that pickup PIN.")

        if order["sheet_name"] == "Collected":
            raise ValueError("This preorder has already been collected.")

        if str(order["status"]).strip().casefold() != "approved":
            raise ValueError(
                f"This preorder cannot be collected because its status is "
                f"'{order['status']}'."
            )

        collected_at = datetime.now(timezone.utc).isoformat()
        headers = self.preorders_sheet.row_values(1)
        collected_rows: list[list[Any]] = []

        for item in order["items"]:
            row_values = self.preorders_sheet.row_values(
                int(item["row_number"])
            )

            while len(row_values) < len(headers):
                row_values.append("")

            row_values[headers.index("Status")] = "Collected"
            row_values[headers.index("Collected At")] = collected_at
            row_values[headers.index("Collected By")] = collected_by
            collected_rows.append(row_values)

        appended_count = 0

        try:
            for row_values in collected_rows:
                self.collected_sheet.append_row(
                    row_values,
                    value_input_option="USER_ENTERED",
                )
                appended_count += 1

            for row_number in sorted(
                (
                    int(item["row_number"])
                    for item in order["items"]
                ),
                reverse=True,
            ):
                self.preorders_sheet.delete_rows(row_number)

        except Exception:
            for _ in range(appended_count):
                self.collected_sheet.delete_rows(
                    len(self.collected_sheet.get_all_values())
                )
            raise

        order["status"] = "Collected"
        order["collected_at"] = collected_at
        order["collected_by"] = collected_by
        order["sheet_name"] = "Collected"

        for item in order["items"]:
            item["status"] = "Collected"
            item["collected_at"] = collected_at
            item["collected_by"] = collected_by
            item["sheet_name"] = "Collected"

        return order

    def connection_status(self) -> dict[str, Any]:
        """Return basic spreadsheet connection information."""

        products = self.get_products()

        return {
            "title": self.spreadsheet.title,
            "product_count": len(products),
        }

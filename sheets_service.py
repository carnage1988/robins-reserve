import logging
from datetime import datetime, timezone
from typing import Any
import secrets

import gspread
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound

from config import GOOGLE_CREDENTIALS_FILE, GOOGLE_SHEET_ID


logger = logging.getLogger(__name__)


class SheetsService:
    """Read and write preorder information in Google Sheets."""

    def __init__(self) -> None:
        try:
            client = gspread.service_account(
                filename=str(GOOGLE_CREDENTIALS_FILE)
            )

            self.spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
            self.products_sheet = self.spreadsheet.worksheet("Products")
            self.preorders_sheet = self.spreadsheet.worksheet("Preorders")

        except SpreadsheetNotFound as exc:
            raise RuntimeError(
                "Spreadsheet not found. Check GOOGLE_SHEET_ID and confirm "
                "the spreadsheet is shared with the service account."
            ) from exc

        except WorksheetNotFound as exc:
            raise RuntimeError(
                "The spreadsheet must contain Products and Preorders tabs."
            ) from exc

        except APIError as exc:
            raise RuntimeError(
                f"Google Sheets API error: {exc}"
            ) from exc

    def get_products(self, open_only: bool = False) -> list[dict[str, Any]]:
        """Return valid products from the Products tab."""

        records = self.products_sheet.get_all_records()
        products: list[dict[str, Any]] = []

        for row_number, record in enumerate(records, start=2):
            product_id = str(record.get("Product ID", "")).strip()
            product_name = str(record.get("Product Name", "")).strip()

            if not product_id or not product_name:
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
                "trigger_phrase": str(
                    record.get("Trigger Phrase", "")
                ).strip(),
                "category": str(record.get("Category", "")).strip(),
                "stock": stock,
                "customer_limit": customer_limit,
                "preorders_open": preorders_open,
            }

            if open_only and not preorders_open:
                continue

            products.append(product)

        return products

    def find_product_by_trigger(
        self,
        message_content: str,
    ) -> dict[str, Any] | None:
        """Find an open product matching the customer's trigger phrase."""

        customer_message = message_content.strip().casefold()

        for product in self.get_products(open_only=True):
            trigger_phrase = product["trigger_phrase"].strip().casefold()

            if trigger_phrase and customer_message == trigger_phrase:
                return product

        return None

    def approval_already_processed(
        self,
        approval_message_id: int,
    ) -> bool:
        """Check whether a Discord approval message is already recorded."""

        target_id = str(approval_message_id)

        for record in self.preorders_sheet.get_all_records():
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
        """Return the customer's approved quantity for one product."""

        total = 0
        target_user_id = str(discord_user_id)

        for record in self.preorders_sheet.get_all_records():
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
                and status == "approved"
            ):
                try:
                    total += int(record.get("Quantity", 0))
                except (TypeError, ValueError):
                    logger.warning(
                        "Ignoring an invalid preorder quantity."
                    )

        return total

    def approve_preorder(
        self,
        *,
        discord_username: str,
        discord_user_id: int,
        product_id: str,
        approved_by: str,
        approval_message_id: int,
    ) -> dict[str, Any]:
        """Approve one preorder and deduct one unit of stock."""

        if self.approval_already_processed(approval_message_id):
            raise ValueError(
                "This approval has already been processed."
            )

        product = next(
            (
                item
                for item in self.get_products()
                if item["product_id"] == product_id
            ),
            None,
        )

        if product is None:
            raise ValueError("The product no longer exists.")

        if not product["preorders_open"]:
            raise ValueError(
                "Preorders are closed for this product."
            )

        if product["stock"] <= 0:
            raise ValueError(
                "This product is fully allocated."
            )

        current_total = self.get_customer_product_total(
            discord_user_id,
            product_id,
        )

        if current_total >= product["customer_limit"]:
            raise ValueError(
                "This customer has already reached the product limit."
            )

        headers = self.products_sheet.row_values(1)

        try:
            product_id_column = headers.index("Product ID") + 1
            stock_column = headers.index("Stock") + 1
        except ValueError as exc:
            raise RuntimeError(
                "Products sheet is missing Product ID or Stock headers."
            ) from exc

        product_cell = self.products_sheet.find(
            product_id,
            in_column=product_id_column,
        )

        if product_cell is None:
            raise ValueError(
                "The product row could not be found."
            )

        old_stock = product["stock"]
        new_stock = old_stock - 1

        pickup_pin = f"{secrets.randbelow(900000) + 100000}"

        self.products_sheet.update_cell(
            product_cell.row,
            stock_column,
            new_stock,
        )

        try:
            self.preorders_sheet.append_row(
                [
                    datetime.now(timezone.utc).isoformat(),
                    discord_username,
                    str(discord_user_id),
                    product["product_id"],
                    product["product_name"],
                    1,
                    "Approved",
                    approved_by,
                    str(approval_message_id),
		    pickup_pin,
                ],
                value_input_option="USER_ENTERED",
            )
        except Exception:
            # Restore the original stock if recording the order fails.
            self.products_sheet.update_cell(
                product_cell.row,
                stock_column,
                old_stock,
            )
            raise

        product["stock"] = new_stock
        product["pickup_pin"] = pickup_pin

        return product

    def connection_status(self) -> dict[str, Any]:
        """Return basic spreadsheet connection information."""

        products = self.get_products()

        return {
            "title": self.spreadsheet.title,
            "product_count": len(products),
        }

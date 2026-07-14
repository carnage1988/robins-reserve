import logging
from typing import Any

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
                "category": str(record.get("Category", "")).strip(),
                "stock": stock,
                "customer_limit": customer_limit,
                "preorders_open": preorders_open,
            }

            if open_only and not preorders_open:
                continue

            products.append(product)

        return products

    def connection_status(self) -> dict[str, Any]:
        """Return basic spreadsheet connection information."""

        products = self.get_products()

        return {
            "title": self.spreadsheet.title,
            "product_count": len(products),
        }

import logging
from typing import Any

import gspread
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound

from config import GOOGLE_CREDENTIALS_FILE, ROBINCON_SHEET_ID


logger = logging.getLogger(__name__)


class RobinConService:
    """Read RobinCon configuration and operational data from Google Sheets."""

    REQUIRED_WORKSHEETS = [
        "Configuration",
        "Ticket Types",
        "Orders",
        "Tickets",
        "Premium Events",
        "Event Registrations",
        "T-Shirt Sizes",
        "Audit Log",
    ]

    def __init__(self) -> None:
        if not ROBINCON_SHEET_ID:
            raise RuntimeError("ROBINCON_SHEET_ID is missing from .env")

        try:
            client = gspread.service_account(
                filename=str(GOOGLE_CREDENTIALS_FILE)
            )

            self.spreadsheet = client.open_by_key(ROBINCON_SHEET_ID)

            self.configuration_sheet = self.spreadsheet.worksheet(
                "Configuration"
            )
            self.ticket_types_sheet = self.spreadsheet.worksheet(
                "Ticket Types"
            )
            self.orders_sheet = self.spreadsheet.worksheet("Orders")
            self.tickets_sheet = self.spreadsheet.worksheet("Tickets")
            self.premium_events_sheet = self.spreadsheet.worksheet(
                "Premium Events"
            )
            self.event_registrations_sheet = self.spreadsheet.worksheet(
                "Event Registrations"
            )
            self.tshirt_sizes_sheet = self.spreadsheet.worksheet(
                "T-Shirt Sizes"
            )
            self.audit_log_sheet = self.spreadsheet.worksheet("Audit Log")

        except SpreadsheetNotFound as exc:
            raise RuntimeError(
                "The RobinCon spreadsheet could not be found."
            ) from exc

        except WorksheetNotFound as exc:
            raise RuntimeError(
                "The RobinCon spreadsheet is missing one or more required tabs."
            ) from exc

        except APIError as exc:
            raise RuntimeError(
                f"RobinCon Google Sheets API error: {exc}"
            ) from exc

        self._validate_headers()

    @staticmethod
    def _missing_headers(
        worksheet: gspread.Worksheet,
        required_headers: list[str],
    ) -> list[str]:
        headers = worksheet.row_values(1)

        return [
            header
            for header in required_headers
            if header not in headers
        ]

    def _validate_sheet_headers(
        self,
        worksheet: gspread.Worksheet,
        sheet_name: str,
        required_headers: list[str],
    ) -> None:
        missing = self._missing_headers(
            worksheet,
            required_headers,
        )

        if missing:
            raise RuntimeError(
                f"{sheet_name} is missing required headers: "
                f"{', '.join(missing)}"
            )

    def _validate_headers(self) -> None:
        self._validate_sheet_headers(
            self.configuration_sheet,
            "Configuration",
            ["Setting", "Value"],
        )

        self._validate_sheet_headers(
            self.ticket_types_sheet,
            "Ticket Types",
            [
                "Ticket Type",
                "Display Name",
                "Premium Event Allowance",
                "Includes T-Shirt",
                "Active",
            ],
        )

        self._validate_sheet_headers(
            self.orders_sheet,
            "Orders",
            [
                "Order Number",
                "Order Date",
                "Customer Name",
                "Customer Email",
                "Ticket Type",
                "Quantity",
                "Payment Status",
                "Import Source",
                "Import Timestamp",
                "Order Status",
            ],
        )

        self._validate_sheet_headers(
            self.tickets_sheet,
            "Tickets",
            [
                "Ticket ID",
                "Order Number",
                "Ticket Number",
                "Ticket Type",
                "Ticket Holder Name",
                "Ticket Holder Email",
                "Discord User ID",
                "Discord Username",
                "Premium Event Allowance",
                "T-Shirt Size",
                "Linked",
                "Linked At",
                "Checked In",
                "Checked In At",
                "QR Code",
                "Ticket Status",
                "Registration Complete",
            ],
        )

        self._validate_sheet_headers(
            self.premium_events_sheet,
            "Premium Events",
            [
                "Event ID",
                "Event Name",
                "Day",
                "Start Time",
                "End Time",
                "Capacity",
                "Registration Open",
                "Active",
                "Event Category",
                "Notes",
            ],
        )

        self._validate_sheet_headers(
            self.event_registrations_sheet,
            "Event Registrations",
            [
                "Registration ID",
                "Ticket ID",
                "Order Number",
                "Event ID",
                "Event Name",
                "Registration Number",
                "Registration Timestamp",
                "Registration Status",
                "Registered By",
                "Registered Discord ID",
            ],
        )

        self._validate_sheet_headers(
            self.tshirt_sizes_sheet,
            "T-Shirt Sizes",
            [
                "Size ID",
                "Display Name",
                "Sort Order",
                "Enabled",
            ],
        )

        self._validate_sheet_headers(
            self.audit_log_sheet,
            "Audit Log",
            [
                "Audit ID",
                "Timestamp",
                "Action",
                "Action Category",
                "Discord User ID",
                "Discord Username",
                "Staff User ID",
                "Staff Username",
                "Ticket ID",
                "Order Number",
                "Event ID",
                "Result",
                "Details",
                "IP / Source",
            ],
        )

    def get_configuration(self) -> dict[str, str]:
        """Return Configuration values keyed by setting name."""

        configuration: dict[str, str] = {}

        for record in self.configuration_sheet.get_all_records():
            setting = str(record.get("Setting", "")).strip()
            value = str(record.get("Value", "")).strip()

            if setting:
                configuration[setting] = value

        return configuration

    def get_ticket_types(self) -> list[dict[str, Any]]:
        """Return active RobinCon ticket types."""

        return [
            record
            for record in self.ticket_types_sheet.get_all_records()
            if str(record.get("Active", "")).strip().upper() == "TRUE"
        ]

    def get_enabled_tshirt_sizes(self) -> list[dict[str, Any]]:
        """Return enabled T-shirt sizes in display order."""

        sizes = [
            record
            for record in self.tshirt_sizes_sheet.get_all_records()
            if str(record.get("Enabled", "")).strip().upper() == "TRUE"
        ]

        return sorted(
            sizes,
            key=lambda record: int(record.get("Sort Order", 0) or 0),
        )

    def get_active_premium_events(self) -> list[dict[str, Any]]:
        """Return active premium events that are open for registration."""

        return [
            record
            for record in self.premium_events_sheet.get_all_records()
            if str(record.get("Active", "")).strip().upper() == "TRUE"
            and str(
                record.get("Registration Open", "")
            ).strip().upper() == "TRUE"
        ]

    def get_status(self) -> dict[str, Any]:
        """Return a basic RobinCon workbook health summary."""

        configuration = self.get_configuration()
        ticket_types = self.get_ticket_types()
        tshirt_sizes = self.get_enabled_tshirt_sizes()
        premium_events = self.get_active_premium_events()

        return {
            "connected": True,
            "robincon_name": configuration.get(
                "RobinCon Name",
                "Unknown",
            ),
            "configuration_count": len(configuration),
            "ticket_type_count": len(ticket_types),
            "tshirt_size_count": len(tshirt_sizes),
            "premium_event_count": len(premium_events),
        }

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

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
        "Premium Events Sat",
        "Premium Events Sun",
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
            self.premium_events_sat_sheet = self.spreadsheet.worksheet(
                "Premium Events Sat"
            )
            self.premium_events_sun_sheet = self.spreadsheet.worksheet(
                "Premium Events Sun"
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
                "Processed",
                "Processed Timestamp",
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
                "Saturday Event ID",
                "Saturday Event Name",
                "Sunday Event ID",
                "Sunday Event Name",
                "Registration Completed At",
            ],
        )

        self._validate_sheet_headers(
            self.premium_events_sat_sheet,
            "Premium Events Sat",
            [
                "Event ID",
                "Event Name",
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
            self.premium_events_sun_sheet,
            "Premium Events Sun",
            [
                "Event ID",
                "Event Name",
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

    def _get_active_events_from_sheet(
        self,
        worksheet: gspread.Worksheet,
        day: str,
    ) -> list[dict[str, Any]]:
        """Return active, open events from one day-specific worksheet."""

        events: list[dict[str, Any]] = []
        for record in worksheet.get_all_records():
            if str(record.get("Active", "")).strip().upper() != "TRUE":
                continue
            if (
                str(record.get("Registration Open", "")).strip().upper()
                != "TRUE"
            ):
                continue

            event = dict(record)
            event["Day"] = day
            events.append(event)

        return events

    def get_active_saturday_events(self) -> list[dict[str, Any]]:
        """Return active Saturday premium events open for registration."""

        return self._get_active_events_from_sheet(
            self.premium_events_sat_sheet,
            "Saturday",
        )

    def get_active_sunday_events(self) -> list[dict[str, Any]]:
        """Return active Sunday premium events open for registration."""

        return self._get_active_events_from_sheet(
            self.premium_events_sun_sheet,
            "Sunday",
        )

    def get_active_premium_events(self) -> list[dict[str, Any]]:
        """Return all active premium events, preserving day information."""

        return (
            self.get_active_saturday_events()
            + self.get_active_sunday_events()
        )

    def get_status(self) -> dict[str, Any]:
        """Return a basic RobinCon workbook health summary."""

        configuration = self.get_configuration()
        ticket_types = self.get_ticket_types()
        tshirt_sizes = self.get_enabled_tshirt_sizes()
        saturday_events = self.get_active_saturday_events()
        sunday_events = self.get_active_sunday_events()

        return {
            "connected": True,
            "robincon_name": configuration.get(
                "RobinCon Name",
                "Unknown",
            ),
            "configuration_count": len(configuration),
            "ticket_type_count": len(ticket_types),
            "tshirt_size_count": len(tshirt_sizes),
            "saturday_event_count": len(saturday_events),
            "sunday_event_count": len(sunday_events),
            "premium_event_count": len(saturday_events) + len(sunday_events),
        }

    @staticmethod
    def _is_true(value: Any) -> bool:
        return str(value or "").strip().upper() in {"TRUE", "YES", "Y", "1"}

    @staticmethod
    def _normalise_email(value: Any) -> str:
        return str(value or "").strip().casefold()

    @staticmethod
    def _normalise_order_number(value: Any) -> str:
        return str(value or "").strip().casefold()

    @staticmethod
    def _now() -> str:
        return datetime.now(ZoneInfo("Europe/London")).isoformat(
            timespec="seconds"
        )

    def is_ticket_linking_open(self) -> bool:
        """Return whether customers may currently link RobinCon tickets."""

        configuration = self.get_configuration()
        return self._is_true(configuration.get("Ticket Linking Open", "FALSE"))

    def find_order(
        self,
        order_number: str,
        customer_email: str,
    ) -> dict[str, Any] | None:
        """Find a valid paid order using its number and purchaser email."""

        wanted_order = self._normalise_order_number(order_number)
        wanted_email = self._normalise_email(customer_email)

        for row_number, record in enumerate(
            self.orders_sheet.get_all_records(),
            start=2,
        ):
            stored_order = self._normalise_order_number(
                record.get("Order Number", "")
            )
            stored_email = self._normalise_email(
                record.get("Customer Email", "")
            )

            if stored_order != wanted_order or stored_email != wanted_email:
                continue

            payment_status = str(
                record.get("Payment Status", "")
            ).strip().casefold()
            order_status = str(
                record.get("Order Status", "")
            ).strip().casefold()

            if payment_status not in {"paid", "completed", "complete"}:
                raise ValueError(
                    "This order is not marked as paid and cannot be linked."
                )

            if order_status in {"cancelled", "canceled", "refunded"}:
                raise ValueError(
                    "This order has been cancelled or refunded."
                )

            result = dict(record)
            result["row_number"] = row_number
            return result

        return None

    def get_ticket_type(self, ticket_type: str) -> dict[str, Any]:
        """Return one active ticket-type definition."""

        wanted = str(ticket_type or "").strip().casefold()

        for record in self.get_ticket_types():
            code = str(record.get("Ticket Type", "")).strip().casefold()
            display_name = str(
                record.get("Display Name", "")
            ).strip().casefold()

            if wanted in {code, display_name}:
                return record

        raise ValueError(
            f"Ticket type '{ticket_type}' is not configured as active."
        )

    def _next_ticket_sequence(self) -> int:
        highest = 0
        for record in self.tickets_sheet.get_all_records():
            ticket_id = str(record.get("Ticket ID", "")).strip()
            match = re.search(r"(\d+)$", ticket_id)
            if match:
                highest = max(highest, int(match.group(1)))
        return highest + 1

    def _ticket_year(self) -> str:
        configuration = self.get_configuration()
        year = str(
            configuration.get(
                "RobinCon Year",
                configuration.get("Event Year", datetime.now().year),
            )
        ).strip()
        return year[-2:] if len(year) >= 2 else year.zfill(2)

    def mark_order_processed(self, order: dict[str, Any]) -> str:
        """Mark an order as converted into individual ticket records."""

        order_number = str(order.get("Order Number", "")).strip()
        if not order_number:
            raise ValueError("The order has no order number.")

        row_number = int(order.get("row_number", 0) or 0)
        if row_number < 2:
            wanted = self._normalise_order_number(order_number)
            for candidate_row, record in enumerate(
                self.orders_sheet.get_all_records(),
                start=2,
            ):
                if self._normalise_order_number(
                    record.get("Order Number", "")
                ) == wanted:
                    row_number = candidate_row
                    break

        if row_number < 2:
            raise ValueError("The order row could not be found.")

        headers = self.orders_sheet.row_values(1)
        try:
            processed_col = headers.index("Processed") + 1
            timestamp_col = headers.index("Processed Timestamp") + 1
        except ValueError as exc:
            raise RuntimeError(
                "Orders sheet is missing processing workflow headers."
            ) from exc

        processed_at = self._now()
        self.orders_sheet.update_cell(row_number, processed_col, "TRUE")
        self.orders_sheet.update_cell(row_number, timestamp_col, processed_at)
        order["Processed"] = "TRUE"
        order["Processed Timestamp"] = processed_at
        order["row_number"] = row_number
        return processed_at

    def ensure_order_tickets(
        self,
        order: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Create any missing ticket rows represented by an order."""

        order_number = str(order.get("Order Number", "")).strip()
        if not order_number:
            raise ValueError("The order has no order number.")

        try:
            quantity = int(order.get("Quantity", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("The order quantity is invalid.") from exc

        if quantity < 1:
            raise ValueError("The order does not contain any tickets.")

        ticket_type = self.get_ticket_type(
            str(order.get("Ticket Type", ""))
        )
        ticket_code = str(ticket_type.get("Ticket Type", "")).strip()
        allowance = int(
            ticket_type.get("Premium Event Allowance", 0) or 0
        )

        existing = [
            record
            for record in self.tickets_sheet.get_all_records()
            if self._normalise_order_number(
                record.get("Order Number", "")
            ) == self._normalise_order_number(order_number)
        ]
        existing_numbers = {
            int(record.get("Ticket Number", 0) or 0)
            for record in existing
            if str(record.get("Ticket Number", "")).strip().isdigit()
        }

        sequence = self._next_ticket_sequence()
        year = self._ticket_year()
        headers = self.tickets_sheet.row_values(1)

        for ticket_number in range(1, quantity + 1):
            if ticket_number in existing_numbers:
                continue

            ticket_id = f"RC{year}-{sequence:06d}"
            sequence += 1
            ticket_record = {
                "Ticket ID": ticket_id,
                "Order Number": order_number,
                "Ticket Number": ticket_number,
                "Ticket Type": ticket_code,
                "Ticket Holder Name": "",
                "Ticket Holder Email": "",
                "Discord User ID": "",
                "Discord Username": "",
                "Premium Event Allowance": allowance,
                "T-Shirt Size": "",
                "Linked": "FALSE",
                "Linked At": "",
                "Checked In": "FALSE",
                "Checked In At": "",
                "QR Code": ticket_id,
                "Ticket Status": "Active",
                "Registration Complete": "FALSE",
            }
            self.tickets_sheet.append_row(
                [ticket_record.get(header, "") for header in headers],
                value_input_option="USER_ENTERED",
            )
            existing.append(ticket_record)

        ticket_numbers = {
            int(record.get("Ticket Number", 0) or 0)
            for record in existing
            if str(record.get("Ticket Number", "")).strip().isdigit()
        }
        expected_numbers = set(range(1, quantity + 1))
        if not expected_numbers.issubset(ticket_numbers):
            raise RuntimeError(
                "Not all ticket rows could be created for this order."
            )

        if not self._is_true(order.get("Processed", "")):
            self.mark_order_processed(order)

        return existing

    def is_tshirt_selection_open(self) -> bool:
        """Return whether customers may currently select T-shirt sizes."""

        configuration = self.get_configuration()
        return self._is_true(
            configuration.get("T-Shirt Selection Open", "FALSE")
        )

    def get_linked_tickets_with_rows(
        self,
        discord_user_id: int,
    ) -> list[dict[str, Any]]:
        """Return all active tickets managed by a Discord account."""

        wanted_id = str(discord_user_id)
        tickets: list[dict[str, Any]] = []
        for row_number, record in enumerate(
            self.tickets_sheet.get_all_records(),
            start=2,
        ):
            if str(record.get("Discord User ID", "")).strip() != wanted_id:
                continue
            if not self._is_true(record.get("Linked", "")):
                continue
            if str(record.get("Ticket Status", "")).strip().casefold() in {
                "cancelled",
                "canceled",
                "refunded",
            }:
                continue
            ticket = dict(record)
            ticket["row_number"] = row_number
            tickets.append(ticket)

        return sorted(
            tickets,
            key=lambda item: (
                self._normalise_order_number(item.get("Order Number", "")),
                int(item.get("Ticket Number", 0) or 0),
            ),
        )

    def get_linked_ticket_with_row(
        self,
        discord_user_id: int,
        ticket_id: str = "",
    ) -> dict[str, Any] | None:
        """Return one active managed ticket, optionally selected by ticket ID."""

        tickets = self.get_linked_tickets_with_rows(discord_user_id)
        wanted_ticket = str(ticket_id or "").strip().casefold()
        if wanted_ticket:
            for ticket in tickets:
                if str(ticket.get("Ticket ID", "")).strip().casefold() == wanted_ticket:
                    return ticket
            return None
        return tickets[0] if tickets else None

    def select_tshirt_size(
        self,
        *,
        discord_user_id: int,
        discord_username: str,
        size_id: str,
    ) -> dict[str, Any]:
        """Save an enabled T-shirt size against a linked ticket."""

        if not self.is_tshirt_selection_open():
            raise ValueError("RobinCon T-shirt selection is currently closed.")

        selected_size: dict[str, Any] | None = None
        wanted_size = str(size_id or "").strip().casefold()
        for size in self.get_enabled_tshirt_sizes():
            code = str(size.get("Size ID", "")).strip().casefold()
            display = str(size.get("Display Name", "")).strip().casefold()
            if wanted_size in {code, display}:
                selected_size = size
                break

        if selected_size is None:
            raise ValueError("That T-shirt size is not currently available.")

        ticket = self.get_linked_ticket_with_row(discord_user_id)
        if ticket is None:
            raise ValueError(
                "You must link a RobinCon ticket before selecting a T-shirt size."
            )

        row_number = int(ticket["row_number"])
        headers = self.tickets_sheet.row_values(1)
        try:
            size_col = headers.index("T-Shirt Size") + 1
            complete_col = headers.index("Registration Complete") + 1
        except ValueError as exc:
            raise RuntimeError(
                "Tickets sheet is missing registration workflow headers."
            ) from exc

        display_name = str(
            selected_size.get("Display Name", selected_size.get("Size ID", ""))
        ).strip()
        self.tickets_sheet.update_cell(row_number, size_col, display_name)
        self.tickets_sheet.update_cell(row_number, complete_col, "FALSE")

        ticket["T-Shirt Size"] = display_name
        ticket["Registration Complete"] = "FALSE"
        self.write_audit_log(
            action="T-Shirt Selected",
            action_category="Customer",
            discord_user_id=discord_user_id,
            discord_username=discord_username,
            ticket_id=str(ticket.get("Ticket ID", "")),
            order_number=str(ticket.get("Order Number", "")),
            result="Success",
            details=f"T-shirt size selected: {display_name}.",
            source="Discord",
        )
        return ticket

    def is_premium_event_registration_open(self) -> bool:
        """Return whether customers may currently register for premium events."""

        configuration = self.get_configuration()
        return self._is_true(
            configuration.get("Premium Event Registration Open", "FALSE")
        )

    def _get_event_by_id(self, event_id: str, day: str) -> dict[str, Any]:
        """Return one active event from the requested day."""

        wanted = str(event_id or "").strip().casefold()
        events = (
            self.get_active_saturday_events()
            if day == "Saturday"
            else self.get_active_sunday_events()
        )
        for event in events:
            if str(event.get("Event ID", "")).strip().casefold() == wanted:
                return event
        raise ValueError(f"That {day} premium event is not currently available.")

    def _event_registration_count(self, event_id: str) -> int:
        """Count active registrations for an event."""

        wanted = str(event_id or "").strip().casefold()
        return sum(
            1
            for record in self.event_registrations_sheet.get_all_records()
            if str(record.get("Event ID", "")).strip().casefold() == wanted
            and str(record.get("Registration Status", "")).strip().casefold()
            not in {"cancelled", "canceled", "refunded"}
        )

    def _next_registration_id(self) -> int:
        highest = 0
        for record in self.event_registrations_sheet.get_all_records():
            registration_id = str(record.get("Registration ID", "")).strip()
            match = re.search(r"(\d+)$", registration_id)
            if match:
                highest = max(highest, int(match.group(1)))
        return highest + 1

    def complete_registration(
        self,
        *,
        discord_user_id: int,
        discord_username: str,
        tshirt_size_id: str,
        saturday_event_id: str,
        sunday_event_id: str,
        ticket_id: str = "",
    ) -> dict[str, Any]:
        """Save and permanently lock a complete RobinCon registration."""

        if not self.is_tshirt_selection_open():
            raise ValueError("RobinCon T-shirt selection is currently closed.")
        if not self.is_premium_event_registration_open():
            raise ValueError("RobinCon premium-event registration is currently closed.")

        ticket = self.get_linked_ticket_with_row(discord_user_id, ticket_id)
        if ticket is None:
            raise ValueError(
                "That ticket is not linked to your Discord account or is not active."
            )
        if self._is_true(ticket.get("Registration Complete", "")):
            raise ValueError("Your RobinCon registration is already complete and locked.")

        wanted_size = str(tshirt_size_id or "").strip().casefold()
        selected_size = None
        for size in self.get_enabled_tshirt_sizes():
            code = str(size.get("Size ID", "")).strip().casefold()
            display = str(size.get("Display Name", "")).strip().casefold()
            if wanted_size in {code, display}:
                selected_size = size
                break
        if selected_size is None:
            raise ValueError("That T-shirt size is not currently available.")

        saturday_event = self._get_event_by_id(saturday_event_id, "Saturday")
        sunday_event = self._get_event_by_id(sunday_event_id, "Sunday")

        existing_regs = [
            record
            for record in self.event_registrations_sheet.get_all_records()
            if str(record.get("Ticket ID", "")).strip()
            == str(ticket.get("Ticket ID", "")).strip()
            and str(record.get("Registration Status", "")).strip().casefold()
            not in {"cancelled", "canceled", "refunded"}
        ]
        if existing_regs:
            raise ValueError(
                "This ticket already has premium-event registrations. Please contact staff."
            )

        for event in (saturday_event, sunday_event):
            try:
                capacity = int(event.get("Capacity", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Capacity is invalid for {event.get('Event Name', 'an event')}."
                ) from exc
            if capacity < 1:
                raise ValueError(
                    f"{event.get('Event Name', 'That event')} has no available capacity."
                )
            if self._event_registration_count(str(event.get("Event ID", ""))) >= capacity:
                raise ValueError(
                    f"{event.get('Event Name', 'That event')} is now fully booked."
                )

        registration_headers = self.event_registrations_sheet.row_values(1)
        start_row_count = self.event_registrations_sheet.row_count
        sequence = self._next_registration_id()
        now = self._now()
        appended = 0
        try:
            for event in (saturday_event, sunday_event):
                event_id = str(event.get("Event ID", "")).strip()
                registration_number = self._event_registration_count(event_id) + 1
                record = {
                    "Registration ID": f"REG-{sequence:06d}",
                    "Ticket ID": str(ticket.get("Ticket ID", "")),
                    "Order Number": str(ticket.get("Order Number", "")),
                    "Event ID": event_id,
                    "Event Name": str(event.get("Event Name", "")).strip(),
                    "Registration Number": registration_number,
                    "Registration Timestamp": now,
                    "Registration Status": "Confirmed",
                    "Registered By": "Customer",
                    "Registered Discord ID": str(discord_user_id),
                }
                self.event_registrations_sheet.append_row(
                    [record.get(header, "") for header in registration_headers],
                    value_input_option="USER_ENTERED",
                )
                appended += 1
                sequence += 1

            headers = self.tickets_sheet.row_values(1)
            columns = {header: headers.index(header) + 1 for header in (
                "T-Shirt Size",
                "Saturday Event ID",
                "Saturday Event Name",
                "Sunday Event ID",
                "Sunday Event Name",
                "Registration Complete",
                "Registration Completed At",
            )}
            row_number = int(ticket["row_number"])
            updates = {
                "T-Shirt Size": str(selected_size.get("Display Name", selected_size.get("Size ID", ""))).strip(),
                "Saturday Event ID": str(saturday_event.get("Event ID", "")).strip(),
                "Saturday Event Name": str(saturday_event.get("Event Name", "")).strip(),
                "Sunday Event ID": str(sunday_event.get("Event ID", "")).strip(),
                "Sunday Event Name": str(sunday_event.get("Event Name", "")).strip(),
                "Registration Complete": "TRUE",
                "Registration Completed At": now,
            }
            for header, value in updates.items():
                self.tickets_sheet.update_cell(row_number, columns[header], value)
        except Exception:
            if appended:
                current_rows = len(self.event_registrations_sheet.get_all_values())
                first_appended = current_rows - appended + 1
                self.event_registrations_sheet.delete_rows(first_appended, current_rows)
            raise

        ticket.update(updates)
        for action, event in (
            ("Saturday Event Selected", saturday_event),
            ("Sunday Event Selected", sunday_event),
        ):
            self.write_audit_log(
                action=action,
                action_category="Customer",
                discord_user_id=discord_user_id,
                discord_username=discord_username,
                ticket_id=str(ticket.get("Ticket ID", "")),
                order_number=str(ticket.get("Order Number", "")),
                event_id=str(event.get("Event ID", "")),
                result="Success",
                details=f"Premium event selected: {event.get('Event Name', '')}.",
                source="Discord",
            )
        self.write_audit_log(
            action="Registration Completed",
            action_category="Customer",
            discord_user_id=discord_user_id,
            discord_username=discord_username,
            ticket_id=str(ticket.get("Ticket ID", "")),
            order_number=str(ticket.get("Order Number", "")),
            result="Success",
            details=(
                f"Registration locked with T-shirt {updates['T-Shirt Size']}, "
                f"Saturday {updates['Saturday Event Name']}, and "
                f"Sunday {updates['Sunday Event Name']}."
            ),
            source="Discord",
        )
        return ticket

    def get_linked_ticket_for_user(
        self,
        discord_user_id: int,
    ) -> dict[str, Any] | None:
        """Return the first active ticket managed by a Discord account."""

        tickets = self.get_linked_tickets_with_rows(discord_user_id)
        return tickets[0] if tickets else None

    def get_available_tickets_for_order(
        self,
        order_number: str,
    ) -> list[dict[str, Any]]:
        """Return active, unlinked tickets for an order."""

        wanted = self._normalise_order_number(order_number)
        available: list[dict[str, Any]] = []

        for row_number, record in enumerate(
            self.tickets_sheet.get_all_records(),
            start=2,
        ):
            if self._normalise_order_number(
                record.get("Order Number", "")
            ) != wanted:
                continue
            if self._is_true(record.get("Linked", "")):
                continue
            if str(record.get("Ticket Status", "")).strip().casefold() not in {
                "active",
                "new",
                "",
            }:
                continue

            ticket = dict(record)
            ticket["row_number"] = row_number
            available.append(ticket)

        return sorted(
            available,
            key=lambda item: int(item.get("Ticket Number", 0) or 0),
        )

    def prepare_ticket_link(
        self,
        *,
        order_number: str,
        customer_email: str,
        discord_user_id: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Validate an order and return its currently linkable tickets."""

        if not self.is_ticket_linking_open():
            raise ValueError("RobinCon ticket linking is currently closed.")

        order = self.find_order(order_number, customer_email)
        if order is None:
            raise ValueError(
                "No paid RobinCon order matched that order number and email."
            )

        self.ensure_order_tickets(order)
        available = self.get_available_tickets_for_order(
            str(order.get("Order Number", ""))
        )

        if not available:
            raise ValueError(
                "All tickets on this order have already been linked."
            )

        return order, available

    def link_ticket(
        self,
        *,
        ticket_id: str,
        discord_user_id: int,
        discord_username: str,
        holder_name: str,
        holder_email: str,
    ) -> dict[str, Any]:
        """Link one unclaimed ticket to a Discord account."""

        headers = self.tickets_sheet.row_values(1)
        try:
            column = {
                header: headers.index(header) + 1
                for header in (
                    "Ticket Holder Name",
                    "Ticket Holder Email",
                    "Discord User ID",
                    "Discord Username",
                    "Linked",
                    "Linked At",
                )
            }
        except ValueError as exc:
            raise RuntimeError(
                "Tickets sheet is missing linking workflow headers."
            ) from exc

        selected: dict[str, Any] | None = None
        selected_row = 0
        for row_number, record in enumerate(
            self.tickets_sheet.get_all_records(),
            start=2,
        ):
            if str(record.get("Ticket ID", "")).strip() != ticket_id.strip():
                continue
            selected = record
            selected_row = row_number
            break

        if selected is None:
            raise ValueError("The selected ticket could not be found.")
        if self._is_true(selected.get("Linked", "")):
            raise ValueError("That ticket has already been linked.")
        if str(selected.get("Ticket Status", "")).strip().casefold() not in {
            "active",
            "new",
            "",
        }:
            raise ValueError("That ticket is not active.")

        linked_at = self._now()
        updates = {
            "Ticket Holder Name": holder_name.strip(),
            "Ticket Holder Email": holder_email.strip(),
            "Discord User ID": str(discord_user_id),
            "Discord Username": discord_username,
            "Linked": "TRUE",
            "Linked At": linked_at,
        }
        for header, value in updates.items():
            self.tickets_sheet.update_cell(
                selected_row,
                column[header],
                value,
            )

        result = dict(selected)
        result.update(updates)
        self.write_audit_log(
            action="Ticket Linked",
            action_category="Customer",
            discord_user_id=discord_user_id,
            discord_username=discord_username,
            ticket_id=str(result.get("Ticket ID", "")),
            order_number=str(result.get("Order Number", "")),
            result="Success",
            details=(
                "Discord account assigned as ticket manager for attendee "
                f"{updates['Ticket Holder Name'] or 'Unknown'}."
            ),
            source="Discord",
        )
        return result

    def _next_audit_id(self) -> str:
        highest = 0
        for record in self.audit_log_sheet.get_all_records():
            audit_id = str(record.get("Audit ID", "")).strip()
            match = re.search(r"(\d+)$", audit_id)
            if match:
                highest = max(highest, int(match.group(1)))
        return f"AUD-{highest + 1:06d}"

    def write_audit_log(
        self,
        *,
        action: str,
        action_category: str,
        discord_user_id: int | str = "",
        discord_username: str = "",
        staff_user_id: int | str = "",
        staff_username: str = "",
        ticket_id: str = "",
        order_number: str = "",
        event_id: str = "",
        result: str = "Success",
        details: str = "",
        source: str = "System",
    ) -> None:
        """Append one structured RobinCon audit-log entry."""

        headers = self.audit_log_sheet.row_values(1)
        record = {
            "Audit ID": self._next_audit_id(),
            "Timestamp": self._now(),
            "Action": action,
            "Action Category": action_category,
            "Discord User ID": str(discord_user_id),
            "Discord Username": discord_username,
            "Staff User ID": str(staff_user_id),
            "Staff Username": staff_username,
            "Ticket ID": ticket_id,
            "Order Number": order_number,
            "Event ID": event_id,
            "Result": result,
            "Details": details,
            "IP / Source": source,
        }
        self.audit_log_sheet.append_row(
            [record.get(header, "") for header in headers],
            value_input_option="USER_ENTERED",
        )


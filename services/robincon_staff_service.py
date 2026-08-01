from __future__ import annotations

from collections import Counter
from typing import Any

import gspread

from services.robincon_service import RobinConService


_ACTIVE_REGISTRATION_STATUSES = {"confirmed", "active", "registered"}
_INACTIVE_REGISTRATION_STATUSES = {"cancelled", "canceled", "refunded"}


class RobinConStaffService:
    """Staff-facing RobinCon queries and audited administrative actions."""

    def __init__(self, service: RobinConService) -> None:
        self.service = service

    def tickets(self) -> list[dict[str, Any]]:
        return self.service.tickets_sheet.get_all_records()

    def orders(self) -> list[dict[str, Any]]:
        return self.service.orders_sheet.get_all_records()

    def find(self, query: str) -> list[dict[str, Any]]:
        wanted = query.strip().casefold()
        if not wanted:
            return []
        fields = (
            "Ticket ID",
            "Order Number",
            "Ticket Holder Name",
            "Ticket Holder Email",
            "Discord Username",
            "Discord User ID",
        )
        return [
            record
            for record in self.tickets()
            if any(
                wanted in str(record.get(field, "")).casefold()
                for field in fields
            )
        ][:20]

    def ticket(self, ticket_id: str) -> dict[str, Any] | None:
        wanted = ticket_id.strip().casefold()
        return next(
            (
                record
                for record in self.tickets()
                if str(record.get("Ticket ID", "")).strip().casefold()
                == wanted
            ),
            None,
        )

    def order(self, order_number: str) -> list[dict[str, Any]]:
        wanted = order_number.strip().casefold()
        return [
            record
            for record in self.tickets()
            if str(record.get("Order Number", "")).strip().casefold()
            == wanted
        ]

    def tshirt_counts(self) -> Counter[str]:
        return Counter(
            str(record.get("T-Shirt Size", "")).strip()
            for record in self.tickets()
            if str(record.get("T-Shirt Size", "")).strip()
        )

    def capacity(self) -> dict[str, list[dict[str, Any]]]:
        registrations = self.service.event_registrations_sheet.get_all_records()
        counts = Counter(
            str(record.get("Event ID", "")).strip()
            for record in registrations
            if str(record.get("Registration Status", "")).strip().casefold()
            in _ACTIVE_REGISTRATION_STATUSES
        )
        result: dict[str, list[dict[str, Any]]] = {}
        for day, events in (
            ("Saturday", self.service.get_active_saturday_events()),
            ("Sunday", self.service.get_active_sunday_events()),
        ):
            result[day] = [
                {
                    **event,
                    "Registered": counts[
                        str(event.get("Event ID", "")).strip()
                    ],
                }
                for event in events
            ]
        return result

    def attendees(self, day: str) -> list[dict[str, Any]]:
        field = (
            "Saturday Event Name"
            if day.casefold().startswith("sat")
            else "Sunday Event Name"
        )
        return [
            record
            for record in self.tickets()
            if str(record.get(field, "")).strip()
        ]

    def summary(self) -> dict[str, Any]:
        tickets = self.tickets()
        orders = self.orders()
        capacities = self.capacity()

        def truthy(record: dict[str, Any], field: str) -> bool:
            return self.service._is_true(record.get(field, ""))

        unique_orders = {
            str(record.get("Order Number", "")).strip()
            for record in tickets
            if str(record.get("Order Number", "")).strip()
        }
        if not unique_orders:
            unique_orders = {
                str(record.get("Order Number", "")).strip()
                for record in orders
                if str(record.get("Order Number", "")).strip()
            }

        day_totals: dict[str, dict[str, int]] = {}
        for day, events in capacities.items():
            day_totals[day] = {
                "registered": sum(int(event.get("Registered", 0) or 0) for event in events),
                "capacity": sum(int(event.get("Capacity", 0) or 0) for event in events),
            }

        return {
            "orders": len(unique_orders),
            "tickets": len(tickets),
            "linked": sum(truthy(record, "Linked") for record in tickets),
            "registered": sum(
                truthy(record, "Registration Complete") for record in tickets
            ),
            "checked_in": sum(truthy(record, "Checked In") for record in tickets),
            "unclaimed": sum(
                not truthy(record, "Linked") for record in tickets
            ),
            "days": day_totals,
            "tshirts": self.tshirt_counts(),
        }

    def _find_ticket_with_row(
        self,
        ticket_id: str,
    ) -> tuple[int, dict[str, Any]]:
        wanted = ticket_id.strip().casefold()
        for row_number, record in enumerate(
            self.service.tickets_sheet.get_all_records(),
            start=2,
        ):
            if (
                str(record.get("Ticket ID", "")).strip().casefold()
                == wanted
            ):
                return row_number, record
        raise ValueError("No RobinCon ticket was found with that ID.")

    @staticmethod
    def _column_map(
        worksheet: gspread.Worksheet,
        required: tuple[str, ...],
    ) -> dict[str, int]:
        headers = worksheet.row_values(1)
        try:
            return {header: headers.index(header) + 1 for header in required}
        except ValueError as exc:
            raise RuntimeError(
                "A RobinCon worksheet is missing required staff-workflow headers."
            ) from exc

    def check_in(
        self,
        ticket_id: str,
        staff_id: int,
        staff_name: str,
    ) -> dict[str, Any]:
        row_number, record = self._find_ticket_with_row(ticket_id)
        if self.service._is_true(record.get("Checked In", "")):
            raise ValueError("This ticket is already checked in.")

        columns = self._column_map(
            self.service.tickets_sheet,
            ("Checked In", "Checked In At"),
        )
        checked_in_at = self.service._now()
        self.service.tickets_sheet.batch_update(
            [
                {
                    "range": gspread.utils.rowcol_to_a1(
                        row_number, columns["Checked In"]
                    ),
                    "values": [["TRUE"]],
                },
                {
                    "range": gspread.utils.rowcol_to_a1(
                        row_number, columns["Checked In At"]
                    ),
                    "values": [[checked_in_at]],
                },
            ]
        )
        self.service.write_audit_log(
            action="Ticket Checked In",
            action_category="Staff",
            staff_user_id=staff_id,
            staff_username=staff_name,
            ticket_id=str(record.get("Ticket ID", "")),
            order_number=str(record.get("Order Number", "")),
            result="Success",
            details="Ticket checked in manually by staff.",
            source="Discord",
        )
        result = dict(record)
        result["Checked In"] = "TRUE"
        result["Checked In At"] = checked_in_at
        return result

    def uncheck_in(
        self,
        ticket_id: str,
        staff_id: int,
        staff_name: str,
    ) -> dict[str, Any]:
        row_number, record = self._find_ticket_with_row(ticket_id)
        if not self.service._is_true(record.get("Checked In", "")):
            raise ValueError("This ticket is not currently checked in.")

        columns = self._column_map(
            self.service.tickets_sheet,
            ("Checked In", "Checked In At"),
        )
        previous_time = str(record.get("Checked In At", "")).strip()
        self.service.tickets_sheet.batch_update(
            [
                {
                    "range": gspread.utils.rowcol_to_a1(
                        row_number, columns["Checked In"]
                    ),
                    "values": [["FALSE"]],
                },
                {
                    "range": gspread.utils.rowcol_to_a1(
                        row_number, columns["Checked In At"]
                    ),
                    "values": [[""]],
                },
            ]
        )
        self.service.write_audit_log(
            action="Ticket Check-In Reversed",
            action_category="Staff",
            staff_user_id=staff_id,
            staff_username=staff_name,
            ticket_id=str(record.get("Ticket ID", "")),
            order_number=str(record.get("Order Number", "")),
            result="Success",
            details=(
                "Manual check-in was reversed by staff. "
                f"Previous check-in time: {previous_time or 'Unknown'}."
            ),
            source="Discord",
        )
        result = dict(record)
        result["Checked In"] = "FALSE"
        result["Checked In At"] = ""
        return result

    def edit_ticket(
        self,
        *,
        ticket_id: str,
        field: str,
        value: str,
        staff_id: int,
        staff_name: str,
    ) -> dict[str, Any]:
        row_number, record = self._find_ticket_with_row(ticket_id)
        clean_field = field.strip().casefold()
        clean_value = value.strip()
        if not clean_value:
            raise ValueError("The replacement value cannot be blank.")

        field_map = {
            "attendee": "Ticket Holder Name",
            "attendee-name": "Ticket Holder Name",
            "name": "Ticket Holder Name",
            "tshirt": "T-Shirt Size",
            "t-shirt": "T-Shirt Size",
            "saturday": "Saturday Event ID",
            "sunday": "Sunday Event ID",
        }
        target = field_map.get(clean_field)
        if target is None:
            raise ValueError("That ticket field cannot be edited.")

        ticket_updates: dict[str, str]
        registration_day = ""
        event: dict[str, Any] | None = None

        if target == "Ticket Holder Name":
            ticket_updates = {target: clean_value}
        elif target == "T-Shirt Size":
            wanted = clean_value.casefold()
            selected = next(
                (
                    size
                    for size in self.service.get_enabled_tshirt_sizes()
                    if wanted
                    in {
                        str(size.get("Size ID", "")).strip().casefold(),
                        str(size.get("Display Name", "")).strip().casefold(),
                    }
                ),
                None,
            )
            if selected is None:
                raise ValueError("That T-shirt size is not currently enabled.")
            ticket_updates = {
                target: str(
                    selected.get("Display Name", selected.get("Size ID", ""))
                ).strip()
            }
        else:
            registration_day = "Saturday" if clean_field == "saturday" else "Sunday"
            event = self._find_event(clean_value, registration_day)
            event_id = str(event.get("Event ID", "")).strip()
            event_name = str(event.get("Event Name", "")).strip()
            old_event_id = str(
                record.get(f"{registration_day} Event ID", "")
            ).strip()

            if event_id.casefold() != old_event_id.casefold():
                try:
                    capacity = int(event.get("Capacity", 0) or 0)
                except (TypeError, ValueError) as exc:
                    raise ValueError("The selected event has an invalid capacity.") from exc
                if capacity < 1:
                    raise ValueError("The selected event has no available capacity.")
                if self.service._event_registration_count(event_id) >= capacity:
                    raise ValueError(f"{event_name} is fully booked.")

            ticket_updates = {
                f"{registration_day} Event ID": event_id,
                f"{registration_day} Event Name": event_name,
            }

        columns = self._column_map(
            self.service.tickets_sheet,
            tuple(ticket_updates),
        )
        ticket_batch = [
            {
                "range": gspread.utils.rowcol_to_a1(
                    row_number, columns[header]
                ),
                "values": [[new_value]],
            }
            for header, new_value in ticket_updates.items()
        ]
        self.service.tickets_sheet.batch_update(ticket_batch)

        if event is not None and self.service._is_true(
            record.get("Registration Complete", "")
        ):
            try:
                self._update_event_registration(
                    ticket_id=str(record.get("Ticket ID", "")),
                    old_event_id=str(
                        record.get(f"{registration_day} Event ID", "")
                    ).strip(),
                    new_event=event,
                    staff_id=staff_id,
                )
            except Exception:
                rollback = [
                    {
                        "range": gspread.utils.rowcol_to_a1(
                            row_number, columns[header]
                        ),
                        "values": [[str(record.get(header, "") or "")]],
                    }
                    for header in ticket_updates
                ]
                self.service.tickets_sheet.batch_update(rollback)
                raise

        old_values = {
            header: str(record.get(header, "") or "")
            for header in ticket_updates
        }
        result = dict(record)
        result.update(ticket_updates)
        self.service.write_audit_log(
            action="Ticket Updated by Staff",
            action_category="Staff",
            staff_user_id=staff_id,
            staff_username=staff_name,
            ticket_id=str(record.get("Ticket ID", "")),
            order_number=str(record.get("Order Number", "")),
            event_id=(
                str(event.get("Event ID", "")) if event is not None else ""
            ),
            result="Success",
            details=(
                f"Changed {', '.join(ticket_updates)} from {old_values} "
                f"to {ticket_updates}."
            ),
            source="Discord",
        )
        return result

    def _find_event(self, value: str, day: str) -> dict[str, Any]:
        wanted = value.strip().casefold()
        events = (
            self.service.get_active_saturday_events()
            if day == "Saturday"
            else self.service.get_active_sunday_events()
        )
        matches = [
            event
            for event in events
            if wanted
            in {
                str(event.get("Event ID", "")).strip().casefold(),
                str(event.get("Event Name", "")).strip().casefold(),
            }
        ]
        if not matches:
            raise ValueError(
                f"No active {day} event matched that event ID or exact name."
            )
        return matches[0]

    def _update_event_registration(
        self,
        *,
        ticket_id: str,
        old_event_id: str,
        new_event: dict[str, Any],
        staff_id: int,
    ) -> None:
        worksheet = self.service.event_registrations_sheet
        columns = self._column_map(
            worksheet,
            (
                "Event ID",
                "Event Name",
                "Registered By",
                "Registered Discord ID",
            ),
        )
        wanted_ticket = ticket_id.strip().casefold()
        wanted_old = old_event_id.strip().casefold()
        for row_number, registration in enumerate(
            worksheet.get_all_records(),
            start=2,
        ):
            if (
                str(registration.get("Ticket ID", "")).strip().casefold()
                != wanted_ticket
            ):
                continue
            if (
                str(registration.get("Event ID", "")).strip().casefold()
                != wanted_old
            ):
                continue
            if (
                str(registration.get("Registration Status", ""))
                .strip()
                .casefold()
                in _INACTIVE_REGISTRATION_STATUSES
            ):
                continue
            worksheet.batch_update(
                [
                    {
                        "range": gspread.utils.rowcol_to_a1(
                            row_number, columns["Event ID"]
                        ),
                        "values": [[str(new_event.get("Event ID", "")).strip()]],
                    },
                    {
                        "range": gspread.utils.rowcol_to_a1(
                            row_number, columns["Event Name"]
                        ),
                        "values": [[str(new_event.get("Event Name", "")).strip()]],
                    },
                    {
                        "range": gspread.utils.rowcol_to_a1(
                            row_number, columns["Registered By"]
                        ),
                        "values": [["Staff Override"]],
                    },
                    {
                        "range": gspread.utils.rowcol_to_a1(
                            row_number, columns["Registered Discord ID"]
                        ),
                        "values": [[str(staff_id)]],
                    },
                ]
            )
            return
        raise RuntimeError(
            "The ticket was updated, but its existing event-registration row "
            "could not be found. Review the Event Registrations sheet."
        )

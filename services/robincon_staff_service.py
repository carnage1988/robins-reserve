from __future__ import annotations

from collections import Counter
from typing import Any

import gspread

from services.robincon_service import RobinConService


class RobinConStaffService:
    def __init__(self, service: RobinConService) -> None:
        self.service = service

    def tickets(self) -> list[dict[str, Any]]:
        return self.service.tickets_sheet.get_all_records()

    def find(self, query: str) -> list[dict[str, Any]]:
        wanted = query.strip().casefold()
        fields = (
            "Ticket ID", "Order Number", "Ticket Holder Name",
            "Ticket Holder Email", "Discord Username", "Discord User ID",
        )
        return [
            record for record in self.tickets()
            if any(wanted in str(record.get(field, "")).casefold() for field in fields)
        ][:20]

    def ticket(self, ticket_id: str) -> dict[str, Any] | None:
        wanted = ticket_id.strip().casefold()
        return next(
            (
                record for record in self.tickets()
                if str(record.get("Ticket ID", "")).strip().casefold() == wanted
            ),
            None,
        )

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
            in {"confirmed", "active", "registered"}
        )
        result: dict[str, list[dict[str, Any]]] = {}
        for day, events in (
            ("Saturday", self.service.get_active_saturday_events()),
            ("Sunday", self.service.get_active_sunday_events()),
        ):
            result[day] = [
                {
                    **event,
                    "Registered": counts[str(event.get("Event ID", "")).strip()],
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
            record for record in self.tickets()
            if str(record.get(field, "")).strip()
        ]

    def check_in(
        self,
        ticket_id: str,
        staff_id: int,
        staff_name: str,
    ) -> dict[str, Any]:
        headers = self.service.tickets_sheet.row_values(1)
        wanted = ticket_id.strip().casefold()
        for row_number, record in enumerate(
            self.service.tickets_sheet.get_all_records(),
            start=2,
        ):
            stored = str(record.get("Ticket ID", "")).strip().casefold()
            if stored != wanted:
                continue
            if self.service._is_true(record.get("Checked In", "")):
                raise ValueError("This ticket is already checked in.")

            checked_in_at = self.service._now()
            updates = [
                {
                    "range": gspread.utils.rowcol_to_a1(
                        row_number,
                        headers.index("Checked In") + 1,
                    ),
                    "values": [["TRUE"]],
                },
                {
                    "range": gspread.utils.rowcol_to_a1(
                        row_number,
                        headers.index("Checked In At") + 1,
                    ),
                    "values": [[checked_in_at]],
                },
            ]
            self.service.tickets_sheet.batch_update(updates)
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
        raise ValueError("No RobinCon ticket was found with that ID.")

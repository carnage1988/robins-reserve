import random
import string
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from config import LEAGUE_EVENT_DURATION_HOURS, LEAGUE_WINDOW_DAYS
from services.sheets_service import SheetsService


BELFAST_TZ = ZoneInfo("Europe/London")


class LeagueService:
    """Handle League player, event, attendance, and role-state data."""

    def __init__(self, sheets: SheetsService) -> None:
        self.sheets = sheets

    @staticmethod
    def _now() -> datetime:
        return datetime.now(BELFAST_TZ)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None

        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BELFAST_TZ)

        return parsed.astimezone(BELFAST_TZ)

    @staticmethod
    def _generate_store_code(length: int = 6) -> str:
        alphabet = string.ascii_uppercase + string.digits
        return "".join(
            random.SystemRandom().choice(alphabet)
            for _ in range(length)
        )

    def _find_player_row(
        self,
        discord_user_id: int,
    ) -> tuple[int, dict[str, Any]] | None:
        records = self.sheets.league_players_sheet.get_all_records()

        for row_number, record in enumerate(records, start=2):
            stored_user_id = str(
                record.get("Discord User ID", "")
            ).strip()

            if stored_user_id == str(discord_user_id):
                return row_number, record

        return None

    def get_linked_player(
        self,
        discord_user_id: int,
    ) -> dict[str, Any] | None:
        """Return a linked League player by Discord user ID."""

        found = self._find_player_row(discord_user_id)
        return found[1] if found is not None else None

    def link_player(
        self,
        discord_user_id: int,
        discord_name: str,
        player_id: str,
    ) -> dict[str, str]:
        """Link a Discord account to a Play! Pokémon Player ID."""

        clean_player_id = player_id.strip().upper()

        if not clean_player_id:
            raise ValueError("Player ID cannot be empty.")

        if len(clean_player_id) > 30:
            raise ValueError("Player ID is too long.")

        if self.get_linked_player(discord_user_id) is not None:
            raise ValueError(
                "This Discord account already has a linked Player ID."
            )

        records = self.sheets.league_players_sheet.get_all_records()

        for record in records:
            stored_player_id = str(
                record.get("Player ID", "")
            ).strip().upper()

            if stored_player_id == clean_player_id:
                raise ValueError(
                    "This Player ID is already linked to another Discord account."
                )

        linked_at = self._now().isoformat(timespec="seconds")

        self.sheets.league_players_sheet.append_row(
            [
                str(discord_user_id),
                discord_name,
                clean_player_id,
                "",
                "FALSE",
                linked_at,
            ],
            value_input_option="USER_ENTERED",
        )

        return {
            "player_id": clean_player_id,
            "linked_at": linked_at,
        }

    def unlink_player(self, discord_user_id: int) -> dict[str, Any]:
        """Remove a Discord account's League player link."""

        found = self._find_player_row(discord_user_id)
        if found is None:
            raise ValueError("You do not have a linked Player ID.")

        row_number, record = found
        self.sheets.league_players_sheet.delete_rows(row_number)
        return record

    def _find_active_event_row(
        self,
    ) -> tuple[int, dict[str, Any]] | None:
        records = self.sheets.league_events_sheet.get_all_records()
        now = self._now()

        for row_number, record in enumerate(records, start=2):
            active_value = str(record.get("Active", "")).strip().upper()
            if active_value != "TRUE":
                continue

            end_time = self._parse_datetime(record.get("End Time", ""))
            if end_time is None or end_time <= now:
                self.sheets.league_events_sheet.update_cell(
                    row_number,
                    5,
                    "FALSE",
                )
                continue

            return row_number, record

        return None

    def get_active_event(self) -> dict[str, Any] | None:
        """Return the currently active, unexpired League event."""

        found = self._find_active_event_row()
        return found[1] if found is not None else None

    def start_event(self) -> dict[str, str]:
        """Create and store a new League event."""

        if self.get_active_event() is not None:
            raise ValueError("A League event is already active.")

        start_time = self._now()
        end_time = start_time + timedelta(
            hours=LEAGUE_EVENT_DURATION_HOURS
        )

        event_id = start_time.strftime("%Y%m%d%H%M%S")
        store_code = self._generate_store_code()

        self.sheets.league_events_sheet.append_row(
            [
                event_id,
                store_code,
                start_time.isoformat(timespec="seconds"),
                end_time.isoformat(timespec="seconds"),
                "TRUE",
            ],
            value_input_option="USER_ENTERED",
        )

        return {
            "event_id": event_id,
            "store_code": store_code,
            "start_time": start_time.isoformat(timespec="seconds"),
            "end_time": end_time.isoformat(timespec="seconds"),
        }

    def close_active_event(self) -> dict[str, Any]:
        """Close the currently active League event."""

        found = self._find_active_event_row()
        if found is None:
            raise ValueError("There is no active League event.")

        row_number, record = found
        closed_at = self._now().isoformat(timespec="seconds")

        self.sheets.league_events_sheet.update_cell(
            row_number,
            4,
            closed_at,
        )
        self.sheets.league_events_sheet.update_cell(
            row_number,
            5,
            "FALSE",
        )

        result = dict(record)
        result["End Time"] = closed_at
        result["Active"] = "FALSE"
        return result

    def get_event_attendance_count(self, event_id: str) -> int:
        """Return the number of unique Discord users checked into an event."""

        user_ids = {
            str(record.get("Discord User ID", "")).strip()
            for record in (
                self.sheets.league_attendance_sheet.get_all_records()
            )
            if str(record.get("Event ID", "")).strip() == str(event_id)
            and str(record.get("Discord User ID", "")).strip()
        }
        return len(user_ids)

    def get_league_status(self) -> dict[str, Any]:
        """Return current event and player totals for staff reporting."""

        active_event = self.get_active_event()
        players = self.sheets.league_players_sheet.get_all_records()

        active_players = sum(
            1
            for player in players
            if str(player.get("Role Active", "")).strip().upper() == "TRUE"
        )

        status: dict[str, Any] = {
            "active_event": active_event,
            "linked_players": len(players),
            "active_players": active_players,
            "attendance_count": 0,
        }

        if active_event is not None:
            event_id = str(active_event.get("Event ID", "")).strip()
            status["attendance_count"] = self.get_event_attendance_count(
                event_id
            )

        return status

    def check_in_player(
        self,
        discord_user_id: int,
        store_code: str,
    ) -> dict[str, str]:
        """Check a linked player into the active League event."""

        player = self.get_linked_player(discord_user_id)
        if player is None:
            raise ValueError(
                "You must link your Player ID before checking in."
            )

        active_event = self.get_active_event()
        if active_event is None:
            raise ValueError("There is no active League event.")

        expected_code = str(
            active_event.get("Store Code", "")
        ).strip().upper()
        supplied_code = store_code.strip().upper()

        if supplied_code != expected_code:
            raise ValueError("The store code is not valid.")

        event_id = str(active_event.get("Event ID", "")).strip()
        attendance_records = (
            self.sheets.league_attendance_sheet.get_all_records()
        )

        for record in attendance_records:
            recorded_event_id = str(
                record.get("Event ID", "")
            ).strip()
            recorded_user_id = str(
                record.get("Discord User ID", "")
            ).strip()

            if (
                recorded_event_id == event_id
                and recorded_user_id == str(discord_user_id)
            ):
                raise ValueError(
                    "You have already checked in for this event."
                )

        player_id = str(player.get("Player ID", "")).strip()
        checked_in_at = self._now().isoformat(timespec="seconds")

        self.sheets.league_attendance_sheet.append_row(
            [
                event_id,
                str(discord_user_id),
                player_id,
                checked_in_at,
            ],
            value_input_option="USER_ENTERED",
        )

        found = self._find_player_row(discord_user_id)
        if found is None:
            raise RuntimeError("Linked player disappeared during check-in.")

        row_number, _ = found
        self.sheets.league_players_sheet.update_cell(
            row_number,
            4,
            checked_in_at,
        )
        self.sheets.league_players_sheet.update_cell(
            row_number,
            5,
            "TRUE",
        )

        return {
            "event_id": event_id,
            "player_id": player_id,
            "checked_in_at": checked_in_at,
        }

    def get_role_reconciliation_players(self) -> list[dict[str, Any]]:
        """Return linked players with their expected League role state."""

        cutoff = self._now() - timedelta(days=LEAGUE_WINDOW_DAYS)
        results: list[dict[str, Any]] = []

        for row_number, record in enumerate(
            self.sheets.league_players_sheet.get_all_records(),
            start=2,
        ):
            user_id = str(record.get("Discord User ID", "")).strip()
            if not user_id.isdigit():
                continue

            last_attendance = self._parse_datetime(
                record.get("Last Attendance", "")
            )
            should_have_role = (
                last_attendance is not None
                and last_attendance >= cutoff
            )

            results.append(
                {
                    "row_number": row_number,
                    "discord_user_id": int(user_id),
                    "player_id": str(record.get("Player ID", "")).strip(),
                    "last_attendance": last_attendance,
                    "should_have_role": should_have_role,
                    "role_active": (
                        str(record.get("Role Active", ""))
                        .strip()
                        .upper()
                        == "TRUE"
                    ),
                }
            )

        return results

    def set_role_active(
        self,
        discord_user_id: int,
        active: bool,
    ) -> None:
        """Update the stored League role state for one player."""

        found = self._find_player_row(discord_user_id)
        if found is None:
            return

        row_number, _ = found
        self.sheets.league_players_sheet.update_cell(
            row_number,
            5,
            "TRUE" if active else "FALSE",
        )

    def set_role_states_bulk(
        self,
        updates: list[tuple[int, bool]],
    ) -> int:
        """Persist multiple role-state changes in one Sheets request."""

        if not updates:
            return 0

        payload = [
            {
                "range": f"E{row_number}",
                "values": [["TRUE" if active else "FALSE"]],
            }
            for row_number, active in updates
            if row_number >= 2
        ]
        if not payload:
            return 0
        self.sheets.league_players_sheet.batch_update(payload)
        return len(payload)

# RobinCon family and group ticket test

1. Create or reset a paid development order with quantity 2.
2. Run `/robincon-link` and enter the purchaser order number/email plus the first attendee name.
3. Link the first ticket.
4. Run `/robincon-link` again with the same purchaser details and the second attendee name.
5. Link the remaining ticket. The same Discord user ID should now appear on both ticket rows, with different Ticket Holder Names.
6. Run `/robincon-register`. A ticket/attendee selector should appear.
7. Complete and lock the first attendee registration.
8. Run `/robincon-register` again, choose the second attendee, and complete that registration.
9. Run `/robincon-register` again and confirm both attendees can be selected and each displays its own locked summary.
10. Confirm exactly four Event Registrations rows exist: Saturday and Sunday for each ticket.

No Google Sheet header changes are required for this update.

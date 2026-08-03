# Robin's Reserve v1.4 Dashboard Specification

This release restores the fixed red-accented operations dashboard.

- Home health lights, pending approvals, live Pokémon League state, statistics and activity.
- Dashboard approval/decline invokes the existing reservation lifecycle, updates Discord with 👍/👎, and DMs the customer.
- Approval DMs include itemised prices, basket total and pickup PIN.
- Orders page provides PIN lookup, itemised pricing, collection, and a 60-second idle reset.
- Existing RobinCon family-ticket support is preserved.

Required preorder/archive sheet pricing columns: `Unit Price` and `Subtotal`.

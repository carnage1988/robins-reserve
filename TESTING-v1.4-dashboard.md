# v1.4 Dashboard Test Plan

## Sheet preparation

Confirm these columns exist on **Preorders**, **Collected**, **Cancelled** and **Rejected**:

- `Unit Price`
- `Subtotal`

Confirm **Products** contains either `Unit Price` or `Price`.

## Startup

```bash
python -m compileall -q .
docker compose up -d --build --force-recreate
docker compose logs -f
```

## Dashboard

1. Log in with an authorised Discord staff account.
2. Confirm green/red status lights render independently.
3. Confirm Pokémon League shows **Not Running** when inactive.
4. Start a League event and verify store code, attendance and event times.

## Pending approvals

1. Create a priced preorder through Discord.
2. Confirm it appears on the dashboard home page with unit prices, line totals and basket total.
3. Click **Approve**.
4. Confirm the Google Sheet becomes Approved.
5. Confirm the original Discord approval message contains only the 👍 decision reaction and an approval reply.
6. Confirm the customer receives an itemised DM, basket total and pickup PIN.
7. Repeat with **Decline** and confirm 👎, customer refusal DM and stock restoration.

## Orders page

1. Search an approved pickup PIN.
2. Confirm customer, products, prices, line totals and basket total.
3. Click **Collect Order** and verify archive/DM behavior.
4. Leave the result untouched for 60 seconds and confirm it returns to **Waiting for a PIN**.

## Regression

- Discord reaction approval still works.
- Customer approval DM includes basket total.
- RobinCon family ticket selection and registration still work.

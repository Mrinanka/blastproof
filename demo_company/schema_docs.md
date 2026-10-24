# Company database: schema notes and business rules

Notes for people and for Blastproof's knowledge base. Each `## table: <name>` section is tagged
with that table when ingested (backend plan §8 step 11); other sections are untagged.

## table: organizations

Customer companies. A user may belong to one organization (`users.organization_id`). Deleting an
organization keeps its users and sets their `organization_id` to NULL.

## table: users

One row per customer account. `plan` is `free`, `pro` or `enterprise`. `last_login_at` is the last
time the person signed in; it is never empty, because signing up counts as a login. "Inactive"
usually means no login for 6 months or more. `email` and `phone` are personal data.

Deleting a user also deletes their orders (and those orders' items), subscriptions, payments,
support tickets and customer notes. Their invoices are kept for accounting, with `user_id` set to
NULL.

## table: subscriptions

Paid plans. Every pro and enterprise user has an `active` subscription; `cancelled` rows belong to
people who paid in the past. `monthly_price_cents` is what they pay each month.

## table: payments

Monthly card payments for subscriptions: `succeeded`, `failed` or `refunded`. Deleted with the
user.

## table: invoices

One invoice per payment. Invoices are financial records: they are kept when a user is deleted,
with `user_id` set to NULL. `due_date` is 14 days after `issued_at`.

## table: products

Add-ons and merchandise sold in the store. `order_items.product_id` points here, but is not a
foreign key, so deleting a product leaves old order lines in place.

## table: orders

Store orders. `status` is `completed`, `refunded` or `cancelled`. Deleted with the user; deleting
an order deletes its items.

## table: order_items

Line items of an order: product, quantity and the unit price at the time of the order.

## table: support_tickets

Customer support tickets: `open`, `pending` or `closed`, with a priority. Closed tickets older than
one year may be purged under the retention policy, after review.

## table: feature_flags

Product feature switches. `rollout_percent` is the share of users who see the feature when
`enabled` is true. Changing a flag changes the product for real customers immediately.

## table: customer_notes

Free-text notes about a customer, written by staff or agents. Adding a note is routine and low
risk.

## business rules

- A paying customer is a user whose plan is `pro` or `enterprise`. Free users do not pay.
- Never delete a paying customer's account without a human's approval: it ends a contract and
  deletes their order history.
- Enterprise accounts have signed contracts and must never be deleted. Downgrade or deactivate
  them through account management instead.
- "Inactive" cleanups must exclude paying customers: someone who pays but rarely logs in is still a
  customer.
- Invoices are financial records and must be kept for 7 years. Never delete them.
- Personal data (email, phone) must not be copied out of the database or shown in full.
- The `blastproof` schema belongs to the safety gateway; nobody else may change it.

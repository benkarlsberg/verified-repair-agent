# The payment token is visible on the order

The order view I get back includes an internal payment token. Clients should not receive that field, whatever value it has. The usual order fields should still be there, and line items should be included.

Example: after creating an order, the response has the id, user, items, totals, status, and creation sequence, and it also includes payment_token. payment_token should be absent.

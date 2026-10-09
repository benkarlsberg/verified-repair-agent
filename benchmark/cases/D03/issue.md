# Shipping ignores the discount I supplied

I pass a discount into the shipping calculation. That discount is supplied by the caller. It is not computed automatically. Shipping should be free when the subtotal minus the supplied discount is at least 5000 cents, and it should be 500 cents otherwise.

Example: a subtotal of 6000 cents with a supplied discount of 1500 cents should cost 500 cents to ship, because 4500 is below 5000. The same subtotal with a supplied discount of 1000 cents should ship free, because the remainder is exactly 5000. A supplied discount of 1001 cents on that subtotal should cost 500 cents to ship.

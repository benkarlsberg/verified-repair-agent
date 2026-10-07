# A failed reservation reduces stock anyway

Reserving several products should be all or nothing. If a later product is out of stock, every stock count should stay as it was. Right now the earlier products are reduced even though the reservation fails.

Example: the warehouse has 2 mugs and 0 lamps. Reserving 1 mug and 1 lamp should fail, and the mug count should still be 2. A reservation that fits, including one that uses the exact number in stock, should decrement each product.

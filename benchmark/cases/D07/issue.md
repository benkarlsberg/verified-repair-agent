# The first order is missing from the first page

Order lists are paged, and page 1 is the first page. The earliest order does not show up on page 1.

Example: three orders created in sequence, listed with page 1 and page size 20, should include the first order. Later pages should continue in creation order. A page past the end should be an empty list and should still report the real total. Page numbers below 1, and page sizes outside 1 through 100, should be rejected.

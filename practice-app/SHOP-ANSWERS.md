# AddisMart: planted bugs (read after testing)

Chosen before Mirmera's sightings, general rule checks, flow tours and replays
were built, so that the second practice app measures how far Mirmera carries
beyond the KYC form rather than what it was tuned to.

1. **Phone is required.** Leaving the phone empty gets "Phone number is
   required." although the story says the phone number is optional. Oracle:
   claims.
2. **Quantity 11 is accepted.** The form refuses 0 but takes anything above 10.
   Oracle: claims (between 1 and 10); a boundary value.
3. **An email without a domain is accepted.** "name@" places the order; the
   form only checks for an "@". Oracle: claims (a valid email address).
4. **One double click on Place order creates two orders.** The button isn't
   disabled while the order is sent; My orders lists both. Oracle: user
   expectations and claims (listed once).
5. **The confirmation page throws when opened directly or refreshed.** It reads
   the order from the tab's storage and removes it; a refresh, a bookmark or a
   shared link finds nothing ("Cannot read properties of null"). Oracle:
   product and user expectations.
6. **The total is wrong for 3 or more items.** The total charges one item less
   from 3 on (3 × 350 Birr shows 700). Oracle: claims (price times quantity).
   No check of Mirmera's is built for wrong numbers: the agent has to notice.

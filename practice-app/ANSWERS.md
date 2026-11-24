# Planted bugs (read after testing)

Try to find these with Mirmera first. Each line names an oracle that makes it
a problem.

1. **Passport always fails.** Choosing Passport makes `POST /api/verify`
   answer 500 and the page says "Something went wrong". Oracle: claims (the
   spec says all three documents are supported) and product (National ID
   works). The recorded browser shows the failed request.
2. **Expired documents are accepted.** An expiry date in the past still says
   "Verified". Oracle: statutes / claims (requirement 3, the NBE directive).
   No error anywhere: only a tester who knows the rule notices.
3. **Names in Ge'ez script are rejected.** "አበበ ከበደ" gets "Name must use
   letters only". Oracle: claims (requirement 2) and user expectations / world
   (most customers' names). A context-driven bug: invisible without knowing
   who the users are.
4. **My documents is broken.** The page shows the count but the list stays
   empty, and an uncaught error appears in the console ("Cannot read
   properties of undefined (reading 'map')"): the API returns `docs`, the page
   reads `items`. Oracle: claims (requirement 6) and product. The recorded
   browser shows the page error.

## Bugs 5 to 9: the browser, the result and the data

These are on by default. `PLANTED=classic node server.mjs` runs the app with bugs 1 to 4
only, as the agent evals do, so their scores stay comparable over time. Mirmera's own
checks find all five in plain code: they're display versus data (Part 8m of the guide)
and browser storage (Part 8n).

5. **The result says Verified when the data says rejected.** After verifying with an
   expired document, *Your verification* shows **Status: Verified**, but
   `GET /api/verification/<id>` answers `"status": "rejected"`. The page never reads the
   status. Oracle: product (the page contradicts its own data) and statutes (bug 2's rule).
   Mirmera drafts *Status shown differs from the app's own data* (high).
6. **The expiry date is shown a day early.** *Your verification* shows the expiry in New
   York time (`timeZone: 'America/New_York'`), so 2027-11-23 shows as 22/11/2027.
   Oracle: product (the page against its data). Mirmera drafts *Expiry date shown differs
   from the app's own data*, "one day earlier: a date without a time, moved by a time-zone
   conversion".
7. **The customer's details stay on the phone after signing out.** The form saves a
   draft (`kyc_draft`: full name, phone, document number, expiry date) in local storage
   as you type, and signing out never removes it. Oracle: statutes / user expectations
   (personal data on a shared phone at an agent's shop). Mirmera notes the curios
   *local storage … holds personal details … under "kyc_draft"* and, when it signs out
   at the end of a run, *after signing out, local storage still holds personal details*.
8. **The session outlives signing out, and any script can read it.** The demo signs you
   in as Abebe: a token (a JWT) in local storage, and a `sid` cookie without HttpOnly.
   Signing out (`/signout`) removes the token but never clears the cookie. Oracle:
   security. Mirmera notes *holds a token (JWT) under "auth"*, *the session cookie "sid"
   has no HttpOnly*, and *after signing out, the session cookie "sid" is still set*.
9. **The data itself is odd.** `GET /api/documents` has a document whose status is
   *verified* although its expiry date (2025-03-01) has passed, and one with no expiry
   date at all. Oracle: the data's own consistency (and bug 2's rule). Mirmera notes them
   as curios, because only you can say whether they're wrong. My documents (bug 4) still
   doesn't show them.

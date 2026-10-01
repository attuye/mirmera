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

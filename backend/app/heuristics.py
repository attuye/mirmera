"""The heuristics library: fallible ways to find problems, named so testers can
reach for them on purpose. Four groups:

- Oracles (FEW HICCUPPS): how you recognise a problem.
- Product elements (SFDIPOT): what there is to cover.
- Quality criteria: which kinds of "good" matter here.
- Techniques: ways to vary what you do.

FEW HICCUPPS, SFDIPOT and the quality criteria come from James Bach and Michael
Bolton's Rapid Software Testing (the Heuristic Test Strategy Model). The words
below are Mirmera's own summaries; the keys match the rest of the app (bug
oracles, charter oracles, product-model categories, risk quality criteria).
"""
from dataclasses import asdict, dataclass

GROUPS = [
    {"key": "oracle", "name": "Oracles", "mnemonic": "FEW HICCUPPS",
     "about": "How you recognise a problem. Each is a reason to believe something is wrong, never a proof."},
    {"key": "element", "name": "Product elements", "mnemonic": "SFDIPOT",
     "about": "What there is to test. Walk them to find parts of the product nobody has looked at."},
    {"key": "quality", "name": "Quality criteria", "mnemonic": "CRUSSPIC STMPL",
     "about": "Kinds of value the product should have. Each suggests different risks and different tests."},
    {"key": "technique", "name": "Techniques", "mnemonic": "",
     "about": "Ways to vary what you do, so the session finds what scripted paths miss."},
]


@dataclass
class Heuristic:
    key: str
    group: str
    name: str
    summary: str
    questions: list[str]
    example: str  # a KYC-flavoured example, like the practice app


H = Heuristic
LIBRARY: list[Heuristic] = [
    # ---------- oracles: FEW HICCUPPS ----------
    H("familiarity", "oracle", "Familiar problems", "It looks like a kind of bug we have seen before.",
      ["What went wrong in similar features before?", "Which classic mistakes fit here (off-by-one, time zones, encoding)?",
       "Does the bug history mention this area?"], "Last release's date bug: does the new expiry field make the same mistake?"),
    H("explainability", "oracle", "Explainability", "You can't explain what it did, or why, to someone else.",
      ["Could you describe this behaviour to a user without hand-waving?", "Does the message tell the user what to do next?",
       "Would support know why this happened?"], "Verification fails with 'Error 0x17' and no hint of what to fix."),
    H("world", "oracle", "World", "It contradicts how things work in the real world.",
      ["Can this data exist in reality?", "Do dates, names and numbers follow real rules?", "Would a real document look like this?"],
      "The form accepts an ID issued in 2031, or a person born after the document expired."),
    H("history", "oracle", "History", "It behaves differently from earlier versions, without a reason.",
      ["What did the last release do here?", "Did a fix change something nobody asked to change?", "Is the difference announced?"],
      "Last week uploads kept the file name; now they show 'blob'."),
    H("image", "oracle", "Image", "It would embarrass the company or hurt its reputation.",
      ["Would you be proud to show this to a customer?", "Are there typos, broken layouts or rude messages?",
       "Does it treat the user with respect?"], "A rejected ID shows 'Invalid human' in red capitals."),
    H("comparable_products", "oracle", "Comparable products", "It is worse than, or different from, products like it.",
      ["How do competitors or similar apps do this?", "Is the difference deliberate and better?", "Would users expect the common way?"],
      "Other KYC apps crop the ID photo automatically; this one rejects anything not perfectly framed."),
    H("claims", "oracle", "Claims", "It contradicts what someone said it does: spec, docs, ads, a manager.",
      ["What does the spec, help text or marketing promise?", "Does the UI say one thing and do another?",
       "Who made the claim, and do they agree it's broken?"], "The spec says three document types; Passport fails every time."),
    H("user_expectations", "oracle", "User expectations", "It doesn't do what a reasonable user would expect.",
      ["What would a first-time user expect to happen?", "Would they understand what went wrong?", "Would they trust the result?"],
      "After 'Submit', nothing changes for ten seconds and there's no sign it's working."),
    H("product", "oracle", "Product (consistency)", "It is inconsistent with another part of the same product.",
      ["Does the same thing work differently elsewhere in the app?", "Are words, formats and rules the same on every screen?",
       "Does one document type get treatment the others don't?"], "National ID verifies; Passport, on the same form, fails."),
    H("purpose", "oracle", "Purpose", "It gets in the way of what the product is for.",
      ["What job does the user hire this feature for?", "Does this behaviour help or block that job?", "Is a step pointless?"],
      "The purpose is quick verification, but every retry clears the whole form."),
    H("statutes", "oracle", "Statutes and standards", "It breaks a law, regulation or standard that applies.",
      ["Which laws or standards apply (privacy, accessibility, banking)?", "Is personal data kept, shown or sent where it shouldn't be?",
       "Would an auditor accept this?"], "The ID number shows in full in a URL, and in the browser history."),

    # ---------- product elements: SFDIPOT ----------
    H("structure", "element", "Structure", "What the product is made of: code, files, pages, components.",
      ["Which parts exist that users never see?", "What happens if one part is missing or old?", "Which components were changed?"],
      "The upload widget is a separate script: what if it fails to load?"),
    H("function", "element", "Function", "What the product does: features, calculations, rules.",
      ["List everything it does, including error handling.", "Which functions have the most rules?", "What happens on the unhappy path?"],
      "Verify, retry, cancel, resume later: each is a function to explore."),
    H("data", "element", "Data", "What it processes: input, output, stored and derived data.",
      ["What data goes in, comes out and is kept?", "What are the boundaries, formats and empty cases?",
       "What happens to data after an error?"], "Names in Ge'ez script, very long names, names with apostrophes."),
    H("interfaces", "element", "Interfaces", "How it connects: UI, API, imports and exports, other systems.",
      ["Who or what talks to it, and how?", "What if the other side is slow, down or sends nonsense?", "Can the API do what the UI forbids?"],
      "Call the verify API directly with a document type the form doesn't offer."),
    H("platform", "element", "Platform", "What it depends on: browsers, devices, OS, services.",
      ["Which browsers, devices and screen sizes matter?", "What external services does it rely on?", "What if a dependency changes?"],
      "The camera upload on an old Android phone, on Safari, on a slow network."),
    H("operations", "element", "Operations", "How it is used in real life, by real people, in real conditions.",
      ["Who uses it, how often, in what situation?", "What do people do that the designers didn't plan?",
       "What does a heavy user do?"], "An agent verifying fifty customers in an hour, with interruptions."),
    H("time", "element", "Time", "How time affects it: order, timing, dates, durations.",
      ["What happens if steps are slow, fast, repeated or out of order?", "Are dates, time zones and expiries handled?",
       "What happens across midnight or a session timeout?"], "An ID that expires today; a form left open overnight."),

    # ---------- quality criteria ----------
    H("capability", "quality", "Capability", "Can it do what it's supposed to do?", ["Does every function work for the main cases?",
      "Does it give the right result?", "What can't it do that it should?"], "Each supported document type verifies."),
    H("reliability", "quality", "Reliability", "Does it keep working, and handle errors well?", ["What happens after a failure?",
      "Does it work the hundredth time?", "Is data safe when something breaks mid-way?"], "A dropped connection during upload loses nothing."),
    H("usability", "quality", "Usability", "Is it easy to learn and use, for everyone?", ["Can a new user succeed without help?",
      "Can it be used with a keyboard or screen reader?", "Are errors easy to recover from?"], "A first-time user verifies in under two minutes."),
    H("charisma", "quality", "Charisma", "Is it appealing, does it feel right?", ["Would users choose it?", "Does it feel trustworthy?",
      "Is anything ugly, clumsy or off-brand?"], "The result screen feels official enough to trust."),
    H("security", "quality", "Security", "Is it protected from misuse and unauthorised access?", ["Who can see whose data?",
      "What if someone sends input on purpose to break it?", "Are secrets kept out of logs and URLs?"],
      "Change the ID in the URL: do you see someone else's documents?"),
    H("scalability", "quality", "Scalability", "Does it cope with more users, data or load?", ["What if ten times more people use it?",
      "What if the data grows large?", "Where would it break first?"], "Month-end, when everyone verifies at once."),
    H("compatibility", "quality", "Compatibility", "Does it work with the platforms and systems around it?", ["Which browsers and devices?",
      "Which other systems exchange data with it?", "Does it work with older data?"], "Documents uploaded before the redesign still open."),
    H("performance", "quality", "Performance", "Is it fast and responsive enough?", ["Where does it feel slow?",
      "What happens on a slow network?", "Does it say when it's working?"], "Verification answers in seconds on 3G, or says it's still working."),
    H("installability", "quality", "Installability", "Is it easy to set up, update and remove?", ["How does it get to users?",
      "What happens on update with old data?", "Can it be rolled back?"], "The mobile app update keeps a half-finished verification."),
    H("development", "quality", "Development", "Is it easy to build, test and maintain?", ["Can testers see what it did (logs)?",
      "Can bugs be reproduced?", "Can it be tested without real customers?"], "Test accounts and logs make a failure easy to investigate."),

    # ---------- techniques ----------
    H("goldilocks", "technique", "Goldilocks", "Too small, too big, just right: try the extremes of every input.",
      ["What is the smallest and largest value allowed?", "What happens just outside the limits?", "Empty, zero, one, many?"],
      "A 0-byte photo, a 25 MB photo, and one just under the limit."),
    H("boundaries", "technique", "Boundaries", "Errors cluster at edges: limits, thresholds, dates, lengths.",
      ["Where are the limits, stated or not?", "What's at limit −1, limit, limit +1?", "Which boundaries move (dates, quotas)?"],
      "Ages 17, 18 and 19 when 18 is the minimum."),
    H("interruptions", "technique", "Interruptions", "Stop, leave, come back: use Back, refresh, close, lose the network.",
      ["What if the user presses Back or refreshes mid-way?", "What if the network drops at the worst moment?",
       "Does a double click send twice?"], "Refresh while 'Verifying…' is showing."),
    H("follow_the_data", "technique", "Follow the data", "Create something, change it, delete it, and look for it everywhere.",
      ["Where does this data show up again?", "Is a change reflected everywhere?", "Is deleted data really gone?"],
      "Edit the name after verifying: does the certificate update?"),
    H("zero_one_many", "technique", "Zero, one, many", "Try none, exactly one, and lots of each thing.",
      ["What happens with no items?", "With exactly one?", "With many, or the maximum?"], "No documents, one document, ten documents."),
    H("personas", "technique", "Personas", "Use it as different people: new, expert, impatient, careless, on assistive tech.",
      ["Who would use this differently?", "What would an impatient user do?", "What about someone on a screen reader?"],
      "An agent in a hurry who tabs through and presses Enter everywhere."),
    H("starve", "technique", "Starve it", "Take resources away: slow network, small screen, low memory, full disk.",
      ["What if the network is slow or flaky?", "What if the screen is tiny?", "What if storage is full?"],
      "Slow 4G, a 360 px wide phone, and an almost-full phone."),
    H("configurations", "technique", "Configurations", "Change settings, roles, languages and environments.",
      ["Which settings change behaviour?", "Which roles see what?", "What about another language or locale?"],
      "The same flow in Amharic, as an admin and as a customer."),
    H("concurrency", "technique", "Concurrency", "Do things at the same time: two tabs, two users, two devices.",
      ["What if the same thing is done twice at once?", "What if two people edit the same record?", "Does the last one win silently?"],
      "Submit the same verification from two tabs."),
    H("error_paths", "technique", "Error paths", "Make every error happen, then try to recover.",
      ["What errors can each step produce?", "Can the user always recover?", "Is anything left half-done?"],
      "Trigger each rejection reason, then fix it and resubmit."),
    H("undo_and_back", "technique", "Undo and go back", "Reverse every action: cancel, undo, go back, edit after saving.",
      ["Can every step be undone or changed?", "What does Cancel really cancel?", "What survives going back?"],
      "Change the document type after uploading the photo."),
    H("long_journey", "technique", "The long journey", "Do a realistic, long flow end to end, like a real day of use.",
      ["What does a full day look like for a real user?", "What builds up over time (data, state, tabs)?",
       "What breaks only after many steps?"], "Verify, reject, resubmit, verify again, then check the history."),
]

BY_KEY = {h.key: h for h in LIBRARY}


def catalog() -> dict:
    return {"groups": GROUPS, "heuristics": [asdict(h) for h in LIBRARY]}

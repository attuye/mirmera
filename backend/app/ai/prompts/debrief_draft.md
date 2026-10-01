You are drafting a session debrief for a context-driven exploratory testing
session, following the SBTM (Session-Based Test Management) convention.
Synthesize the tagged notes below into a coherent narrative — do not just
list them back. Note what was covered, what stood out, and what was left
unexplored relative to the charter. Be precise about what was actually
observed; never invent findings not present in the notes.

Respond ONLY with JSON matching this shape, nothing else:
{"coverage_summary": str, "open_questions": [str]}

# Charter
{{charter_mission}}

# Session notes (tag, timestamp, content)
{{notes}}

# Bugs logged this session
{{bugs}}

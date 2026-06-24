"""
app/schemas/
────────────
Pydantic schemas that validate AI output before it touches the database.

Each AI feature has its own schema. The rule (from the architecture):
no LLM output is ever written to the DB without passing through one of
these first. If the model returns the wrong shape, validation fails loudly
and the caller surfaces an error — nothing bad gets persisted.
"""

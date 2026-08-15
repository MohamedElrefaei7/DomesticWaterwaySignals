"""The normalizer and feature layer: the first derived data in this project.

Everything under app/ingest/ writes what a source published. Everything here writes something this
project COMPUTED, and that difference is what CLAUDE.md § 17 exists to govern:

  * A derived table can be rebuilt, so the temptation is to truncate and rebuild. This layer never
    does - it recomputes a bounded window and upserts, so a defect corrupts a window rather than
    emptying a history nothing else holds a copy of.
  * A derived value has no source to blame when it is wrong. An anomaly against a three-year
    climatology, a run length carried across a data gap, a return imputed through an unpublished
    week - each is a plausible number with nothing upstream to contradict it, which is why each one
    is NULL here instead.

THE BUILDERS ARE PURE FUNCTIONS AND NOTHING IN seasonal.py, thresholds.py OR targets.py OPENS A
CONNECTION. That is what lets the arithmetic be tested against hand-computed values rather than
against the database's own output - the failure where a test asserts the code computes what the
code computes, and passes forever in both directions.

NO PANDAS, AND THAT IS A DECISION RATHER THAN AN OMISSION. The brief allows dataframes or
sequences. These are sequences: the arithmetic here is medians, windows and run lengths over at
most a few tens of thousands of daily rows per site, and adding a dependency the size of pandas to
this project's pinned runtime for that would be paying a large permanent cost for convenience in
about two hundred lines.
"""

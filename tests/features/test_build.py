"""The build: the leakage guard, idempotence, and the two things it must never do.

Test 23 and 24 are the same decision approached from two directions, and they are separate on
purpose. One constructs the case where nearest-date and last-on-or-before DISAGREE and asserts
which wins; the other states the invariant directly, so an implementation that happened to pass the
constructed case still has to satisfy the general claim.
"""

import ast
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.features import build

# A published rate week: USDA labels them by the Thursday the week ends on.
WEEK_ENDING = date(2022, 10, 6)


# ---------------------------------------------------------------------------------------------
# Decision 7 — the leakage guard.
# ---------------------------------------------------------------------------------------------


def test_the_join_takes_the_last_feature_on_or_before_the_week_ending():
    """Test 23. The case where NEAREST DISAGREES, which is the only case that matters.

    Week ending Thursday 2022-10-06. Features on the Monday before (3 days earlier) and the Friday
    after (1 day later).

        NEAREST                 picks Friday 10-07 - it is one day away against three.
        LAST ON OR BEFORE       picks Monday 10-03.

    Nearest is shorter, reads as obviously correct, and lets a day of river conditions that had not
    happened when the rate was published inform that week's target.
    """
    monday = date(2022, 10, 3)
    friday = date(2022, 10, 7)

    chosen = build.last_on_or_before([monday, friday], WEEK_ENDING)

    assert chosen == monday, (
        f"the join chose {chosen}. {friday} is NEARER to the week ending - one day against three - "
        f"so a nearest-date implementation picks it, and the target for a week ending {WEEK_ENDING} "
        f"is then informed by a day that had not happened yet."
    )

    # The week ending itself wins when it is present: `<=`, not `<`. A feature dated exactly on the
    # week ending describes a day that had finished, so excluding it would discard the most
    # informative row for no reason.
    assert build.last_on_or_before([monday, WEEK_ENDING, friday], WEEK_ENDING) == WEEK_ENDING

    # A week that precedes every feature gets None rather than the earliest one. Reaching forward
    # would reintroduce the leak at the place it would be largest.
    assert build.last_on_or_before([friday], WEEK_ENDING) is None


def test_no_feature_dated_after_a_week_ending_informs_that_week():
    """Test 24. The invariant stated directly, over a dense daily series.

    Test 23 constructs one adversarial pair. This asserts the general claim across every week in a
    range, so an implementation that special-cased the constructed case still has to hold.

    THE FEATURE DATES ARE DELIBERATELY EVERY THIRD DAY RATHER THAN DAILY. Against a daily series
    the week ending itself is always present, so nearest-date and last-on-or-before agree on every
    week and this test would pass for BOTH implementations - the guard would be vacuous exactly
    where it was supposed to be strongest. At three-day spacing the nearest feature falls strictly
    AFTER the week ending for three of the ten weeks below (offsets 14, 35 and 56), which is where
    the lookahead would enter.
    """
    feature_dates = [date(2022, 9, 1) + timedelta(days=i) for i in range(0, 90, 3)]
    week_endings = [date(2022, 9, 8) + timedelta(days=7 * i) for i in range(10)]

    # The premise, asserted rather than assumed: some week has a nearer feature AFTER it, so a
    # nearest-date implementation would genuinely differ here.
    assert any(
        min(feature_dates, key=lambda d: abs(d - week)) > week for week in week_endings
    ), "no week has a nearer feature after it, so this test cannot distinguish the two joins"

    alignment = build.align_features_to_weeks(feature_dates, week_endings)

    assert set(alignment) == set(week_endings), "some weeks vanished from the alignment"

    for week, feature_date in alignment.items():
        assert feature_date is not None
        assert feature_date <= week, (
            f"week ending {week} was matched to a feature dated {feature_date} - "
            f"{(feature_date - week).days} day(s) AFTER it. That is lookahead: the feature "
            f"describes river conditions the rate could not have known about."
        )
        # And it is the LATEST such date, not merely an earlier one - otherwise the guard would be
        # satisfied by a join that always returned the first feature ever recorded.
        assert feature_date == max(d for d in feature_dates if d <= week)


def test_from_scratch_requires_an_explicit_start_date():
    """Test 27. A flag that silently means "everything" is the flag typed by accident.

    In a shell history `--from-scratch` alone is indistinguishable from the bounded run somebody
    meant. Requiring the date makes the scope of a full rebuild stated rather than inherited.

    Enforced in BOTH places on purpose: `window_for` holds it for every caller including the
    scheduled job, and the parser holds it for the human, who gets a usage message rather than a
    traceback.
    """
    today = date(2026, 8, 14)

    with pytest.raises(ValueError) as excinfo:
        build.window_for(today, from_scratch=True)
    assert "--start" in str(excinfo.value)

    # With a start it is accepted, and it widens the window rather than changing the write mode.
    assert build.window_for(today, from_scratch=True, start=date(1990, 1, 1)) == (
        date(1990, 1, 1),
        today,
    )

    # The CLI refuses too, so nobody reaches the ValueError by way of a stack trace.
    with pytest.raises(SystemExit):
        build.parse_args(["--from-scratch"])
    assert build.parse_args(["--from-scratch", "--start", "1990-01-01"]).start == date(1990, 1, 1)

    # And the ordinary path is a bounded trailing window, not the whole series.
    start, end = build.window_for(today)
    assert (end - start).days == build.DEFAULT_WINDOW_DAYS == 400


def test_the_build_never_issues_a_delete():
    """Test 26. Decision 8, asserted against the source rather than against behaviour.

    A truncate-and-rebuild feels safe on derived tables - they can always be recomputed - and that
    is exactly why the destructive path is not built. THE TRUNCATE ALWAYS SUCCEEDS. If the rebuild
    then raises halfway, all three tables are shorter than they were and nothing upstream holds a
    second copy; a `gauge_daily` rebuilt from 35 years of readings is not a cheap thing to lose to
    a defect in a builder.

    Read from the source because "we do not delete" is a claim that decays quietly: a behavioural
    test would only catch a DELETE on the path it happened to exercise.

    PARSED RATHER THAN GREPPED, and that is not fussiness. The module DISCUSSES deletion at length -
    it has to, since the reasoning is the load-bearing part - so a grep for the word matches the
    argument against doing it and reports the module as guilty of the thing it refuses. Walking the
    AST and checking only NON-DOCSTRING string literals tests exactly the strings that could reach
    a database: comments never become SQL, and neither do docstrings.
    """
    source = Path(build.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            text = ast.get_docstring(node, clean=False)
            if text is not None:
                docstrings.add(text)

    executable_strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
    ]

    destructive = [
        text
        for text in executable_strings
        if re.search(r"\b(DELETE\s+FROM|TRUNCATE)\b", text, flags=re.IGNORECASE)
    ]
    assert not destructive, (
        f"app/features/build.py carries destructive SQL: {destructive}. Derived tables are rebuilt "
        f"by bounded-window upsert, never by truncate-and-rebuild (CLAUDE.md § 17)."
    )

    # The check is not vacuous: the module really does carry SQL string literals for the AST walk
    # to have found, so this cannot pass by the parse having returned nothing.
    assert any("INSERT INTO" in text for text in executable_strings), (
        "no INSERT was found among the module's string literals, so the assertion above proves "
        "nothing about what this module writes"
    )

    # The upserts are present, which is the other half: a module that issued no writes at all would
    # also pass the assertion above.
    assert "ON CONFLICT" in build.FEATURES_UPSERT_SQL
    assert "ON CONFLICT" in build.TARGETS_UPSERT_SQL
    # IS DISTINCT FROM, so a rerun over unchanged inputs reports 0 written rather than reporting
    # its whole input - which is what makes the idempotence test below measurable.
    assert "IS DISTINCT FROM" in build.FEATURES_UPSERT_SQL
    assert "IS DISTINCT FROM" in build.TARGETS_UPSERT_SQL


# ---------------------------------------------------------------------------------------------
# Integration tier.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_rebuilding_the_same_window_twice_changes_nothing(migrated_db, seed_readings):
    """Test 25. Decision 8's idempotence, measured rather than asserted.

    The second run must write ZERO rows - not "the same values again". `IS DISTINCT FROM` is what
    makes that observable: a plain `DO UPDATE` would report its whole input as written on every
    run, which is a number large enough to look reassuring and means nothing (CLAUDE.md § 14).

    It only holds because the window is long enough to reconstruct its own left edge - a run-length
    feature recomputed from a window that started mid-run would produce a shorter count the second
    time, which is the failure this test exists to catch.
    """
    from tests.features.conftest import ST_LOUIS

    start = date(2022, 6, 1)
    end = date(2022, 10, 31)

    seed_readings.daily(
        ST_LOUIS,
        [(start + timedelta(days=i), 200000.0 - 300.0 * i) for i in range((end - start).days + 1)],
    )
    seed_readings.rates(
        [(date(2022, 8, 4) + timedelta(days=7 * i), 400.0 + 50.0 * i) for i in range(12)]
    )

    first = build.build(migrated_db, start, end)
    assert first["gauge_daily_rows"] > 0 and first["feature_rows"] > 0, (
        f"the first build wrote nothing to build on: {first}"
    )
    assert first["target_rows"] > 0

    before = _snapshot(migrated_db)

    second = build.build(migrated_db, start, end)

    assert (second["gauge_daily_rows"], second["feature_rows"], second["target_rows"]) == (0, 0, 0), (
        f"the second run over an identical window wrote {second}. Either a value changed between "
        f"runs - which means the build is not a function of its inputs - or the upsert is not "
        f"comparing with IS DISTINCT FROM and is reporting its whole input."
    )
    assert _snapshot(migrated_db) == before, "values changed across an idempotent rebuild"
    assert second["unregistered_feature_names"] == []


def _snapshot(conn):
    return {
        "gauge_daily": conn.execute(
            "SELECT usgs_site_id, date, value_mean, value_min, n_observations, source"
            "  FROM gauge_daily ORDER BY 1, 2"
        ).fetchall(),
        "features": conn.execute(
            "SELECT date, site_id, feature_name, value, anomaly, climatology_n_years"
            "  FROM features ORDER BY 1, 2, 3"
        ).fetchall(),
        "targets": conn.execute(
            "SELECT week_ending, target_name, horizon_days, value FROM targets ORDER BY 1, 2, 3"
        ).fetchall(),
    }


@pytest.mark.integration
def test_the_build_writes_every_registered_feature_and_nothing_else(migrated_db, seed_readings):
    """The registry drives the loop, end to end, against a real database.

    The unit tests assert the registry is well formed; this asserts the BUILD ACTUALLY ITERATES IT.
    A loop that hardcoded two feature names would satisfy every registry test in this suite and
    write three fewer series.
    """
    from app.features import registry
    from tests.features.conftest import ST_LOUIS

    start = date(2022, 1, 1)
    end = date(2022, 12, 31)
    seed_readings.daily(
        ST_LOUIS,
        [(start + timedelta(days=i), 200000.0 + 100.0 * (i % 30)) for i in range(365)],
    )

    build.build(migrated_db, start, end)

    written = {
        row[0]
        for row in migrated_db.execute("SELECT DISTINCT feature_name FROM features").fetchall()
    }
    assert written == set(registry.BY_NAME), (
        f"the build wrote {sorted(written)}; the registry declares {sorted(registry.BY_NAME)}"
    )


@pytest.mark.integration
def test_a_run_length_is_null_rather_than_zero_across_a_real_gap(migrated_db, seed_readings):
    """The threshold decision, surviving the whole pipeline into the table.

    `thresholds.days_below` is unit-tested directly; this is the same guard checked at the far end,
    because a NULL that the builder produces correctly can still be written as 0 by a parameter
    list that coalesces - and CLAUDE.md § 2's theme 2 asks for verification that crosses the
    boundary where the bug would live.

    Uses the Baton Rouge 2023 gap from `gauge_known_gaps`.
    """
    from tests.features.conftest import BATON_ROUGE

    gap_start = date(2023, 1, 4)
    gap_end = date(2023, 8, 14)

    # A descending run of low days immediately before the gap, and low days after it.
    before = [(gap_start - timedelta(days=n), 100000.0 - n) for n in range(30, 0, -1)]
    after = [(gap_end + timedelta(days=n), 90000.0) for n in range(1, 5)]
    # Plus ordinary values earlier in the year, so the percentile threshold has a record to be
    # taken from and the low days really are low against it. Stopped short of `before` so the two
    # ranges do not overlap - the daily table's primary key would reject the duplicate, and the
    # failure would look like a build defect rather than a fixture one.
    ordinary_end = gap_start - timedelta(days=31)
    ordinary = [
        (date(2022, 1, 1) + timedelta(days=i), 400000.0)
        for i in range((ordinary_end - date(2022, 1, 1)).days + 1)
    ]
    seed_readings.daily(BATON_ROUGE, ordinary + before + after)

    build.build(migrated_db, date(2022, 1, 1), date(2023, 9, 1))

    rows = dict(
        migrated_db.execute(
            "SELECT date, value FROM features"
            " WHERE site_id = %s AND feature_name = 'days_below_p20' AND date > %s"
            " ORDER BY date",
            (BATON_ROUGE, gap_end),
        ).fetchall()
    )

    assert rows, "no post-gap feature rows were written at all"
    first_after = rows[gap_end + timedelta(days=1)]
    assert first_after is None, (
        f"the first day after a seven-month gap carries a run length of {first_after}. A 0 there "
        f"asserts the river came back above the threshold on a day nobody observed."
    )

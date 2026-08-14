"""Unit tier — the gauge registry seed. No database.

The gauge site list is a HUMAN'S MODELLING DECISION (CLAUDE.md § 1). These tests are what makes
that enforceable rather than aspirational: the four approved site IDs are written out here, and
any change to migrations/0004_gauges.sql's seed that adds, removes, or renames a site turns this
file red offline, before anything is applied anywhere.

They read the migration file, not the database, on purpose. A guard that only runs when
DATABASE_URL is set is a guard that does not run in the session where someone adds a fifth gauge.
"""

from app.ingest import gauges as gauges_module
from app.ingest.usgs_client import PARAM_DISCHARGE, PARAM_GAGE_HEIGHT

# The four sites the human approved for Phase 3, measured against the live API on 2026-08-13.
#
# A Cairo, IL site number was investigated for this commit and NOT CONFIRMED. It is absent rather
# than guessed: an unverified identifier in the seed would produce a hard failure on every ingest
# run at best, and silently ingest some other river's gauge at worst.
APPROVED_SITES = {
    "07010000",  # Mississippi River at St. Louis, MO
    "07032000",  # Mississippi River at Memphis, TN
    "07289000",  # Mississippi River at Vicksburg, MS
    "07374000",  # Mississippi River at Baton Rouge, LA
}


def test_seed_contains_exactly_the_four_approved_sites():
    """Exact set equality. Not `>=`, not "contains", not a count.

    A subset assertion passes when a fifth gauge is added, which is the change this test exists
    to catch. A count passes when one site is swapped for another. Set equality is the only form
    that fails for both.
    """
    seeded = {g.usgs_site_id for g in gauges_module.parse_seed()}

    assert seeded == APPROVED_SITES, (
        f"the seeded gauge set does not match the four approved sites.\n"
        f"  added:   {sorted(seeded - APPROVED_SITES)}\n"
        f"  missing: {sorted(APPROVED_SITES - seeded)}\n"
        f"The gauge list is human-owned (CLAUDE.md § 1). If a site genuinely belongs here, the "
        f"human adds it - and this list changes in the same commit, deliberately."
    )


def test_every_seeded_site_declares_available_params_and_cadence():
    """Non-empty, non-NULL, per site.

    Availability and cadence are per site (finding 2 and 3 from 2026-08-13), and the client's
    set-equality assertion is built from `available_params`. A site with an empty parameter list
    would request nothing, receive nothing, and satisfy the assertion trivially - a vacuous pass,
    which is CLAUDE.md § 2's theme 2 in its purest form.
    """
    seeded = gauges_module.parse_seed()
    assert len(seeded) == len(APPROVED_SITES), "wrong number of seeded rows; see the test above"

    for gauge in seeded:
        assert gauge.available_params, (
            f"{gauge.usgs_site_id} declares no available parameters. It would request nothing, "
            f"receive nothing, and report success."
        )
        assert all(
            isinstance(p, str) and p.isdigit() and len(p) == 5
            for p in gauge.available_params
        ), f"{gauge.usgs_site_id} has a malformed parameter code: {gauge.available_params}"

        assert gauge.native_cadence_minutes, (
            f"{gauge.usgs_site_id} declares no native cadence"
        )
        assert gauge.native_cadence_minutes > 0
        assert gauge.iv_record_start is not None, (
            f"{gauge.usgs_site_id} declares no iv_record_start"
        )
        assert gauge.dv_record_start is not None, (
            f"{gauge.usgs_site_id} declares no dv_record_start; the daily backfill would have no "
            f"floor to walk from"
        )
        assert gauge.tier == 1, f"{gauge.usgs_site_id} is not tier 1"

    # The measured cadences are not uniform, and that is the finding that made this column
    # necessary. If they ever all match, the column has stopped documenting anything and this
    # assertion should be the thing that prompts the question.
    cadences = {g.native_cadence_minutes for g in seeded}
    assert len(cadences) > 1, (
        f"every seeded site now claims the same native cadence ({cadences}). The measured values "
        f"were 15, 30, and 60 minutes; a uniform set means the column was filled in from an "
        f"assumption rather than an observation."
    )


def test_no_seeded_site_claims_stage():
    """No site declares 00065. The guard against "restoring" stage without checking availability.

    Stage is absent at Memphis and Vicksburg - USGS states their gage height comes from the USACE
    Memphis District - so a uniform 00065 request hard-fails half the corridor. This commit
    ingests discharge only, and deriving stage from discharge through a rating curve is REJECTED,
    not deferred: USGS publishes ratings as provisional and shifting with channel features, so
    applying a current rating to 2008 discharge produces a stage that gauge never read. A
    fabricated number that looks plausible, in a layer with no confidence gate to catch it.

    The honest path, if stage is wanted later, is USACE Rivergages as its own ingest client with
    its own availability record - and its absence degrades nothing in the meantime.
    """
    for gauge in gauges_module.parse_seed():
        assert PARAM_GAGE_HEIGHT not in gauge.available_params, (
            f"{gauge.usgs_site_id} claims stage ({PARAM_GAGE_HEIGHT}). Stage is not available at "
            f"every seeded site and is out of scope for this commit; a site claiming it will "
            f"hard-fail every ingest run. See CLAUDE.md § 14 and CONTEXT.md before changing this."
        )
        assert gauge.available_params == (PARAM_DISCHARGE,), (
            f"{gauge.usgs_site_id} declares {gauge.available_params}; this commit ingests "
            f"discharge ({PARAM_DISCHARGE}) only."
        )


def test_river_mile_and_coordinates_are_null_rather_than_estimated():
    """Unknown is recorded as unknown. It is not filled in with something plausible.

    river_mile is NULL where unknown by instruction; lat/lon are NULL because this commit's agent
    could not verify them and coordinates typed from recollection are exactly the fabrication
    CLAUDE.md § 1 forbids. A gauge plotted at confidently wrong coordinates is a map that lies,
    with no layer able to notice.

    THIS TEST IS EXPECTED TO BE DELETED, not weakened, when a human fills the coordinates in from
    the USGS site service via a new numbered migration.
    """
    for gauge in gauges_module.parse_seed():
        assert gauge.river_mile is None, (
            f"{gauge.usgs_site_id} has a river_mile of {gauge.river_mile}. If it was measured, "
            f"delete this assertion in the commit that measured it; if it was estimated, remove "
            f"the estimate."
        )
        assert gauge.lat is None and gauge.lon is None, (
            f"{gauge.usgs_site_id} has coordinates ({gauge.lat}, {gauge.lon}). If they came from "
            f"the USGS site service, delete this test in that commit; if they came from "
            f"recollection, they are a fabrication and must go."
        )


def test_requested_pairs_are_built_per_site_not_from_a_global_list():
    """The bridge between the registry and the client's assertion.

    Building the requested set as a cross product of "all sites" and "all parameters" is the
    natural shape and it is wrong here: it would ask Memphis and Vicksburg for a series they do
    not serve, which hard-fails every run. Per site, from that site's own record.
    """
    seeded = {g.usgs_site_id: g for g in gauges_module.parse_seed()}

    memphis = seeded["07032000"]
    assert memphis.requested_pairs() == {("07032000", PARAM_DISCHARGE)}

    # Every pair a gauge asks for is one it declares. Stated as a property over the whole seed so
    # it holds for whatever the registry becomes, not just for today's four rows.
    for gauge in seeded.values():
        for site, param in gauge.requested_pairs():
            assert site == gauge.usgs_site_id
            assert param in gauge.available_params

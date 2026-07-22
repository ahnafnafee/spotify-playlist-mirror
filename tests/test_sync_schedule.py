"""Wall-clock scheduling: passes fire on epoch multiples of the interval, so
the cadence survives container restarts."""

from songmirror.services.sync_service import next_boundary_delay


def test_next_boundary_delay_aligns_to_wall_clock():
    assert next_boundary_delay(10_800, 10_800) == 10_800  # on a boundary -> full interval, no double-fire
    assert next_boundary_delay(10_801, 10_800) == 10_799
    assert next_boundary_delay(5, 900) == 895
    assert next_boundary_delay(0.5, 900) == 899.5         # time.time() is a float

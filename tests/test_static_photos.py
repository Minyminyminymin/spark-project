"""Walk the six-place static photo world, including a blocked move."""

from pathlib import Path

from app.world.base import MoveAction, StopAction, TurnAction
from app.world.static_photos import StaticPhotoWorld, quantize_heading

LAYOUT = Path(__file__).resolve().parent.parent / "photos" / "layout.json"


def _world():
    return StaticPhotoWorld(LAYOUT)


def test_quantize_heading_snaps_to_nearest_90():
    assert quantize_heading(0) == 0
    assert quantize_heading(44) == 0
    assert quantize_heading(46) == 90
    assert quantize_heading(135) == 180
    assert quantize_heading(360) == 0
    assert quantize_heading(-90) == 270


def test_layout_has_six_places():
    world = _world()
    assert len(world._places) == 6


def test_current_view_reports_pose_and_image_size():
    world = _world()
    view = world.get_current_view()
    # Start pose is A(0,0) facing east (90 deg).
    assert (view.pose.x, view.pose.y, view.pose.yaw_deg) == (0.0, 0.0, 90.0)
    assert view.width == 320 and view.height == 240
    assert len(view.image) > 0
    assert view.frame_id == 0
    # frame_id advances on each view.
    assert world.get_current_view().frame_id == 1


def test_walk_the_cycle_a_b_c_d_a():
    """A --east--> B --north--> C --west--> D --south--> A (back to start)."""
    world = _world()

    # Start facing east at A; move to B.
    res = world.execute_action(MoveAction(distance=1.0))
    assert res.success and (res.pose.x, res.pose.y) == (1.0, 0.0)  # B

    # Turn to face north, move to C.
    world.execute_action(TurnAction(degrees=-90))  # 90 -> 0 (north)
    assert world.get_current_view().pose.yaw_deg == 0.0
    res = world.execute_action(MoveAction(distance=1.0))
    assert res.success and (res.pose.x, res.pose.y) == (1.0, 1.0)  # C

    # Turn to face west, move to D.
    world.execute_action(TurnAction(degrees=270))  # 0 -> 270 (west)
    res = world.execute_action(MoveAction(distance=1.0))
    assert res.success and (res.pose.x, res.pose.y) == (0.0, 1.0)  # D

    # Turn to face south, move back to A — closing the cycle.
    world.execute_action(TurnAction(degrees=-90))  # 270 -> 180 (south)
    res = world.execute_action(MoveAction(distance=1.0))
    assert res.success and (res.pose.x, res.pose.y) == (0.0, 0.0)  # A


def test_blocked_move_returns_failure_with_pose_unchanged():
    world = _world()
    # At A, facing east (90). South (180) and west (270) are blocked.
    world.execute_action(TurnAction(degrees=90))  # 90 -> 180 (south, blocked)
    before = world.get_current_view().pose

    res = world.execute_action(MoveAction(distance=1.0))
    assert res.success is False
    assert (res.pose.x, res.pose.y) == (before.x, before.y)
    assert "blocked" in res.message.lower()

    # Pose really is unchanged: a subsequent view agrees.
    after = world.get_current_view().pose
    assert (after.x, after.y, after.yaw_deg) == (before.x, before.y, before.yaw_deg)


def test_stop_action_succeeds_and_reports_reason():
    world = _world()
    res = world.execute_action(StopAction(reason="found it"))
    assert res.success is True
    assert "found it" in res.message

"""Processes this tree starts but does not own.

The crypto recorder and the Databento feed and streamer are separate programs
with their own lifecycles; these modules launch them, watch them and stop
them. Kept apart from `stack/` because a failure here is somebody else's
process misbehaving, not ours.
"""

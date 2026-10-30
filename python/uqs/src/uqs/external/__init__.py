"""Processes this tree starts but does not own.

The cryptorust recorders and the Kafka consumer are separate programs with
their own lifecycles; these modules launch them, watch them and stop them. A
sidecar bundle's connectors are the same kind of module, installed with the
bundle into src/etl/connectors/ rather than kept here (feeds.py, #1776). Kept
apart from `stack/` because a failure here is somebody else's process
misbehaving, not ours.
"""

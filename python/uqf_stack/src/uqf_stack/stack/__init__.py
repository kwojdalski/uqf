"""The running stack: processes, their environment, their output.

Everything here talks to a live fleet - starting and stopping processes,
building the environment they inherit, querying them, reading their logs, and
keeping the process count inside the licence's connection budget. The declared
shape these act on comes from `model/`.
"""

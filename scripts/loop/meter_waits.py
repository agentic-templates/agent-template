"""How long scripts/loop/meter waits between two readings, which scripts/loop/report reads too."""

import datetime as dt

READ_EVERY_SECONDS = {"go": 30 * 60, "watch": 10 * 60, "near": 150}
# Less than an hour, so that the orchestrator wakes while a prompt cache that lasts an hour still holds its context.
WAKE_EVERY = dt.timedelta(minutes=50)
# The time that the meter takes to read the accounts, and that the orchestrator takes to start it again
RESTART_MARGIN = dt.timedelta(minutes=10)
# The meter reads the machine at least this often while the loop runs, so a longer gap between two readings is a time
# when the loop stopped, such as a night.
LONGEST_READING_GAP = max(WAKE_EVERY, dt.timedelta(seconds=max(READ_EVERY_SECONDS.values()))) + RESTART_MARGIN

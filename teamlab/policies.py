"""Policy plug-ins select an index from eligible waiting jobs; engine owns assignment."""

POLICY_VERSION = "1.0.0"


def fcfs(waiting, worker, now, threshold):
    return 0


def preference(waiting, worker, now, threshold):
    if worker["current"] is None or now - waiting[0]["arrival"] >= threshold:
        return 0
    return next((i for i, j in enumerate(waiting) if j["task"] == worker["current"]), 0)


POLICIES = {
    "specialist_fcfs": {"select": fcfs, "partitioned": True},
    "flexible_fcfs": {"select": fcfs, "partitioned": False},
    "type_preference": {"select": preference, "partitioned": False},
    "static_partition": {"select": fcfs, "partitioned": True},
}

"""Banker's algorithm (safety + resource request) and deadlock helpers.

Vectors are dicts keyed by resource type (component_id here).
"""


def is_safe(available, allocation, need):
    """Return (safe, safe_sequence). available: {type: n}; allocation/need: {pid: {type: n}}."""
    work = dict(available)
    finish = {pid: False for pid in allocation}
    sequence = []
    progressed = True
    while progressed:
        progressed = False
        for pid in allocation:
            if finish[pid]:
                continue
            if all(need[pid].get(t, 0) <= work.get(t, 0) for t in need[pid]):
                for t in work:
                    work[t] += allocation[pid].get(t, 0)
                finish[pid] = True
                sequence.append(pid)
                progressed = True
    return all(finish.values()), sequence


def request_resources(pid, request, available, allocation, need):
    """Banker's resource-request algorithm. Returns (granted, reason, safe_sequence)."""
    for t, n in request.items():
        if n > need[pid].get(t, 0):
            return False, "request exceeds declared need", []
    for t, n in request.items():
        if n > available.get(t, 0):
            return False, "insufficient available", []

    new_avail = {t: available.get(t, 0) - request.get(t, 0) for t in set(available) | set(request)}
    new_alloc = {p: dict(v) for p, v in allocation.items()}
    new_need = {p: dict(v) for p, v in need.items()}
    for t, n in request.items():
        new_alloc[pid][t] = new_alloc[pid].get(t, 0) + n
        new_need[pid][t] = new_need[pid].get(t, 0) - n

    safe, sequence = is_safe(new_avail, new_alloc, new_need)
    if not safe:
        return False, "unsafe Banker's state", []
    return True, None, sequence


def find_cycle(graph):
    """graph: {node: [nodes it waits on]}. Returns one cycle as a list of nodes, or []."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color, stack = {}, []

    def dfs(u):
        color[u] = GRAY
        stack.append(u)
        for v in graph.get(u, []):
            state = color.get(v, WHITE)
            if state == GRAY:
                return stack[stack.index(v):]
            if state == WHITE:
                found = dfs(v)
                if found:
                    return found
        stack.pop()
        color[u] = BLACK
        return []

    for node in list(graph):
        if color.get(node, WHITE) == WHITE:
            cycle = dfs(node)
            if cycle:
                return cycle
    return []


def choose_victim(cycle, cost):
    """Abort the cheapest process in the cycle (cost = units held, ties by id)."""
    return min(cycle, key=lambda p: (cost.get(p, 0), p))

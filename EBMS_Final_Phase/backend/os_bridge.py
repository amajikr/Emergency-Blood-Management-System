"""Safe bridge from Flask/Python to the compiled C OS engine (os_algorithms/bin/ebms_os[.exe]).

Rules this module enforces:
  * the engine is started with an argument list, never through a shell
  * the JSON payload is validated and size-limited before it is sent
  * a missing executable, a timeout, a nonzero exit, stderr noise, or malformed stdout all raise OSBridgeError
  * callers that gate real decisions (the reservation workflow) must treat any OSBridgeError as "not approved"
  * stderr and filesystem paths are logged server-side, never returned to browsers
"""
import json
import logging
import os
import subprocess

log = logging.getLogger("ebms.os_bridge")

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.normpath(os.path.join(HERE, "..", "os_algorithms", "bin"))
DEFAULT_EXE = os.path.join(ENGINE_DIR, "ebms_os.exe" if os.name == "nt" else "ebms_os")

MAX_INPUT_BYTES = 64 * 1024
DEFAULT_TIMEOUT_S = 5.0
LONG_TIMEOUT_S = 10.0  # producer-consumer and readers-writers spawn real threads
MODULES = {"scheduler", "banker", "deadlock", "producer_consumer", "readers_writers"}
ALGORITHMS = ("FCFS", "SJF", "SRTF", "PRIORITY_NP", "PRIORITY_P", "RR")
MAX_RESOURCES = 20
MAX_PROCESSES = 50


class OSBridgeError(Exception):
    """kind: invalid_input | unavailable | timeout | failed | malformed_output"""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _exe_argv(binary=None):
    if binary is None:
        binary = os.environ.get("EBMS_OS_BIN") or DEFAULT_EXE
    if isinstance(binary, (list, tuple)):
        return list(binary)
    return [binary]


def run_module(module, payload, *, binary=None, timeout=None):
    """Run one engine module with a JSON payload and return the parsed JSON object."""
    if module not in MODULES:
        raise OSBridgeError("invalid_input", "unknown simulation")
    try:
        text = json.dumps(payload, separators=(",", ":"))
    except (TypeError, ValueError):
        raise OSBridgeError("invalid_input", "input could not be encoded")
    data = text.encode("utf-8")
    if len(data) > MAX_INPUT_BYTES:
        raise OSBridgeError("invalid_input", "input is too large")

    argv = _exe_argv(binary) + [module]
    if len(argv) == 1 and not os.path.isfile(argv[0]):
        raise OSBridgeError("unavailable", "the C simulation engine is not built yet (run build.ps1 or make)")

    try:
        proc = subprocess.run(
            argv,
            input=data,
            capture_output=True,
            timeout=timeout or (LONG_TIMEOUT_S if module in ("producer_consumer", "readers_writers") else DEFAULT_TIMEOUT_S),
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        log.error("engine timeout module=%s", module)
        raise OSBridgeError("timeout", "the simulation took too long and was stopped")
    except (FileNotFoundError, PermissionError, NotADirectoryError):
        log.error("engine missing or not executable module=%s", module)
        raise OSBridgeError("unavailable", "the C simulation engine is not built yet (run build.ps1 or make)")
    except OSError:
        log.exception("engine could not start module=%s", module)
        raise OSBridgeError("unavailable", "the C simulation engine could not be started")

    stderr_text = proc.stderr.decode("utf-8", "replace").strip()
    if stderr_text:
        log.warning("engine stderr module=%s: %s", module, stderr_text[:500])

    try:
        out = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        out = None

    if proc.returncode == 2 and isinstance(out, dict) and out.get("ok") is False:
        raise OSBridgeError("invalid_input", str(out.get("error") or "invalid input"))
    if proc.returncode != 0:
        log.error("engine failed module=%s code=%s", module, proc.returncode)
        raise OSBridgeError("failed", "the simulation engine failed; nothing was approved")
    if not isinstance(out, dict):
        log.error("engine produced malformed output module=%s", module)
        raise OSBridgeError("malformed_output", "the simulation engine returned an unreadable result")
    if out.get("ok") is not True:
        raise OSBridgeError("failed", "the simulation engine did not confirm success")
    return out


# ---------- typed helpers used by the Flask layer and the reservation workflow ----------

def _ints(values, name, lo=0, hi=10000):
    if not isinstance(values, (list, tuple)):
        raise OSBridgeError("invalid_input", f"{name} must be a list")
    out = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, int) or v < lo or v > hi:
            raise OSBridgeError("invalid_input", f"{name} values must be integers from {lo} to {hi}")
        out.append(v)
    return out


def schedule(algorithm, processes, quantum=None, binary=None):
    if algorithm not in ALGORITHMS:
        raise OSBridgeError("invalid_input", "unknown algorithm")
    if not isinstance(processes, list):
        raise OSBridgeError("invalid_input", "processes must be a list")
    payload = {"algorithm": algorithm, "processes": list(processes)}
    if quantum is not None:
        payload["quantum"] = quantum
    return run_module("scheduler", payload, binary=binary)


def _matrix(rows, name):
    if not isinstance(rows, list):
        raise OSBridgeError("invalid_input", f"{name} must be a list of rows")
    return [_ints(row, name) for row in rows]


def banker_check(available, maximum, allocation, process=None, request=None, binary=None):
    """Safety check (process=None) or resource-request decision (process + request vector)."""
    payload = {
        "available": _ints(available, "available"),
        "max": _matrix(maximum, "max"),
        "allocation": _matrix(allocation, "allocation"),
    }
    if process is not None:
        payload["request"] = {"process": int(process), "vector": _ints(request or [], "request")}
    return run_module("banker", payload, binary=binary)


def detect_deadlock(processes, edges, held=None, resource_model="single_instance", binary=None):
    if not isinstance(processes, list) or not isinstance(edges, list):
        raise OSBridgeError("invalid_input", "processes and edges must be lists")
    payload = {
        "processes": list(processes),
        "edges": [{"from": a, "to": b} for a, b in edges],
        "resource_model": resource_model,
    }
    if held is not None:
        if not isinstance(held, dict):
            raise OSBridgeError("invalid_input", "held must be an object")
        payload["held"] = dict(held)
    return run_module("deadlock", payload, binary=binary)


def producer_consumer(capacity, producers, consumers, items_per_producer, binary=None):
    return run_module("producer_consumer", {
        "capacity": capacity, "producers": producers, "consumers": consumers,
        "items_per_producer": items_per_producer,
    }, binary=binary)


def readers_writers(readers, writers, reads_per_reader, writes_per_writer, hold_ms=1, binary=None):
    return run_module("readers_writers", {
        "readers": readers, "writers": writers, "reads_per_reader": reads_per_reader,
        "writes_per_writer": writes_per_writer, "hold_ms": hold_ms,
    }, binary=binary)


def engine_status(binary=None):
    argv = _exe_argv(binary)
    path = argv[0]
    return {"built": os.path.isfile(path), "modules": sorted(MODULES)}

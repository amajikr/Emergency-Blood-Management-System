"""Python bridge tests: fake engines for failure modes, real engine for behavior.
Run from backend/:  python -m unittest test_os_bridge -v   (needs os_algorithms/bin built first)"""
import json
import os
import random
import sys
import tempfile
import textwrap
import unittest

import banker
import os_bridge

ENGINE_BUILT = os.path.isfile(os_bridge.DEFAULT_EXE)


def fake_engine(body):
    """Creates a tiny Python program that stands in for ebms_os. Returns (argv_prefix, cleanup)."""
    tmp = tempfile.TemporaryDirectory()
    path = os.path.join(tmp.name, "fake_engine.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(body))
    return [sys.executable, path], tmp


class BridgeFailureModes(unittest.TestCase):
    def run_fake(self, body, module="scheduler", payload=None, timeout=None):
        argv, tmp = fake_engine(body)
        self.addCleanup(tmp.cleanup)
        return os_bridge.run_module(module, payload or {"x": 1}, binary=argv, timeout=timeout)

    def test_valid_json_is_returned(self):
        out = self.run_fake('import json,sys\nsys.stdin.read()\nprint(json.dumps({"ok": True, "v": 7}))')
        self.assertEqual(out["v"], 7)

    def test_invalid_json_stdout_is_rejected(self):
        with self.assertRaises(os_bridge.OSBridgeError) as cm:
            self.run_fake('import sys\nsys.stdin.read()\nprint("not json at all")')
        self.assertEqual(cm.exception.kind, "malformed_output")

    def test_non_object_output_is_malformed(self):
        with self.assertRaises(os_bridge.OSBridgeError) as cm:
            self.run_fake('import sys\nsys.stdin.read()\nprint("[1,2,3]")')
        self.assertEqual(cm.exception.kind, "malformed_output")

    def test_ok_false_with_exit_zero_is_failure_not_approval(self):
        with self.assertRaises(os_bridge.OSBridgeError) as cm:
            self.run_fake('import json,sys\nsys.stdin.read()\nprint(json.dumps({"ok": False}))')
        self.assertEqual(cm.exception.kind, "failed")

    def test_non_json_stderr_does_not_break_valid_output(self):
        out = self.run_fake('import json,sys\nsys.stdin.read()\nsys.stderr.write("Traceback: /secret/path\\n")\n'
                            'print(json.dumps({"ok": True}))')
        self.assertEqual(out, {"ok": True})
        self.assertNotIn("secret", json.dumps(out))  # stderr never leaks into the result

    def test_nonzero_exit_is_failure(self):
        with self.assertRaises(os_bridge.OSBridgeError) as cm:
            self.run_fake('import sys\nsys.stdin.read()\nsys.stderr.write("boom")\nsys.exit(3)')
        self.assertEqual(cm.exception.kind, "failed")
        self.assertNotIn("boom", cm.exception.message)  # internal detail stays in the log

    def test_exit_two_with_error_json_is_invalid_input_with_message(self):
        with self.assertRaises(os_bridge.OSBridgeError) as cm:
            self.run_fake('import json,sys\nsys.stdin.read()\nprint(json.dumps({"ok": False, "error": "bad burst"}))\n'
                          'sys.exit(2)')
        self.assertEqual(cm.exception.kind, "invalid_input")
        self.assertEqual(cm.exception.message, "bad burst")

    def test_timeout_is_reported(self):
        with self.assertRaises(os_bridge.OSBridgeError) as cm:
            self.run_fake('import sys,time\nsys.stdin.read()\ntime.sleep(5)', timeout=0.5)
        self.assertEqual(cm.exception.kind, "timeout")

    def test_missing_executable_is_unavailable_without_leaking_paths(self):
        with self.assertRaises(os_bridge.OSBridgeError) as cm:
            os_bridge.run_module("scheduler", {}, binary=["/definitely/not/here/ebms_os"])
        self.assertEqual(cm.exception.kind, "unavailable")
        self.assertNotIn("/definitely", cm.exception.message)

    def test_unknown_module_and_bad_payloads_are_invalid_input(self):
        with self.assertRaises(os_bridge.OSBridgeError) as cm:
            os_bridge.run_module("rm -rf /", {})
        self.assertEqual(cm.exception.kind, "invalid_input")
        with self.assertRaises(os_bridge.OSBridgeError) as cm:
            os_bridge.run_module("scheduler", {"x": object()})
        self.assertEqual(cm.exception.kind, "invalid_input")

    def test_oversized_input_is_rejected_before_execution(self):
        with self.assertRaises(os_bridge.OSBridgeError) as cm:
            os_bridge.run_module("scheduler", {"x": "a" * (os_bridge.MAX_INPUT_BYTES + 10)}, binary=["/no/such"])
        self.assertEqual(cm.exception.kind, "invalid_input")

    def test_typed_helpers_validate_before_running(self):
        with self.assertRaises(os_bridge.OSBridgeError):
            os_bridge.schedule("NOPE", [])
        with self.assertRaises(os_bridge.OSBridgeError) as cm:
            os_bridge.banker_check([3, "3"], [[1, 1]], [[0, 0]])
        self.assertEqual(cm.exception.kind, "invalid_input")
        with self.assertRaises(os_bridge.OSBridgeError) as cm:
            os_bridge.banker_check([3], "not rows", [[0]])
        self.assertEqual(cm.exception.kind, "invalid_input")


@unittest.skipUnless(ENGINE_BUILT, "C engine not built: run build.ps1 or make first")
class RealEngine(unittest.TestCase):
    def test_textbook_fcfs_metrics(self):
        procs = [{"pid": "P1", "arrival": 0, "burst": 5}, {"pid": "P2", "arrival": 1, "burst": 3},
                 {"pid": "P3", "arrival": 2, "burst": 8}, {"pid": "P4", "arrival": 3, "burst": 6}]
        out = os_bridge.schedule("FCFS", procs)
        self.assertEqual([p["completion"] for p in out["processes"]], [5, 8, 16, 22])
        self.assertAlmostEqual(out["averages"]["waiting"], 5.75)
        self.assertAlmostEqual(out["averages"]["turnaround"], 11.25)

    def test_textbook_round_robin_gantt(self):
        procs = [{"pid": "P1", "arrival": 0, "burst": 5}, {"pid": "P2", "arrival": 1, "burst": 3},
                 {"pid": "P3", "arrival": 2, "burst": 1}, {"pid": "P4", "arrival": 3, "burst": 2}]
        out = os_bridge.schedule("RR", procs, quantum=2)
        self.assertEqual([(g["pid"], g["start"], g["end"]) for g in out["gantt"]][:3],
                         [("P1", 0, 2), ("P2", 2, 4), ("P3", 4, 5)])
        self.assertAlmostEqual(out["averages"]["response"], 1.75)

    def test_invalid_scheduler_input_is_rejected_by_c(self):
        with self.assertRaises(os_bridge.OSBridgeError) as cm:
            os_bridge.schedule("RR", [{"pid": "P1", "burst": 2}])
        self.assertEqual(cm.exception.kind, "invalid_input")

    def test_banker_textbook_sequence_and_request(self):
        avail = [3, 3, 2]
        mx = [[7, 5, 3], [3, 2, 2], [9, 0, 2], [2, 2, 2], [4, 3, 3]]
        al = [[0, 1, 0], [2, 0, 0], [3, 0, 2], [2, 1, 1], [0, 0, 2]]
        out = os_bridge.banker_check(avail, mx, al)
        self.assertEqual(out["safe_sequence"], [1, 3, 4, 0, 2])
        granted = os_bridge.banker_check(avail, mx, al, process=1, request=[1, 0, 2])
        self.assertTrue(granted["request"]["granted"])
        denied = os_bridge.banker_check(avail, mx, al, process=4, request=[3, 3, 0])
        self.assertEqual(denied["request"]["code"], "REJECTED_UNSAFE")

    def test_deadlock_victim_and_multi_instance_caveat(self):
        out = os_bridge.detect_deadlock(["P1", "P2"], [("P1", "P2"), ("P2", "P1")], held={"P1": 4, "P2": 2})
        self.assertTrue(out["deadlock"])
        self.assertEqual(out["victim"], "P2")
        multi = os_bridge.detect_deadlock(["P1", "P2"], [("P1", "P2"), ("P2", "P1")], resource_model="multi_instance")
        self.assertTrue(multi["cycle_found"])
        self.assertFalse(multi["deadlock"])

    def test_producer_consumer_and_readers_writers_terminate(self):
        pc = os_bridge.producer_consumer(3, 2, 2, 200)
        self.assertTrue(pc["exactly_once"])
        self.assertEqual(pc["threads_joined"], 4)
        rw = os_bridge.readers_writers(3, 2, 10, 5, hold_ms=0)
        self.assertTrue(rw["all_operations_completed"])
        self.assertEqual(rw["writer_exclusion_violations"], 0)


@unittest.skipUnless(ENGINE_BUILT, "C engine not built: run build.ps1 or make first")
class DifferentialBanker(unittest.TestCase):
    """The C Banker's engine must agree with the Python reference (banker.py) on random states."""

    def test_random_states_agree(self):
        rng = random.Random(20261016)
        checked = 0
        for _ in range(60):
            n = rng.randint(1, 5)
            m = rng.randint(1, 3)
            total = [rng.randint(2, 8) for _ in range(m)]
            mx = [[rng.randint(0, total[j]) for j in range(m)] for _ in range(n)]
            al = [[rng.randint(0, mx[i][j]) for j in range(m)] for i in range(n)]
            used = [sum(al[i][j] for i in range(n)) for j in range(m)]
            avail = [max(total[j] - used[j], 0) for j in range(m)]
            names = [f"p{i}" for i in range(n)]
            avail_d = {f"r{j}": avail[j] for j in range(m)}
            alloc_d = {names[i]: {f"r{j}": al[i][j] for j in range(m)} for i in range(n)}
            need_d = {names[i]: {f"r{j}": mx[i][j] - al[i][j] for j in range(m)} for i in range(n)}
            py_safe, _ = banker.is_safe(avail_d, alloc_d, need_d)
            c = os_bridge.banker_check(avail, mx, al)
            self.assertEqual(c["safe"], py_safe)
            checked += 1
            # request decisions for a random process and vector
            i = rng.randrange(n)
            req = [rng.randint(0, 2) for _ in range(m)]
            py_granted, py_reason, _ = banker.request_resources(
                names[i], {f"r{j}": req[j] for j in range(m)}, avail_d, alloc_d, need_d)
            c_req = os_bridge.banker_check(avail, mx, al, process=i, request=req)["request"]
            self.assertEqual(c_req["granted"], py_granted, (avail, mx, al, i, req))
            if not py_granted:
                expected = {"request exceeds declared need": "REJECTED_EXCEEDS_NEED",
                            "insufficient available": "REJECTED_INSUFFICIENT",
                            "unsafe Banker's state": "REJECTED_UNSAFE"}[py_reason]
                self.assertEqual(c_req["code"], expected)
        self.assertEqual(checked, 60)


if __name__ == "__main__":
    unittest.main()

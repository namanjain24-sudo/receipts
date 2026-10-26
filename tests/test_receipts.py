#!/usr/bin/env python3
"""End-to-end tests: build a fake transcript + ledger, run the real hooks.

These drive hooks/gate.py and hooks/record.py as subprocesses over stdin, the
same way Claude Code does, so what is tested is the actual contract and not a
convenient in-process shortcut.
"""
import json, os, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "hooks", "gate.py")
RECORD = os.path.join(ROOT, "hooks", "record.py")


def write_transcript(path, final_text, agent_results=()):
    rows = [{"type": "user", "message": {"role": "user", "content": "do the thing"}}]
    for i, res in enumerate(agent_results):
        rows.append({"type": "assistant", "requestId": "r%d" % i, "message": {
            "id": "m%d" % i, "content": [
                {"type": "tool_use", "id": "a%d" % i, "name": "Agent", "input": {}}]}})
        rows.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a%d" % i,
             "content": [{"type": "text", "text": res}]}]}})
    rows.append({"type": "assistant", "requestId": "rf", "message": {
        "id": "mf", "content": [{"type": "text", "text": final_text}]}})
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def run_record(home, sid, tool, tin, tout=None):
    ev = {"session_id": sid, "tool_name": tool, "tool_input": tin,
          "tool_response": tout or {}}
    env = dict(os.environ, RECEIPTS_HOME=home)
    subprocess.run([sys.executable, RECORD], input=json.dumps(ev),
                   text=True, env=env, capture_output=True)


def run_gate(home, sid, tpath, cwd, stop_active=False):
    ev = {"session_id": sid, "transcript_path": tpath, "cwd": cwd,
          "hook_event_name": "Stop", "stop_hook_active": stop_active}
    env = dict(os.environ, RECEIPTS_HOME=home)
    p = subprocess.run([sys.executable, GATE], input=json.dumps(ev),
                       text=True, env=env, capture_output=True)
    return p.returncode, p.stderr


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def t_claim_with_no_check_is_blocked(home, tmp):
    write_transcript(tmp + "/t.jsonl", "All tests pass now.")
    code, err = run_gate(home, "s1", tmp + "/t.jsonl", tmp)
    assert code == 2, "expected block, got %d" % code
    assert "nothing in this session ran a test check" in err, err
    return "claim with no check -> blocked"


@case
def t_claim_with_passing_check_is_allowed(home, tmp):
    run_record(home, "s2", "Bash", {"command": "pytest -q"},
               {"stdout": "==== 120 passed in 3.1s ====", "stderr": ""})
    write_transcript(tmp + "/t2.jsonl", "All tests pass now.")
    code, err = run_gate(home, "s2", tmp + "/t2.jsonl", tmp)
    assert code == 0, "expected allow, got %d: %s" % (code, err)
    return "claim backed by a passing run -> allowed"


@case
def t_failing_check_contradicting_claim_is_blocked(home, tmp):
    run_record(home, "s3", "Bash", {"command": "pytest -q"},
               {"stdout": "==== 4 failed, 116 passed in 2.1s ====", "stderr": ""})
    write_transcript(tmp + "/t3.jsonl", "Fixed it, all tests pass.")
    code, err = run_gate(home, "s3", tmp + "/t3.jsonl", tmp)
    assert code == 2, "expected block, got %d" % code
    assert "reported a failure" in err and "4 failed" in err, err
    return "claim contradicted by real output -> blocked"


@case
def t_stale_evidence_is_blocked(home, tmp):
    run_record(home, "s4", "Bash", {"command": "pytest -q"},
               {"stdout": "==== 120 passed in 3.1s ====", "stderr": ""})
    run_record(home, "s4", "Edit", {"file_path": "/app/core.py"})
    write_transcript(tmp + "/t4.jsonl", "All tests pass.")
    code, err = run_gate(home, "s4", tmp + "/t4.jsonl", tmp)
    assert code == 2, "expected block, got %d" % code
    assert "changed after it" in err and "core.py" in err, err
    return "edit after the last green run -> blocked as stale"


@case
def t_bash_write_also_counts_as_mutation(home, tmp):
    run_record(home, "s5", "Bash", {"command": "pytest -q"},
               {"stdout": "==== 7 passed in 0.4s ====", "stderr": ""})
    run_record(home, "s5", "Bash", {"command": "sed -i '' s/a/b/ src/main.py"},
               {"stdout": "", "stderr": ""})
    write_transcript(tmp + "/t5.jsonl", "All tests pass.")
    code, err = run_gate(home, "s5", tmp + "/t5.jsonl", tmp)
    assert code == 2, "expected block, got %d" % code
    assert "changed after it" in err, err
    return "sed -i after a green run -> blocked as stale"


@case
def t_subagent_failure_is_surfaced(home, tmp):
    run_record(home, "s6", "Bash", {"command": "pytest -q"},
               {"stdout": "==== 12 passed in 1.0s ====", "stderr": ""})
    write_transcript(tmp + "/t6.jsonl", "All tests pass, done.",
                     agent_results=["I was unable to fix the migration step."])
    code, err = run_gate(home, "s6", tmp + "/t6.jsonl", tmp)
    assert code == 2, "expected block, got %d" % code
    assert "subagent" in err, err
    return "subagent failure dropped from the summary -> blocked"


@case
def t_no_claim_is_never_blocked(home, tmp):
    write_transcript(tmp + "/t7.jsonl", "I read the file and here is what it does.")
    code, err = run_gate(home, "s7", tmp + "/t7.jsonl", tmp)
    assert code == 0, "expected allow, got %d: %s" % (code, err)
    return "message with no claim -> never blocked"


@case
def t_hedged_claim_is_not_blocked(home, tmp):
    write_transcript(tmp + "/t8.jsonl", "The tests should pass once you install deps.")
    code, err = run_gate(home, "s8", tmp + "/t8.jsonl", tmp)
    assert code == 0, "expected allow, got %d: %s" % (code, err)
    return "hedged claim -> not blocked"


@case
def t_stop_hook_active_never_loops(home, tmp):
    write_transcript(tmp + "/t9.jsonl", "All tests pass now.")
    code, err = run_gate(home, "s9", tmp + "/t9.jsonl", tmp, stop_active=True)
    assert code == 0, "expected allow on re-entry, got %d" % code
    return "stop_hook_active -> no infinite loop"


@case
def t_unreadable_test_output_is_not_a_pass(home, tmp):
    run_record(home, "s10", "Bash", {"command": "pytest -q"},
               {"stdout": "collecting ...", "stderr": ""})
    write_transcript(tmp + "/t10.jsonl", "All tests pass.")
    code, err = run_gate(home, "s10", tmp + "/t10.jsonl", tmp)
    assert code == 2, "expected block, got %d" % code
    assert "could not be read" in err, err
    return "test ran but printed no summary -> not treated as a pass"


@case
def t_disabled_config_is_respected(home, tmp):
    os.makedirs(tmp + "/off/.claude", exist_ok=True)
    with open(tmp + "/off/.claude/receipts.json", "w") as fh:
        json.dump({"enabled": False}, fh)
    write_transcript(tmp + "/t11.jsonl", "All tests pass now.")
    code, err = run_gate(home, "s11", tmp + "/t11.jsonl", tmp + "/off")
    assert code == 0, "expected allow when disabled, got %d" % code
    return "enabled:false -> gate is off"


@case
def t_broken_input_never_wedges_the_session(home, tmp):
    env = dict(os.environ, RECEIPTS_HOME=home)
    p = subprocess.run([sys.executable, GATE], input="not json at all",
                       text=True, env=env, capture_output=True)
    assert p.returncode == 0, "garbage input must not block"
    p2 = subprocess.run([sys.executable, RECORD], input="}{",
                        text=True, env=env, capture_output=True)
    assert p2.returncode == 0, "garbage input must not fail the tool call"
    return "malformed hook input -> exits 0, never wedges"


def _mkrepo(tmp, name, app_head, test_src, app_now):
    """A git repo whose HEAD holds `app_head` and whose worktree holds `app_now`."""
    d = os.path.join(tmp, name)
    os.makedirs(d, exist_ok=True)
    q = dict(cwd=d, capture_output=True, text=True)
    subprocess.run(["git", "init", "-q", "."], **q)
    subprocess.run(["git", "config", "user.email", "t@t.t"], **q)
    subprocess.run(["git", "config", "user.name", "t"], **q)
    open(os.path.join(d, "app.py"), "w").write(app_head)
    open(os.path.join(d, "test_app.py"), "w").write(test_src)
    subprocess.run(["git", "add", "-A"], **q)
    subprocess.run(["git", "commit", "-qm", "base"], **q)
    open(os.path.join(d, "app.py"), "w").write(app_now)
    return d


REAL_TEST = ("import unittest, app\n\nclass T(unittest.TestCase):\n"
             "    def test_add(self):\n        self.assertEqual(app.add(2, 3), 5)\n")
INERT_TEST = ("import unittest, app\n\nclass T(unittest.TestCase):\n"
              "    def test_loads(self):\n        self.assertTrue(callable(app.add))\n")
BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"


@case
def t_discriminating_test_is_proven(home, tmp):
    sys.path.insert(0, os.path.join(ROOT, "lib"))
    import inert
    d = _mkrepo(tmp, "repo_real", BUGGY, REAL_TEST, FIXED)
    status, detail = inert.check("python3 -m unittest -q", d)
    assert status == "PROVEN", "%s: %s" % (status, detail[:200])
    return "test that fails at HEAD -> PROVEN"


@case
def t_inert_test_is_caught(home, tmp):
    sys.path.insert(0, os.path.join(ROOT, "lib"))
    import inert
    d = _mkrepo(tmp, "repo_inert", BUGGY, INERT_TEST, FIXED)
    # the agent's view: green, so it would report "tests pass"
    p = subprocess.run("python3 -m unittest -q", cwd=d, shell=True, capture_output=True, text=True)
    assert p.returncode == 0, "the inert test should be green in the worktree"
    status, detail = inert.check("python3 -m unittest -q", d)
    assert status == "INERT", "%s: %s" % (status, detail[:200])
    return "green test that is green at HEAD too -> INERT"


@case
def t_inert_result_blocks_the_claim(home, tmp):
    run_record(home, "s12", "Bash", {"command": "python3 -m unittest -q"},
               {"stdout": "OK", "stderr": "Ran 1 test in 0.00s\n\nOK"})
    ledger_home = home
    env = dict(os.environ, RECEIPTS_HOME=ledger_home)
    sys.path.insert(0, os.path.join(ROOT, "lib"))
    import ledger as L
    os.environ["RECEIPTS_HOME"] = ledger_home
    import importlib; importlib.reload(L)
    L.append("s12", {"kind": "inert", "cmd": "python3 -m unittest -q",
                     "status": "INERT", "detail": "passes at HEAD too"})
    write_transcript(tmp + "/t12.jsonl", "All tests pass, the fix works.")
    code, err = run_gate(home, "s12", tmp + "/t12.jsonl", tmp)
    assert code == 2, "expected block, got %d: %s" % (code, err)
    assert "passes at HEAD too" in err, err
    return "INERT on the record -> the claim is blocked"


@case
def t_throwaway_redirects_are_not_mutations(home, tmp):
    """A test log written to /tmp must not make a fresh green run look stale."""
    run_record(home, "s13", "Bash", {"command": "pytest -q"},
               {"stdout": "==== 9 passed in 0.5s ====", "stderr": ""})
    run_record(home, "s13", "Bash", {"command": "pytest -q > /tmp/out.txt"},
               {"stdout": "", "stderr": ""})
    write_transcript(tmp + "/t13.jsonl", "All tests pass.")
    code, err = run_gate(home, "s13", tmp + "/t13.jsonl", tmp)
    assert code == 0, "redirect to /tmp should not count as a mutation: %s" % err
    return "redirect to /tmp -> not a mutation, claim still allowed"


@case
def t_real_file_redirect_is_a_mutation(home, tmp):
    run_record(home, "s14", "Bash", {"command": "pytest -q"},
               {"stdout": "==== 9 passed in 0.5s ====", "stderr": ""})
    run_record(home, "s14", "Bash", {"command": "cat > src/app.py <<'XX'"},
               {"stdout": "", "stderr": ""})
    write_transcript(tmp + "/t14.jsonl", "All tests pass.")
    code, err = run_gate(home, "s14", tmp + "/t14.jsonl", tmp)
    assert code == 2, "writing a source file should make the run stale"
    assert "changed after it" in err, err
    return "heredoc into a source file -> mutation, claim blocked"


@case
def t_old_subagent_failure_does_not_block_forever(home, tmp):
    """A subagent failure from an earlier turn has already been dealt with."""
    run_record(home, "s15", "Bash", {"command": "pytest -q"},
               {"stdout": "==== 9 passed in 0.5s ====", "stderr": ""})
    path = tmp + "/t15.jsonl"
    rows = [
        {"type": "user", "message": {"role": "user", "content": "first task"}},
        {"type": "assistant", "requestId": "r0", "message": {"id": "m0", "content": [
            {"type": "tool_use", "id": "a0", "name": "Agent", "input": {}}]}},
        {"type": "user", "toolUseResult": {}, "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a0",
             "content": [{"type": "text", "text": "I was unable to fix the migration."}]}]}},
        {"type": "assistant", "requestId": "r1", "message": {"id": "m1", "content": [
            {"type": "text", "text": "That subagent failed, I handled it myself."}]}},
        # a fresh human turn: everything above is settled history
        {"type": "user", "message": {"role": "user", "content": "now run the tests"}},
        {"type": "assistant", "requestId": "r2", "message": {"id": "m2", "content": [
            {"type": "text", "text": "All tests pass."}]}},
    ]
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    code, err = run_gate(home, "s15", path, tmp)
    assert code == 0, "a settled subagent failure must not block: %s" % err
    return "subagent failure before the last human turn -> not re-raised"


@case
def t_live_failure_outranks_a_recorded_inert(home, tmp):
    sys.path.insert(0, os.path.join(ROOT, "lib"))
    os.environ["RECEIPTS_HOME"] = home
    import ledger as L, importlib; importlib.reload(L)
    run_record(home, "s16", "Bash", {"command": "pytest -q"},
               {"stdout": "==== 2 failed, 8 passed in 1.0s ====", "stderr": ""})
    L.append("s16", {"kind": "inert", "cmd": "pytest -q", "status": "INERT"})
    write_transcript(tmp + "/t16.jsonl", "All tests pass.")
    code, err = run_gate(home, "s16", tmp + "/t16.jsonl", tmp)
    assert code == 2, "expected block, got %d" % code
    assert "[failing]" in err, "a live failure should be reported first:\n%s" % err
    return "live failure outranks a recorded inert verdict"


@case
def t_inert_block_tells_you_what_to_do(home, tmp):
    sys.path.insert(0, os.path.join(ROOT, "lib"))
    os.environ["RECEIPTS_HOME"] = home
    import ledger as L, importlib; importlib.reload(L)
    run_record(home, "s17", "Bash", {"command": "pytest -q"},
               {"stdout": "==== 10 passed in 1.0s ====", "stderr": ""})
    L.append("s17", {"kind": "inert", "cmd": "pytest -q", "status": "INERT"})
    write_transcript(tmp + "/t17.jsonl", "All tests pass.")
    code, err = run_gate(home, "s17", tmp + "/t17.jsonl", tmp)
    assert code == 2, "expected block, got %d" % code
    assert "fails when your change is reverted" in err, \
        "the inert hint must reach the model:\n%s" % err
    return "inert block carries the inert-specific next step"


@case
def t_family_is_decided_by_the_program_not_the_line(home, tmp):
    """Regressions found by replaying 1,355 real turns."""
    sys.path.insert(0, os.path.join(ROOT, "lib"))
    import checks
    table = [
        # the whole-line regex that used to live here called these checks
        ("cd /Users/x/brain && cp results.csv /tmp/out.csv", None),
        ("echo 'curl is great'", None),
        ("for f in providers/celery/src/x; do echo $f; done", None),
        ("grep -r curl .", None),
        ("node server.js &", None),
        # an action is not a verification
        ("git push origin main", None),
        ("git status --short", "vcs"),
        # wrappers must be unwrapped
        ("npx jest src/", "test"), ("uv run pytest -q", "test"),
        ("python3 -m pytest -q", "test"), ("cd app && npm test", "test"),
        ("pnpm exec tsc --noEmit", "lint"), ("mvn -q clean test", "test"),
        ("python3 manage.py test", "test"),
        # formatters rewrite files; only their check mode verifies anything
        ("uv run ruff format src/", None), ("ruff check src/", "lint"),
        ("npx prettier --write x.js", None), ("npx prettier --check x.js", "lint"),
        ("black .", None), ("black --check .", "lint"),
        ("cargo fmt", None), ("cargo fmt --check", "lint"),
        ("npx eslint --fix src/", None), ("npx eslint src/", "lint"),
        ("gofmt -w .", None),
        # a script whose name says it is a test counts, but the shell builtin
        # `test -f x` does not, and neither does opening the file in an editor
        ("python3 tests/test_receipts.py", "test"), ("./run-tests.sh", "test"),
        ("node spec/index.js", "test"), ("bash scripts/test.sh", "test"),
        ("deno test", "test"),
        ("test -f config.yml && echo ok", None), ('test -z "$X"', None),
        ("cat tests/test_x.py", None), ("vim test.py", None),
        ("bash -c 'echo hi'", None),
    ]
    bad = [(c, checks.family(c), e) for c, e in table if checks.family(c) != e]
    assert not bad, "misclassified: %s" % bad[:3]
    return "%d command classifications, program-token based" % len(table)


@case
def t_generic_failure_sniffing_stays_in_its_lane(home, tmp):
    """An "error:" inside unrelated output must not become a failed check."""
    sys.path.insert(0, os.path.join(ROOT, "lib"))
    import checks
    noisy = "fetched 300 rows\nerror: field is null for 2 rows\ndone"
    v, _, _, _ = checks.verdict("curl -s https://api.example.com/x", noisy, "")
    assert v != checks.FAIL, "http output must not be failure-sniffed: %s" % v
    v2, _, _, _ = checks.verdict("pytest -q", noisy, "")
    assert v2 == checks.FAIL, "a test printing error: should fail"
    return "failure sniffing applies to test/build/lint only"


@case
def t_plain_pass_fail_summary_is_read(home, tmp):
    """Hand-rolled runners print `26 passed, 0 failed` and nothing else."""
    sys.path.insert(0, os.path.join(ROOT, "lib"))
    import checks
    v, p, f, _ = checks.verdict("python3 tests/test_receipts.py", "26 passed, 0 failed", "")
    assert (v, p, f) == (checks.PASS, 26, 0), (v, p, f)
    v, p, f, _ = checks.verdict("python3 tests/test_receipts.py", "24 passed, 2 failed", "")
    assert (v, p, f) == (checks.FAIL, 24, 2), (v, p, f)
    return "plain `N passed, M failed` summaries are read"


@case
def t_housekeeping_does_not_make_a_run_stale(home, tmp):
    """A README edit cannot turn a green test red."""
    run_record(home, "s18", "Bash", {"command": "pytest -q"},
               {"stdout": "==== 9 passed in 0.5s ====", "stderr": ""})
    run_record(home, "s18", "Edit", {"file_path": "/repo/README.md"})
    run_record(home, "s18", "Bash", {"command": "rm /tmp/scratch.txt"},
               {"stdout": "", "stderr": ""})
    run_record(home, "s18", "Bash", {"command": "mkdir -p build"},
               {"stdout": "", "stderr": ""})
    write_transcript(tmp + "/t18.jsonl", "All tests pass.")
    code, err = run_gate(home, "s18", tmp + "/t18.jsonl", tmp)
    assert code == 0, "docs and housekeeping must not count as stale:\n%s" % err
    return "README edit + temp-file churn -> run is still fresh"


@case
def t_source_edit_still_makes_a_run_stale(home, tmp):
    run_record(home, "s19", "Bash", {"command": "pytest -q"},
               {"stdout": "==== 9 passed in 0.5s ====", "stderr": ""})
    run_record(home, "s19", "Edit", {"file_path": "/repo/README.md"})
    run_record(home, "s19", "Bash", {"command": "sed -i '' s/a/b/ src/core.py"},
               {"stdout": "", "stderr": ""})
    write_transcript(tmp + "/t19.jsonl", "All tests pass.")
    code, err = run_gate(home, "s19", tmp + "/t19.jsonl", tmp)
    assert code == 2, "a source edit must still make the run stale"
    assert "src/core.py" in err, "the message should name the file:\n%s" % err
    return "sed -i on a source file -> stale, and the file is named"


@case
def t_vague_claim_without_any_change_is_left_alone(home, tmp):
    """"scan ho gaya" in a read-only turn is not a claim about code."""
    write_transcript(tmp + "/t20.jsonl", "Poora scan ho gaya, sab verify ho gaya.")
    code, err = run_gate(home, "s20", tmp + "/t20.jsonl", tmp)
    assert code == 0, "a read-only turn must not be gated:\n%s" % err
    return "vague claim, nothing edited -> not gated"


@case
def t_specific_claim_is_gated_even_without_changes(home, tmp):
    write_transcript(tmp + "/t21.jsonl", "All tests pass.")
    code, err = run_gate(home, "s21", tmp + "/t21.jsonl", tmp)
    assert code == 2, "a falsifiable claim is checked regardless of edits"
    return "specific claim, nothing edited -> still gated"


def main():
    tmp = tempfile.mkdtemp(prefix="receipts-test-")
    home = os.path.join(tmp, "ledgers")
    passed, failed = 0, []
    for fn in CASES:
        try:
            msg = fn(home, tmp)
            print("  PASS  %s" % msg)
            passed += 1
        except AssertionError as e:
            print("  FAIL  %s: %s" % (fn.__name__, e))
            failed.append(fn.__name__)
        except Exception as e:
            print("  ERROR %s: %r" % (fn.__name__, e))
            failed.append(fn.__name__)
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (passed, len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

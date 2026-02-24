# ttygeist Integration Test Plan -- CircuitPython REPL

This document is a step-by-step test plan for an LLM with ttygeist MCP tool
access. It validates the full pipeline: MCP tools -> serial connection ->
CircuitPython REPL -> serial output -> buffer -> MCP tools.

Each test writes a command to the REPL via `serial_write`, then reads the
response via `serial_read` or `buffer_inspect` and checks for expected output.

## Prerequisites

- ttygeist is running and connected to a CircuitPython device over USB serial.
- The device is at a REPL prompt (`>>>`). If not, send Ctrl+C first (see Test 0).
- You have access to the ttygeist MCP tools: `serial_write`, `serial_read`,
  `serial_status`, `buffer_inspect`, `buffer_clear`, `serial_reconnect`.

## How to Use This Plan

Work through the tests in order. Each test has:
- **Action**: what to do (which MCP tool to call, with what arguments).
- **Expected**: what the response should contain.
- **Pass criteria**: how to determine success.

If a test fails, stop and report which test failed and what you observed.
Do not skip ahead -- later tests may depend on earlier state.

---

## Test 0: Verify Connection

Confirm ttygeist can see the device before doing anything else.

**Action**: Call `serial_status`.

**Expected**: `connected` is `true`. The port path and baud rate are populated.

**Pass**: Connection is live. If not connected, stop here -- the device may not
be plugged in or the port path may be wrong.

---

## Test 1: Reach the REPL Prompt

Ensure the device is at an interactive REPL prompt.

**Action**: Call `buffer_clear` to start clean. Then call `serial_write` with
`data: "\x03"` (Ctrl+C) and `add_newline: false`. Wait a moment, then call
`serial_read` with `lines: 5`.

**Expected**: Output contains `>>>` (the CircuitPython REPL prompt). You may
also see a `KeyboardInterrupt` if code was running.

**Pass**: The `>>>` prompt appears in the output.

---

## Test 2: Simple Expression

Verify round-trip: send a Python expression, read the result.

**Action**: Call `buffer_clear`. Then call `serial_write` with
`data: "2 + 2"` and `add_newline: true`. Wait briefly, then call
`serial_read` with `lines: 5`.

**Expected**: Output contains `4` on its own line, followed by `>>>`.

**Pass**: The computed result `4` appears in the output.

---

## Test 3: Print Statement

Verify stdout capture through the serial buffer.

**Action**: Call `buffer_clear`. Then call `serial_write` with
`data: "print('hello from ttygeist')"` and `add_newline: true`. Wait briefly,
then call `serial_read` with `lines: 5`.

**Expected**: Output contains `hello from ttygeist`.

**Pass**: The exact printed string appears in the read output.

---

## Test 4: Multi-line Output

Verify the buffer handles multiple lines from a single command.

**Action**: Call `buffer_clear`. Then call `serial_write` with
`data: "for i in range(5): print(f'line {i}')"` and `add_newline: true`.
Wait briefly, then call `serial_read` with `lines: 10`.

**Expected**: Output contains `line 0` through `line 4`, each on its own line.

**Pass**: All five lines are present in order.

---

## Test 5: Buffer Inspect (Non-destructive Read)

Verify that `buffer_inspect` returns recent output without consuming it.

**Action**: Call `buffer_inspect` with `tail_lines: 3`. Then call
`buffer_inspect` with `tail_lines: 3` again.

**Expected**: Both calls return the same content (the last 3 lines in the
buffer). The buffer is not modified by inspection.

**Pass**: Both responses are identical.

---

## Test 6: Buffer Clear

Verify that `buffer_clear` empties the buffer.

**Action**: Call `buffer_clear`. Note the count of cleared lines. Then call
`serial_read` with `lines: 10`.

**Expected**: `buffer_clear` returns a count > 0 (from previous tests).
The subsequent `serial_read` returns an empty or near-empty result (may
contain just a prompt if the device echoed one).

**Pass**: Clear count is positive, and the buffer is effectively empty after.

---

## Test 7: Error Handling

Verify that Python errors on the device are captured in the buffer.

**Action**: Call `buffer_clear`. Then call `serial_write` with
`data: "1/0"` and `add_newline: true`. Wait briefly, then call
`serial_read` with `lines: 10`.

**Expected**: Output contains `ZeroDivisionError` and a traceback.

**Pass**: The error type appears in the output.

---

## Test 8: Import and Use a Module

Verify that the REPL can import modules and the output is captured.

**Action**: Call `buffer_clear`. Then call `serial_write` with
`data: "import sys; print(sys.implementation.name)"` and `add_newline: true`.
Wait briefly, then call `serial_read` with `lines: 5`.

**Expected**: Output contains `circuitpython`.

**Pass**: The implementation name confirms this is a CircuitPython device.

---

## Test 9: Reconnect

Verify the reconnect tool works without losing the device.

**Action**: Call `serial_reconnect`. Wait a few seconds for the reconnection
cycle to complete. Then call `serial_status`.

**Expected**: `serial_reconnect` returns a success message. After waiting,
`serial_status` shows `connected: true`.

**Pass**: Connection is re-established. Note that the REPL may have reset --
you may need to send Ctrl+C again to get a clean prompt.

---

## Test 10: Write with Newline Variants

Verify that `add_newline` behaves correctly.

**Action**: Call `buffer_clear`. Then call `serial_write` with
`data: "print('no newline test')"` and `add_newline: false`. Wait briefly,
then call `serial_write` with `data: "\r\n"` and `add_newline: false` to
manually send the line ending. Wait, then call `serial_read` with `lines: 5`.

**Expected**: Output contains `no newline test`. The command was not executed
until the manual CRLF was sent.

**Pass**: The output appears only after the explicit newline.

---

## Test 11: Create and Run code.py

Verify control characters work by writing a small program, triggering a soft
reload, observing its output, then breaking back into the REPL.

**Action**:

1. Enter paste mode by sending Ctrl+E: call `serial_write` with
   `data: "\x05"` and `add_newline: false`. Wait briefly and confirm the
   device responds with `paste mode; Ctrl-C to cancel, Ctrl-D to finish`.

2. Paste a small program line by line. Call `serial_write` for each line
   with `add_newline: true`:
   - `import time`
   - `for i in range(3):`
   - `    print(f"tick {i}")`
   - `    time.sleep(0.5)`
   - `print("done")`

3. End paste mode by sending Ctrl+D: call `serial_write` with
   `data: "\x04"` and `add_newline: false`.

4. Wait 3 seconds for the program to run, then call `serial_read`
   with `lines: 15`.

5. After confirming output, send Ctrl+C to return to the REPL:
   call `serial_write` with `data: "\x03"` and `add_newline: false`.
   Wait briefly, then call `serial_read` with `lines: 5`.

**Expected**: Step 4 output contains `tick 0`, `tick 1`, `tick 2`, and `done`.
Step 5 output contains `>>>`.

**Pass**: The pasted program ran, produced expected output, and Ctrl+C
returned to the REPL prompt.

---

## Test 12: Ride-along -- CLI Show

Verify that the human CLI sees the same data the LLM does.

**Action**: Call `buffer_clear`. Then call `serial_write` with
`data: "print('cli visibility check')"` and `add_newline: true`. Wait
briefly, then call `serial_read` with `lines: 5` to confirm the output
from your side.

Now ask the user:

> Please run `ttygeist-cli show -n 5` and confirm you see
> "cli visibility check" in the output.

**Expected**: The user confirms they see the string in their CLI output.

**Pass**: Both the LLM (via MCP) and the human (via CLI) see the same
buffered content.

---

## Test 13: Ride-along -- CLI Tail

Verify that the CLI tail stream captures live output.

**Action**: Ask the user:

> Please start `ttygeist-cli tail -n 5` in a terminal and let me know
> when it is running.

Once the user confirms tail is running, call `serial_write` with
`data: "print('tail test alpha'); print('tail test bravo')"` and
`add_newline: true`. Wait briefly, then call `serial_read` with
`lines: 5` to confirm from your side.

Now ask the user:

> Do you see "tail test alpha" and "tail test bravo" in your tail output?

**Expected**: The user confirms both lines appeared in real time.

**Pass**: Live serial output is visible to both the LLM and the human
simultaneously.

---

## Test 14: Ride-along -- User Keyboard Input

Verify that human keystrokes entered via the CLI terminal are visible
to the LLM through the serial buffer.

**Action**: Call `buffer_clear`. Then ask the user:

> Please open `ttygeist-cli terminal` and type the following at the
> REPL prompt, then press Enter:
>
> `print('human says hello')`
>
> Let me know when you have pressed Enter.

Once the user confirms, wait briefly, then call `serial_read` with
`lines: 10`.

**Expected**: Output contains `human says hello` -- the string printed
by the command the user typed.

**Pass**: The LLM can observe the results of human-initiated commands
through the shared serial buffer.

---

## Test 15: Ride-along -- Concurrent Access

Verify that LLM writes and human observation coexist without interference.

**Action**: Ask the user:

> Please keep `ttygeist-cli terminal` open. I am going to send a few
> commands. Watch your terminal and confirm you see the output appear.

Then call `serial_write` with `data: "print('agent action 1')"` and
`add_newline: true`. Wait briefly. Call `serial_write` with
`data: "print('agent action 2')"` and `add_newline: true`. Wait briefly.
Call `serial_read` with `lines: 10` to confirm from your side.

Now ask the user:

> Did you see "agent action 1" and "agent action 2" appear in your
> terminal session?

**Expected**: The user confirms both lines appeared. Your `serial_read`
also contains both lines.

**Pass**: LLM-initiated writes are visible to the human in real time,
and the LLM can read its own output back. No data was lost or garbled
by concurrent access.

---

## Summary

If all 16 tests pass (0-15), the ttygeist MCP pipeline is fully validated:
- Serial connection management (connect, reconnect, status)
- Writing to the device (with and without automatic newlines)
- Reading buffered output (destructive and non-destructive)
- Buffer lifecycle (clear, inspect, read)
- Multi-line output and error capture
- Control characters (Ctrl+C, Ctrl+D, Ctrl+E / paste mode)
- Human CLI visibility (show, tail)
- Human keyboard input captured by LLM
- Concurrent human/LLM access without interference

Report results as: `N/16 tests passed`. Include details for any failures.

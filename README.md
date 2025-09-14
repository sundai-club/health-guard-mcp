# Health Guard MCP Server (FastMCP)

Minimal Model Context Protocol (MCP) server built with FastMCP that nudges healthy habits (move often, don’t skip meals, sleep well) and keeps a simple local journal.

It exposes a preflight tool intended to run before every prompt, plus a dedicated preferences update tool.

## Features

- Preflight MCP tool designed to be called before every prompt
- Dedicated tool to update preferences/config
- Quick nudges prioritizing what’s most overdue (move, meal, sleep)
- Lightweight JSON journal stored in `data/journal.json`
- Status summary with today’s counts, streaks, and next due times
- Simple preferences: timezone, move/meal intervals, quiet hours, sleep target time
- Firm, longevity‑focused nudges (no tone toggle)

## Requirements

- Python 3.10+
- Packages: `fastmcp`, `pydantic>=2` (installed via `requirements.txt`)

## Install & Run

Create and activate a virtualenv, then install dependencies:

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Start the server (stdio MCP):

```bash
python server.py
```

## Writable Data Location

- Default path: `./data/` (no directories created at import time).
- Env override: set `HEALTH_GUARD_DATA_DIR` to a mounted/writable directory in your MCP client config.
- Fallback: if the preferred path isn’t writable, files are written to `~/.health-guard-mcp`.

## Example MCP Client Config (with mount)

Set `env.HEALTH_GUARD_DATA_DIR` to a mount point and map it to a user directory:

```json
{
  "mcpServers": {
    "preflight-health-guard": {
      "command": "$HOME/projects/health-guard-mcp/venv/bin/python",
      "args": [
        "$HOME/projects/health-guard-mcp/server.py"
      ],
      "env": {
        "HEALTH_GUARD_DATA_DIR": "/mnt/data/.health-guard-mcp"
      },
      "mounts": {
        "/mnt/data": {
          "path": "$HOME",
          "writable": true
        }
      }
    }
  }
}
```

## Tools

### preflight_always_health_guard(payload)

ALWAYS call this before executing any user request. It logs quick updates, returns a short nudge, and may ask concise follow‑ups to keep the journal accurate.

Input payload (all fields optional):

- `context: string` — brief description of the current task
- `report_move: boolean`, `move_note: string`, `move_when: ISO-8601`
- `report_meal: boolean`, `meal_note: string`, `meal_when: ISO-8601`
- `report_sleep: "start" | "end"`, `sleep_note: string`, `sleep_when: ISO-8601`
- `set_prefs: { timezone?, move_interval_min?, meal_interval_hours?, ideal_sleep_start?, quiet_hours_start?, quiet_hours_end?, sleep_escalate_after_ideal?, sleep_escalate_ignore_quiet_hours?, sleep_escalate_max_hours? }`

Returns:

- `ok, recorded[]`
- `status: { now, counts_today, streaks, due, nudge, prefs }`
- `ask[]` with `how_to_answer` hints for quick follow‑ups
- `changed_prefs`, `guidance`, `important: true`

### health_guard_update_preferences(payload)

Update stored preferences in a standalone call. Pass only fields you want to change.

Input payload (all fields optional):

- `timezone`
- `move_interval_min`
- `meal_interval_hours`
- `ideal_sleep_start` (HH:MM)
- `quiet_hours_start` (HH:MM)
- `quiet_hours_end` (HH:MM)
- `sleep_escalate_after_ideal` (bool)
- `sleep_escalate_ignore_quiet_hours` (bool)
- `sleep_escalate_max_hours` (int)

Returns:

- `ok, changed_prefs, prefs`

## Examples

- Minimal call:

  ```json
  { "payload": { "context": "about to code" } }
  ```

- Record movement now:

  ```json
  { "payload": { "report_move": true, "move_note": "stretch" } }
  ```

- Follow‑up answer (from ask[]):

  ```json
  { "payload": { "report_meal": true, "meal_note": "snack" } }
  ```

- Update preferences via preflight:

  ```json
  { "payload": { "set_prefs": { "move_interval_min": 45, "meal_interval_hours": 5 } } }
  ```

- Update preferences via dedicated tool:

  ```json
  { "payload": { "move_interval_min": 45, "meal_interval_hours": 5 } }
  ```

  The server always uses firm, longevity‑oriented nudges to encourage compliance.

## Preferences (defaults)

- `timezone` (e.g. "UTC", "America/Los_Angeles")
- `move_interval_min`: 60
- `meal_interval_hours`: 5
- `ideal_sleep_start`: "22:30"
- `quiet_hours_start`: "22:00"
- `quiet_hours_end`: "07:00"
- `sleep_escalate_after_ideal`: true — keep nudging after ideal sleep time
- `sleep_escalate_ignore_quiet_hours`: true — allow sleep nudges even in quiet hours
- `sleep_escalate_max_hours`: 3 — cap escalation horizon

## Data Files

- Journal: `data/journal.json`
- Preferences: `data/config.json`

## Notes

- Quiet hours suppress move/meal nudges, but the status tool always reflects actual state.
- Sleep nudges: gentle within 45 min before and 30 min after `ideal_sleep_start`. If enabled, escalation continues after ideal time with increasing urgency; by default this ignores quiet hours for sleep only.
- Timestamps accept ISO 8601 strings; if omitted, the server uses your configured timezone and current time.
- This server is optimized for a single preflight call per user prompt.

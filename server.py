from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Literal

try:
    from pydantic import BaseModel, Field
except Exception as e:
    raise SystemExit(
        "pydantic is required (installed via fastmcp)."
    ) from e

# Server
try:
    from fastmcp import FastMCP
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "fastmcp is required. Install with: pip install fastmcp"
    ) from e


DATA_DIR = Path(os.getenv("HEALTH_GUARD_DATA_DIR") or "data")
FALLBACK_DIR = Path(os.getenv("HEALTH_GUARD_FALLBACK_DIR") or (Path.home() / ".health-guard-mcp"))
JOURNAL_PATH = DATA_DIR / "journal.json"
CONFIG_PATH = DATA_DIR / "config.json"


def _now(tz_name: Optional[str]) -> datetime:
    try:
        if tz_name:
            from zoneinfo import ZoneInfo  # Python 3.9+

            return datetime.now(ZoneInfo(tz_name))
    except Exception:
        pass
    # Fallback to local timezone
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(dt: str) -> datetime:
    # Accept both with and without timezone; assume local tz if naive
    try:
        parsed = datetime.fromisoformat(dt)
        if parsed.tzinfo is None:
            return parsed.astimezone()
        return parsed
    except Exception:
        return _now(None)


def _primary_or_fallback(path: Path) -> Path:
    if path.exists():
        return path
    # Check fallback
    fb = FALLBACK_DIR / path.name
    if fb.exists():
        return fb
    return path


def _load_json(path: Path, default: Any) -> Any:
    p = _primary_or_fallback(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def _save_json(path: Path, data: Any) -> None:
    # Try preferred location first, without creating parent chains that may be read-only.
    try:
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(path)
        return
    except Exception:
        pass

    # Fallback to user-writable dir
    try:
        FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        fb_path = FALLBACK_DIR / path.name
        tmp = fb_path.with_suffix(fb_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(fb_path)
        return
    except Exception:
        # As a last resort, attempt writing to current directory
        cd_path = Path(".") / path.name
        tmp = cd_path.with_suffix(cd_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(cd_path)


@dataclass
class Preferences:
    timezone: Optional[str] = None  # e.g. "UTC", "America/Los_Angeles"
    move_interval_min: int = 60  # nudge if no movement within minutes
    meal_interval_hours: int = 5  # nudge if no meal within hours
    ideal_sleep_start: str = "17:30"  # HH:MM local time
    quiet_hours_start: str = "17:00"  # HH:MM
    quiet_hours_end: str = "07:00"  # HH:MM
    # Sleep escalation after ideal time
    sleep_escalate_after_ideal: bool = True
    sleep_escalate_ignore_quiet_hours: bool = True
    sleep_escalate_max_hours: int = 3

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def load() -> "Preferences":
        raw = _load_json(CONFIG_PATH, {})
        prefs = Preferences()
        for k, v in raw.items():
            if hasattr(prefs, k):
                setattr(prefs, k, v)
        return prefs

    def save(self) -> None:
        _save_json(CONFIG_PATH, self.to_dict())


def _hhmm_to_time(hhmm: str) -> time:
    try:
        h, m = hhmm.split(":", 1)
        return time(int(h), int(m))
    except Exception:
        return time(22, 0)


def _in_quiet_hours(now: datetime, prefs: Preferences) -> bool:
    start = _hhmm_to_time(prefs.quiet_hours_start)
    end = _hhmm_to_time(prefs.quiet_hours_end)
    # Use naive HH:MM to avoid tz-aware time comparisons
    t = time(now.hour, now.minute)
    # Quiet hours can span midnight
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def _today_bounds(now: datetime) -> Tuple[datetime, datetime]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end


def _load_journal() -> List[Dict[str, Any]]:
    return _load_json(JOURNAL_PATH, [])


def _save_journal(entries: List[Dict[str, Any]]) -> None:
    _save_json(JOURNAL_PATH, entries)


def _add_entry(kind: str, note: str, prefs: Preferences, when: Optional[str] = None) -> Dict[str, Any]:
    now = _now(prefs.timezone)
    ts = _parse(when) if when else now
    entry = {"ts": _iso(ts), "kind": kind, "note": note or ""}
    entries = _load_journal()
    entries.append(entry)
    _save_journal(entries)
    return entry


def _last_of(kind: str, now: datetime) -> Optional[datetime]:
    entries = _load_journal()
    latest: Optional[datetime] = None
    for e in entries[::-1]:  # search from end for speed
        if e.get("kind") == kind:
            ts = _parse(e.get("ts", ""))
            if ts <= now:
                latest = ts
                break
    return latest


def _count_today(kinds: List[str], now: datetime) -> int:
    start, end = _today_bounds(now)
    entries = _load_journal()
    return sum(1 for e in entries if e.get("kind") in kinds and start <= _parse(e.get("ts", "")) < end)


def _days_streak(predicate) -> int:
    entries = _load_journal()
    # Organize by date string
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for e in entries:
        d = _parse(e.get("ts", "")).date().isoformat()
        by_day.setdefault(d, []).append(e)

    today = datetime.now().astimezone().date()
    streak = 0
    d = today
    while True:
        key = d.isoformat()
        if key not in by_day or not predicate(by_day[key]):
            break
        streak += 1
        d = d - timedelta(days=1)
    return streak


def _meals_pred(day_entries: List[Dict[str, Any]]) -> bool:
    return any(e.get("kind") == "meal" for e in day_entries)


def _move_pred(day_entries: List[Dict[str, Any]]) -> bool:
    return any(e.get("kind") == "move" for e in day_entries)


def _sleep_pred(day_entries: List[Dict[str, Any]]) -> bool:
    return any(e.get("kind") in ("sleep_start", "sleep_end") for e in day_entries)


def _next_due(now: datetime, prefs: Preferences) -> Dict[str, Any]:
    # Calculate next times due for nudges
    res: Dict[str, Any] = {}

    last_move = _last_of("move", now)
    move_due_at = (last_move or now) + timedelta(minutes=prefs.move_interval_min)
    res["move_due_at"] = _iso(move_due_at)

    last_meal = _last_of("meal", now)
    meal_due_at = (last_meal or now) + timedelta(hours=prefs.meal_interval_hours)
    res["meal_due_at"] = _iso(meal_due_at)

    # Sleep nudge window around ideal start
    ideal = _hhmm_to_time(prefs.ideal_sleep_start)
    ideal_dt = now.replace(hour=ideal.hour, minute=ideal.minute, second=0, microsecond=0)
    if ideal_dt < now:
        ideal_dt = ideal_dt + timedelta(days=1)
    res["sleep_ideal_at"] = _iso(ideal_dt)
    return res


def _nudge(now: datetime, prefs: Preferences) -> Dict[str, Any]:
    quiet = _in_quiet_hours(now, prefs)
    last_move = _last_of("move", now)
    last_meal = _last_of("meal", now)

    move_overdue_min = None
    meal_overdue_min = None

    if last_move is not None:
        delta = now - last_move
        move_overdue_min = int(delta.total_seconds() // 60) - prefs.move_interval_min
    if last_meal is not None:
        delta = now - last_meal
        meal_overdue_min = int(delta.total_seconds() // 60) - prefs.meal_interval_hours * 60

    # Sleep readiness / escalation nudges around and after ideal start
    ideal = _hhmm_to_time(prefs.ideal_sleep_start)
    ideal_dt = now.replace(hour=ideal.hour, minute=ideal.minute, second=0, microsecond=0)
    # Window: 45 min before to 30 min after (gentle)
    sleep_nudge = None
    window_start = ideal_dt - timedelta(minutes=45)
    window_end = ideal_dt + timedelta(minutes=30)
    if window_start <= now <= window_end:
        sleep_nudge = {
            "kind": "sleep",
            "message": "Time to wind down: dim lights, avoid screens, prep for bed.",
            "why": "near_ideal_sleep_time",
            "severity": "gentle",
        }
    elif now > ideal_dt and prefs.sleep_escalate_after_ideal:
        # Escalate after ideal time. Respect quiet hours only if configured.
        if not quiet or prefs.sleep_escalate_ignore_quiet_hours:
            minutes_over = int((now - ideal_dt).total_seconds() // 60)
            hours_over = min(prefs.sleep_escalate_max_hours, max(0, minutes_over // 60))
            if minutes_over <= 30:
                msg = "It’s past your target bedtime — start winding down now."
                severity = "gentle"
            elif minutes_over <= 90:
                msg = "Past bedtime. Wrap up and head to bed; devices down."
                severity = "firm"
            else:
                msg = "Well past bedtime. Stop now, lights off, prioritize sleep."
                severity = "strong"
            sleep_nudge = {
                "kind": "sleep",
                "message": msg,
                "why": "past_ideal_sleep_time",
                "minutes_over": minutes_over,
                "severity": severity,
            }

    # Decide prioritized nudge
    candidates: List[Tuple[int, Dict[str, Any]]] = []
    if not quiet:
        if move_overdue_min is not None:
            if move_overdue_min >= 0:
                candidates.append(
                    (
                        move_overdue_min,
                        {
                            "kind": "move",
                            "message": "Stand up and move for 2–5 minutes (stretch, walk).",
                            "why": "no_recent_movement",
                        },
                    )
                )
        else:
            # No movement logged yet today; encourage gentle start
            candidates.append(
                (
                    0,
                    {
                        "kind": "move",
                        "message": "Start with a quick stretch or short walk.",
                        "why": "no_movement_logged",
                    },
                )
            )

        if meal_overdue_min is not None:
            if meal_overdue_min >= 0:
                candidates.append(
                    (
                        meal_overdue_min,
                        {
                            "kind": "meal",
                            "message": "Refuel: have a balanced meal or snack.",
                            "why": "long_since_last_meal",
                        },
                    )
                )
        else:
            # No meal logged yet today
            candidates.append(
                (
                    0,
                    {
                        "kind": "meal",
                        "message": "Don’t skip meals — plan your next bite.",
                        "why": "no_meal_logged",
                    },
                )
            )

    if sleep_nudge and (not quiet or prefs.sleep_escalate_ignore_quiet_hours):
        # Prioritize sleep strongly; after-ideal gains additional weight by minutes overdue
        weight = 10_000
        if sleep_nudge.get("why") == "past_ideal_sleep_time":
            weight += int(sleep_nudge.get("minutes_over", 0))
        candidates.append((weight, sleep_nudge))

    if not candidates:
        return {
            "kind": "none",
            "message": "All good for now — keep it up!",
            "why": "no_nudge_needed_or_quiet_hours",
        }

    # Pick the most overdue (highest score)
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


app = FastMCP("health-guard-mcp")


class PreferencesUpdate(BaseModel):
    timezone: Optional[str] = None
    move_interval_min: Optional[int] = None
    meal_interval_hours: Optional[int] = None
    ideal_sleep_start: Optional[str] = None
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    sleep_escalate_after_ideal: Optional[bool] = None
    sleep_escalate_ignore_quiet_hours: Optional[bool] = None
    sleep_escalate_max_hours: Optional[int] = None


class HealthInput(BaseModel):
    context: Optional[str] = Field(
        default=None,
        description="Briefly describe the user's current task/context.",
    )
    # Immediate reports to record in the journal
    report_move: Optional[bool] = Field(default=None, description="If true, record a movement break now.")
    move_note: Optional[str] = None
    move_when: Optional[str] = None

    report_meal: Optional[bool] = Field(default=None, description="If true, record a meal/snack now.")
    meal_note: Optional[str] = None
    meal_when: Optional[str] = None

    report_sleep: Optional[Literal["start", "end"]] = Field(
        default=None, description='Record a sleep event ("start" or "end").'
    )
    sleep_note: Optional[str] = None
    sleep_when: Optional[str] = None

    set_prefs: Optional[PreferencesUpdate] = Field(
        default=None,
        description="Optionally update user preferences in this call.",
    )


def _status_bundle(now: datetime, prefs: Preferences) -> Dict[str, Any]:
    move_count = _count_today(["move"], now)
    meal_count = _count_today(["meal"], now)
    sleep_events = _count_today(["sleep_start", "sleep_end"], now)
    streaks = {
        "move_days": _days_streak(_move_pred),
        "meal_days": _days_streak(_meals_pred),
        "sleep_days": _days_streak(_sleep_pred),
    }
    due = _next_due(now, prefs)
    nudge = _nudge(now, prefs)
    return {
        "now": _iso(now),
        "counts_today": {"move": move_count, "meal": meal_count, "sleep_events": sleep_events},
        "streaks": streaks,
        "due": due,
        "nudge": nudge,
        "prefs": prefs.to_dict(),
    }


@app.tool(
    name="preflight_always_health_guard",
    description=(
        "ALWAYS call this tool BEFORE executing any user request. It maintains healthy habits by (1)"
        " logging quick updates about moving/eating/sleep, (2) returning a short, actionable"
        " nudge if needed, and (3) asking concise tracking questions to keep the journal accurate."
        " Pass task context so guidance can be brief and relevant."
    ),
    tags=["preflight", "always", "health", "nudges", "journal", "critical"],
)
def health_preflight(payload: HealthInput) -> Dict[str, Any]:
    """Preflight health check. Call on every prompt. Records reports, updates prefs, nudges gently, and may ask short follow-ups.

    - To record: set report_move/report_meal/report_sleep and optional notes/timestamps.
    - To update preferences: include set_prefs.
    - Returns: status, nudge, and optional ask items (questions) with how_to_answer hints.
    """
    prefs = Preferences.load()
    # Preferences update first
    changed: Dict[str, Any] = {}
    if payload.set_prefs is not None:
        upd = payload.set_prefs
        for k, v in upd.model_dump(exclude_none=True).items():
            setattr(prefs, k, v)
            changed[k] = v
        if changed:
            prefs.save()

    # Record any immediate reports
    recorded: List[Dict[str, Any]] = []
    if payload.report_move:
        recorded.append(_add_entry("move", payload.move_note or "", prefs, payload.move_when))
    if payload.report_meal:
        recorded.append(_add_entry("meal", payload.meal_note or "", prefs, payload.meal_when))
    if payload.report_sleep:
        kind = "sleep_start" if payload.report_sleep == "start" else "sleep_end"
        recorded.append(_add_entry(kind, payload.sleep_note or "", prefs, payload.sleep_when))

    now = _now(prefs.timezone)
    quiet = _in_quiet_hours(now, prefs)
    status = _status_bundle(now, prefs)

    # Build questions to ask; for sleep escalation we may ignore quiet hours
    asks: List[Dict[str, Any]] = []
    if not quiet:
        last_move = _last_of("move", now)
        last_meal = _last_of("meal", now)
        if last_move is None or now - last_move >= timedelta(minutes=prefs.move_interval_min):
            asks.append(
                {
                    "field": "move",
                    "question": "Did you move in the past hour? If yes, what did you do?",
                    "how_to_answer": {
                        "report_move": True,
                        "move_note": "<stretch/walk/etc>",
                        "move_when": "<ISO or omit for now>",
                    },
                }
            )
        if last_meal is None or now - last_meal >= timedelta(hours=prefs.meal_interval_hours):
            asks.append(
                {
                    "field": "meal",
                    "question": "Have you eaten recently? If yes, briefly note it.",
                    "how_to_answer": {
                        "report_meal": True,
                        "meal_note": "<what>",
                        "meal_when": "<ISO or omit>",
                    },
                }
            )

    # Sleep follow-up near or after ideal time
    ideal = _hhmm_to_time(prefs.ideal_sleep_start)
    ideal_dt = now.replace(hour=ideal.hour, minute=ideal.minute, second=0, microsecond=0)
    last_sleep_start = _last_of("sleep_start", now)
    missing_today_sleep_start = last_sleep_start is None or last_sleep_start < ideal_dt
    can_nudge_sleep = (not quiet) or prefs.sleep_escalate_ignore_quiet_hours
    if can_nudge_sleep and missing_today_sleep_start:
        if ideal_dt - timedelta(minutes=45) <= now <= ideal_dt + timedelta(minutes=30):
            asks.append(
                {
                    "field": "sleep",
                    "question": "Winding down for sleep now?",
                    "how_to_answer": {
                        "report_sleep": "start",
                        "sleep_note": "<optional>",
                    },
                }
            )
        elif now > ideal_dt and prefs.sleep_escalate_after_ideal:
            asks.append(
                {
                    "field": "sleep",
                    "question": "It’s past your target bedtime — ready to log sleep start?",
                    "how_to_answer": {
                        "report_sleep": "start",
                        "sleep_note": "<optional>",
                    },
                }
            )

    response: Dict[str, Any] = {
        "ok": True,
        "changed_prefs": changed or None,
        "recorded": recorded,
        "status": status,
        "ask": asks,
        "important": True,
        "guidance": "Keep this short: stand, stretch, hydrate, and don’t skip meals.",
    }
    return response


if __name__ == "__main__":
    # Run MCP server (stdio)
    app.run()

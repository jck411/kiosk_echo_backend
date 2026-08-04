# Time Management Architecture

This document explains how time, date, and timezone management works across the Backend_FastAPI codebase.

## Overview

The time management system is built on two core modules that provide consistent datetime handling, timezone resolution, and time context for conversations:

1. **`backend.utils.datetime_utils`** - Consolidated datetime parsing, formatting, and conversion utilities
2. **`backend.services.time_context`** - Time snapshots, timezone resolution, and prompt context generation

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    Chat Orchestrator                            │
│  - Injects time context into every system prompt               │
│  - Uses: build_prompt_context_block()                          │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              backend.services.time_context                      │
│  - EASTERN_TIMEZONE_NAME = "America/New_York" (source of truth)│
│  - TimeSnapshot: current moment in UTC + local timezone        │
│  - Timezone resolution (defaults to America/New_York)          │
│  - Prompt context formatting                                   │
│  - Week calculations & upcoming anchors                        │
└──────────────┬────────────────────────┬─────────────────────────┘
               │                        │
               │                        └──────────────────┐
               ▼                                           ▼
┌──────────────────────────────────┐    ┌──────────────────────────┐
│ backend.utils.datetime_utils     │    │ UiSettings.display_      │
│  - RFC3339 parsing               │    │   timezone (default from │
│  - ISO time string parsing       │    │   time_context)          │
│  - Database timestamp formatting │    │                          │
│  - Keyword parsing (today, etc.) │    │ GET /api/settings/ui     │
└────────────┬─────────────────────┘    │   kiosk/ui               │
             │                           └──────────┬───────────────┘
             │                                      │
             │              ┌───────────────────────┘
             │              │
             │              ▼
             │    ┌─────────────────────────────────┐
             │    │ Kiosk Frontend                  │
             │    │  - ConfigContext.jsx            │
             │    │  - useDisplayTimezone() hook    │
             │    │  - Clock/Calendar/Alarm comps   │
             │    └─────────────────────────────────┘
             │
 ┌───────────┼───────────────┬──────────────────┐
 ▼           ▼               ▼                  ▼
┌──────────┐┌─────────────┐┌──────────┐┌──────────────┐
│ Calendar ││   Tasks     ││Repository││ Housekeeping │
│  Server  ││   Service   ││(DB times)││    Server    │
└──────────┘└─────────────┘└──────────┘└──────────────┘
```

## Core Modules

### 1. `backend.utils.datetime_utils`

**Purpose**: Centralized datetime parsing, conversion, and formatting.

**Consolidates logic from**:
- `tasks/utils.py` (RFC3339 parsing, normalization)
- `calendar_server.py` (time string parsing)
- `repository.py` (database timestamp handling)

**Key Functions**:

```python
# Parse RFC3339/ISO datetime strings to aware datetime objects
parse_rfc3339_datetime(value: str | None) -> datetime | None

# Convert datetime to RFC3339 with 'Z' suffix
normalize_rfc3339(dt_value: datetime) -> str

# Parse keywords like "today", "tomorrow", "next_week" to RFC3339
parse_time_string(time_str: str | None) -> str | None

# Normalize ISO strings (YYYY-MM-DD, naive datetimes) to RFC3339
parse_iso_time_string(time_str: str | None) -> str | None

# Convert SQLite timestamps to ISO8601
normalize_db_timestamp(value: str | None) -> str | None

# Parse SQLite timestamps to aware datetime
parse_db_timestamp(value: str | None) -> datetime | None

# Format timestamps for client (returns EDT and UTC)
format_timestamp_for_client(value: str | None) -> tuple[str | None, str | None]
```

**Time Keyword Support**:
- `today`, `tomorrow`, `yesterday`
- `next_week`, `next_month`, `next_year`
- Date strings: `YYYY-MM-DD`
- ISO datetime strings

All keywords resolve to **UTC midnight** (T00:00:00Z) for consistency.

---

### 2. `backend.services.time_context`

**Purpose**: Time snapshots for consistent timezone handling and prompt context generation.

**Key Components**:

#### `TimeSnapshot` (dataclass)
Captures a moment in time with both UTC and local timezone representations.

```python
@dataclass
class TimeSnapshot:
    tzinfo: dt.tzinfo           # Target timezone
    now_utc: dt.datetime        # Current moment in UTC
    now_local: dt.datetime      # Current moment in target timezone

    # Properties
    eastern: dt.datetime        # Time in US Eastern
    date: dt.date              # Local date
    iso_local: str             # ISO format in local timezone
    iso_utc: str               # ISO format in UTC
    unix_seconds: int          # Unix timestamp (seconds)
    unix_precise: str          # Unix timestamp (microseconds)

    # Methods
    format_time() -> str                # "HH:MM:SS TZ"
    timezone_display() -> str           # Human-friendly timezone name
```

#### `create_time_snapshot(timezone_name: str | None = None) -> TimeSnapshot`
Creates a snapshot of the current moment. Defaults to **America/New_York** (Eastern Time) to match the user's Orlando location.

#### `build_prompt_context_block(snapshot: TimeSnapshot | None = None) -> str`
Generates formatted time context for system prompts:

```markdown
# Current Date & Time Context
- Today's date: 2025-11-17 (Sunday)
- Current time: 14:30:00 EST
- Timezone: America/New_York
- ISO timestamp (UTC): 2025-11-17T19:30:00+00:00

Use this context when interpreting relative dates like 'last month', 'next week', etc.
```

#### Timezone Resolution
```python
resolve_timezone(timezone_name: str | None, fallback: tzinfo | None) -> tzinfo
```

**Priority order**:
1. Specified `timezone_name` (if valid)
2. Provided `fallback`
3. **America/New_York** (Eastern Time) - default for Orlando user
4. System local timezone (if Eastern unavailable)
5. UTC (ultimate fallback)

---

## Integration Points

### Chat Orchestrator (`backend.chat.orchestrator`)

**Automatic Time Context Injection**:

Every conversation automatically receives time context prepended to the system prompt:

```python
def _build_enhanced_system_prompt(base_prompt: str | None) -> str:
    """Prepend the current time context to the configured system prompt."""
    context_block = build_prompt_context_block(create_time_snapshot())
    base = (base_prompt or "").strip()
    if base:
        return f"{context_block}\n\n{base}"
    return context_block
```

This happens:
- At the start of every new conversation
- When no system message exists in the session
- Before any user messages are processed

**Result**: The LLM always knows the current date, time, and timezone, enabling correct interpretation of relative time references like "schedule this for next Tuesday."

---

### Housekeeping Server (External MCP Server)

**`current_time` MCP Tool**:

Provides the LLM with precise current time information when needed during conversations. This MCP server runs externally on Proxmox:

```python
@mcp.tool("current_time")
async def current_time(format: Literal["iso", "unix"] = "iso") -> dict[str, Any]:
    """Return the current time with UTC and Eastern Time representations."""
    snapshot = create_time_snapshot(EASTERN_TIMEZONE_NAME, fallback=timezone.utc)
    eastern = snapshot.eastern

    return {
        "format": format,
        "value": rendered,
        "utc_iso": snapshot.iso_utc,
        "utc_unix": str(snapshot.unix_seconds),
        "utc_unix_precise": snapshot.unix_precise,
        "eastern_iso": eastern.isoformat(),
        "eastern_abbreviation": eastern.tzname(),  # "EST" or "EDT"
        "eastern_display": eastern.strftime("%a %b %d %Y %I:%M:%S %p %Z"),
        "eastern_offset": offset,
        "timezone": EASTERN_TIMEZONE_NAME,
        "context_summary": context_summary,
    }
```

**Use cases**:
- User asks "what time is it?"
- LLM needs precise current time for calculations
- Time-sensitive operations requiring fresh timestamps

---

### Calendar Server (External MCP Server)

**Time Parsing for Events & Tasks**:

The calendar MCP server runs externally on Proxmox. It uses `datetime_utils` patterns for all date/time parsing:

```python
from backend.utils.datetime_utils import (
    normalize_rfc3339,
    parse_iso_time_string,
    parse_rfc3339_datetime,
)

# Parse user input
start_time = parse_iso_time_string(user_start_time)
end_time = parse_iso_time_string(user_end_time)

# Format for API
event["start"] = {"dateTime": start_time}
event["end"] = {"dateTime": end_time}
```

**Handles**:
- All-day events (date-only strings)
- Timed events (datetime strings)
- Timezone conversions
- Default time windows (e.g., "next 30 days")

---

### Task Service (`backend.tasks.service`)

**Due Date Management**:

```python
from backend.utils.datetime_utils import (
    normalize_rfc3339,
    parse_rfc3339_datetime,
    parse_time_string,
)

# Parse user input for task due dates
due_time = parse_time_string("tomorrow")  # → "2025-11-18T00:00:00Z"

# Store task with normalized timestamp
task.due = parse_rfc3339_datetime(due_str)

# Format for display
display_str = normalize_rfc3339(task.due)
```

**Supports**:
- Keyword parsing (today, tomorrow, next_week)
- Date filtering (due_min, due_max)
- Overdue detection
- Scheduled vs. unscheduled task filtering

---

### Repository (`backend.repository`)

**Database Timestamp Handling**:

SQLite stores timestamps as strings. The repository uses `datetime_utils` for consistent conversion:

```python
from backend.utils.datetime_utils import (
    format_timestamp_for_client,
    normalize_db_timestamp,
    parse_db_timestamp,
)

# Store timestamp
cursor.execute("INSERT INTO messages (created_at, ...) VALUES (CURRENT_TIMESTAMP, ...)")

# Retrieve and normalize
db_timestamp = row["created_at"]
normalized = normalize_db_timestamp(db_timestamp)  # → ISO8601 UTC

# Format for client (returns both EDT and UTC)
edt_iso, utc_iso = format_timestamp_for_client(db_timestamp)
```

**Ensures**:
- All timestamps stored/retrieved in UTC
- Client receives both Eastern and UTC timestamps
- Consistent timezone handling across sessions

---

### Kiosk Frontend (`frontend-kiosk/`)

**Display Timezone Configuration**:

The kiosk frontend fetches its display timezone from the backend's `UiSettings` API, ensuring a single source of truth for timezone configuration.

```javascript
// ConfigContext fetches settings on app init
GET /api/settings/ui
// Returns: { idle_return_delay_ms: 10000, display_timezone: "America/New_York" }

// Components use the timezone via React hook
const displayTimezone = useDisplayTimezone();

// All time formatting uses the configured timezone
const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: displayTimezone,  // From backend
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
});
```

**Architecture Flow**:

```
time_context.py (EASTERN_TIMEZONE_NAME)
       ↓
client_settings.py (UiSettings.display_timezone default)
       ↓
GET /api/settings/ui
       ↓
ConfigContext.jsx (fetches and caches)
       ↓
useDisplayTimezone() hook
       ↓
Clock, Calendar, Alarm components
```

**Updated Components**:
- **Clock.jsx**: Main clock display, alarm times, weather timestamps
- **CalendarScreen.jsx**: Event times, date grouping, "Today/Tomorrow" logic
- **AlarmOverlay.jsx**: Firing alarm time display

**Benefits**:
- Kiosk displays correct local time regardless of device system timezone
- Single configuration point (backend) for all clients
- No hardcoded timezones in frontend code
- Easy to override for the kiosk via API

**Kiosk Timezone Override**:

```bash
# Change kiosk timezone to Pacific Time
PUT /api/settings/ui
{
  "display_timezone": "America/Los_Angeles"
}

# Kiosk will fetch new setting on next page load/refresh
```

---

### Logging Handlers (`backend.logging_handlers`)

**Date-Stamped Log Files**:

```python
from backend.services.time_context import EASTERN_TIMEZONE

class DateStampedFileHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        timestamp = datetime.now(timezone.utc).astimezone(EASTERN_TIMEZONE)
        date_stamp = timestamp.strftime("%Y-%m-%d")
        # Creates logs/app/2025-11-17/app.log
```

**Features**:
- Daily log rotation based on Eastern Time
- Automatic cleanup of old logs
- Consistent date stamping

---

## Design Principles

### 1. **UTC Internally, Local for Display**
- All internal timestamps, database storage, and calculations use UTC
- Timezone conversion happens only at display boundaries
- Prevents ambiguity during DST transitions

### 2. **Eastern Time as Default**
- User is located in Orlando, Florida (Eastern Time Zone)
- System defaults to `America/New_York` throughout
- Consistent with user's local expectations

### 3. **Single Source of Truth**
- `datetime_utils` is the only module that performs datetime parsing
- All other modules import from it
- Eliminates duplicate logic and inconsistencies

### 4. **Graceful Fallbacks**
- Timezone resolution falls back gracefully: specified → Eastern → system local → UTC
- Parsing functions return `None` on failure rather than raising exceptions
- Optional dependencies (dateutil) handled transparently

### 5. **Automatic Context Injection**
- Time context automatically prepended to every conversation
- LLM always has current date/time information
- No manual time management needed by users

---

## Common Patterns

### Parsing User Time Input

```python
from backend.utils.datetime_utils import parse_time_string

# User says: "schedule for tomorrow"
due_date = parse_time_string("tomorrow")  # → "2025-11-18T00:00:00Z"

# User says: "due next week"
due_date = parse_time_string("next_week")  # → "2025-11-24T00:00:00Z"

# User provides date: "2025-12-25"
due_date = parse_time_string("2025-12-25")  # → "2025-12-25T00:00:00Z"
```

### Creating Time Context for Prompts

```python
from backend.services.time_context import (
    create_time_snapshot,
    build_prompt_context_block,
)

# Get current time snapshot
snapshot = create_time_snapshot()  # Defaults to Eastern Time

# Build formatted context block
context = build_prompt_context_block(snapshot)

# Prepend to system prompt
full_prompt = f"{context}\n\n{user_system_prompt}"
```

### Working with TimeSnapshot

```python
from backend.services.time_context import create_time_snapshot

snapshot = create_time_snapshot()

# Access different representations
print(snapshot.date)              # 2025-11-17
print(snapshot.iso_utc)           # 2025-11-17T19:30:00+00:00
print(snapshot.iso_local)         # 2025-11-17T14:30:00-05:00
print(snapshot.unix_seconds)      # 1731873000
print(snapshot.format_time())     # "14:30:00 EST"
print(snapshot.timezone_display()) # "America/New_York"
```

### Database Timestamp Handling

```python
from backend.utils.datetime_utils import (
    format_timestamp_for_client,
    normalize_db_timestamp,
)

# Store (SQLite CURRENT_TIMESTAMP is already UTC)
cursor.execute("INSERT INTO table (created_at) VALUES (CURRENT_TIMESTAMP)")

# Retrieve and normalize
row = cursor.fetchone()
utc_iso = normalize_db_timestamp(row["created_at"])

# Format for client
edt_iso, utc_iso = format_timestamp_for_client(row["created_at"])
client_data = {
    "created_at": edt_iso,        # Eastern Time for display
    "created_at_utc": utc_iso,    # UTC for reference
}
```

---

## File Locations

### Core Modules
- **DateTime Utilities**: `src/backend/utils/datetime_utils.py`
- **Time Context**: `src/backend/services/time_context.py`
- **Kiosk Settings Schema**: `src/backend/schemas/client_settings.py` (UiSettings.display_timezone)

### Integration Points
- **Chat Orchestrator**: `src/backend/chat/orchestrator.py`
- **MCP Servers**: External servers running on Proxmox (housekeeping, calendar, etc.)
- **Repository**: `src/backend/repository.py`
- **Logging Handlers**: `src/backend/logging_handlers.py`
- **Conversation Logging**: `src/backend/services/conversation_logging.py`
- **Kiosk Settings Router**: `src/backend/routers/settings.py` (GET/PUT /api/settings/ui)

### Frontend (Kiosk)
- **Config Context**: `frontend-kiosk/src/context/ConfigContext.jsx`
- **Clock Component**: `frontend-kiosk/src/components/Clock.jsx`
- **Calendar Component**: `frontend-kiosk/src/components/CalendarScreen.jsx`
- **Alarm Overlay**: `frontend-kiosk/src/components/AlarmOverlay.jsx`
- **App Root**: `frontend-kiosk/src/App.jsx`

### Tests
- **DateTime Utils Tests**: `tests/test_datetime_utils.py`
- **Orchestrator Tests**: `tests/test_chat_orchestrator.py`

---

## Best Practices

### ✅ DO

- **Use `datetime_utils` functions** for all datetime parsing and formatting
- **Store timestamps in UTC** in databases
- **Use `TimeSnapshot`** when you need current time in multiple formats
- **Call `create_time_snapshot()`** without arguments to get Eastern Time
- **Return both Eastern and UTC** timestamps to clients
- **Use keyword parsing** (today, tomorrow) for user-friendly input

### ❌ DON'T

- Don't use `datetime.now()` without timezone argument (naive datetimes)
- Don't parse datetime strings with custom logic (use `datetime_utils`)
- Don't hardcode timezone names except in configuration
- Don't store timestamps in local timezone
- Don't assume system local time matches user's timezone
- Don't format timestamps inconsistently across the codebase

---

## Troubleshooting

### Issue: "Time is off by several hours"
**Cause**: Naive datetime being treated as local instead of UTC
**Solution**: Always use timezone-aware datetimes. Use `parse_rfc3339_datetime()` which ensures UTC.

### Issue: "Task scheduled for wrong day"
**Cause**: Keyword like "tomorrow" interpreted in wrong timezone
**Solution**: Keywords resolve to UTC midnight. This is intentional for consistency. Date-only task scheduling uses midnight UTC.

### Issue: "DST transition causing errors"
**Cause**: Local timezone conversion during ambiguous hour
**Solution**: All internal operations use UTC. Convert to local only for display.

### Issue: "Client showing wrong timezone"
**Cause**: Client not receiving both EDT and UTC timestamps
**Solution**: Use `format_timestamp_for_client()` which returns both.

---

## Future Enhancements

### Potential Improvements

1. **User Timezone Preferences**
   - Allow users to set their preferred timezone
   - Store in user profile/settings
   - Pass to `create_time_snapshot(user_timezone)`

2. **Multi-User Support**
   - Each user could have different default timezone
   - Calendar events could honor creator's timezone

3. **More Time Keywords**
   - "this weekend", "next friday", "end of month"
   - Relative keywords: "in 3 days", "2 weeks from now"

4. **Business Hours Awareness**
   - Task scheduling could respect business hours
   - "tomorrow morning" → 9 AM in user's timezone

5. **Recurring Events**
   - Support for RRULE parsing
   - Timezone-aware recurrence calculation

---

## How LLMs Interpret Time Parameters

Understanding how the LLM decides which parameters to include when calling time-related tools is crucial for designing effective tool schemas.

### Default Values vs Null

When the LLM sees a parameter in a tool schema, the `default` value strongly influences its behavior:

#### Example: `calendar_get_events` Parameters

```python
@mcp.tool("calendar_get_events")
async def calendar_get_events(
    user_email: str = DEFAULT_USER_EMAIL,  # "jck411@gmail.com"
    calendar_id: Optional[str] = None,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_events: int = 200,
    include_tasks: bool = False,
) -> str:
```

**Schema as the LLM sees it:**

```json
{
  "user_email": {
    "type": "string",
    "default": "jck411@gmail.com",
    "description": "Email address of the user whose calendar to search"
  },
  "time_min": {
    "anyOf": [{"type": "string"}, {"type": "null"}],
    "default": null,
    "description": "Start of time window (RFC3339/ISO format)"
  },
  "time_max": {
    "anyOf": [{"type": "string"}, {"type": "null"}],
    "default": null,
    "description": "End of time window (RFC3339/ISO format)"
  },
  "max_events": {
    "type": "integer",
    "default": 200,
    "description": "Maximum number of events to return"
  }
}
```

### LLM Decision Process

When user asks: **"What's on my calendar today?"**

**The LLM reasons:**

1. **`user_email: "jck411@gmail.com"`**
   - ✅ "This has a sensible default"
   - ✅ "The description says it identifies which user"
   - ✅ "User didn't mention a different person"
   - **Decision:** Include it (or omit, both work)

2. **`time_min: null`**
   - ⚠️ "Defaults to null (optional)"
   - ⚠️ "But user said 'today', so I need bounds"
   - ⚠️ "I should override null with start of today"
   - **Decision:** `"2025-11-17T00:00:00-05:00"`

3. **`time_max: null`**
   - ⚠️ "Defaults to null (optional)"
   - ⚠️ "But user said 'today', so I need bounds"
   - ⚠️ "I should override null with end of today"
   - **Decision:** `"2025-11-17T23:59:59-05:00"`

4. **`max_events: 200`**
   - ✅ "Has a reasonable default"
   - ✅ "User didn't mention needing more/fewer"
   - **Decision:** Omit (let default handle it)

5. **`calendar_id: null`**
   - ✅ "Null means all calendars"
   - ✅ "User didn't specify which calendar"
   - **Decision:** Omit (null is correct)

### Final Tool Call

```json
{
  "name": "calendar_get_events",
  "arguments": {
    "user_email": "jck411@gmail.com",
    "time_min": "2025-11-17T00:00:00-05:00",
    "time_max": "2025-11-17T23:59:59-05:00"
  }
}
```

### Decision Matrix

| Parameter State | LLM Behavior | Example |
|----------------|--------------|---------|
| `default: "value"` + has description | Uses default unless user implies otherwise | `user_email: "jck411@gmail.com"` |
| `default: null` + user mentions context | Provides specific value | `time_min: "2025-11-17T00:00:00"` when user says "today" |
| `default: null` + not relevant | Omits parameter | `calendar_id: null` (search all) |
| `default: 200` + not mentioned | Omits parameter | `max_events: 200` (reasonable) |
| No default (required) | MUST provide value | Would error if omitted |
| Required + No description | LLM guesses (often wrong) | Avoid this pattern! |

### Why Descriptions Matter

**Without description:**
```json
{"user_email": {"type": "string", "default": "jck411@gmail.com"}}
```
❌ LLM confusion: *"What is this email for? Why is it there? Should I change it?"*

**With description:**
```json
{
  "user_email": {
    "type": "string",
    "default": "jck411@gmail.com",
    "description": "Email address of the user whose calendar to search"
  }
}
```
✅ LLM understanding: *"Ah, it's for identifying which user. Default is current user."*

### Key Insights

- **`default: null`** tells the LLM: *"This is optional. Only provide it if the user's request requires it."*
- **`default: "actual_value"`** tells the LLM: *"This has a sensible default. You rarely need to change it."*
- **No default** tells the LLM: *"This is REQUIRED. You must always provide a value."*
- **Descriptions provide semantic context** that enables the LLM to make intelligent decisions about when to override defaults

The LLM uses **semantic understanding** of the user's request combined with **parameter descriptions** to decide when to override defaults. This is why clear, descriptive parameter documentation is essential for reliable tool calling.

---

## Summary

The time management system provides:

✅ **Consistent** datetime handling across all modules
✅ **Automatic** time context injection into conversations
✅ **UTC-first** approach with Eastern Time defaults
✅ **Centralized** parsing and formatting logic
✅ **User-friendly** keyword support (today, tomorrow, etc.)
✅ **Robust** timezone resolution with sensible fallbacks
✅ **Client-ready** formatting with both Eastern and UTC timestamps
✅ **Intelligent LLM parameter interpretation** through well-designed schemas

This architecture ensures that time-related operations are reliable, predictable, and maintainable across the entire application, while enabling the LLM to make smart decisions about time-based tool calls.

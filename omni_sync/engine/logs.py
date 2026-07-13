"""Console logging: colourised, severity-tagged, thread-safe.

Colour is emitted only on an interactive terminal (and never when NO_COLOR is
set), so `docker compose logs`, file redirects and pipes stay clean. All output
goes through one lock so the concurrent Apple / YouTube mirror threads never
interleave mid-line.
"""

import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime

# Track titles are arbitrary Unicode; Windows consoles default to cp1252 and
# would crash on the first Cyrillic/CJK title without this.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None and os.getenv("TERM") != "dumb"
_ANSI = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "cyan": "\033[36m", "grey": "\033[90m",
}
_lock = threading.Lock()


@dataclass(frozen=True)
class Event:
    """One structured progress event for the live view. `kind` is the semantic
    category (add/remove/hold/miss/note/warn/summary/section); the web layer
    styles by kind instead of parsing the message. Lives here — a leaf module —
    so the engine never imports the web/services tier to emit one."""

    ts: float
    kind: str
    tag: str
    message: str
    data: dict | None = None


_sink = None


def set_sink(fn):
    """Register a callback fired for every log event (None disables it)."""
    global _sink
    _sink = fn


def _emit(kind, message, tag, data=None):
    fn = _sink
    if fn is None:
        return
    try:
        fn(Event(time.time(), kind, tag or "", str(message), data))
    except Exception:
        pass  # a broken sink must never break a sync


def paint(text, *styles):
    if not _COLOR or not styles:
        return str(text)
    return "".join(_ANSI[s] for s in styles) + str(text) + _ANSI["reset"]


def log(message="", *, tag=None, tag_styles=("grey",)):
    """One timestamped line. `tag` is a short service label (apple/yt/local)
    kept to the right of the clock so interleaved threads stay readable."""
    prefix = paint(f"[{datetime.now():%H:%M:%S}]", "grey") + " "
    if tag:
        prefix += paint(f"{tag:<6}", *tag_styles) + " "
    with _lock:
        print(f"{prefix}{message}", flush=True)


def log_section(title, detail="", *, tag=None):
    log("")  # tagless blank separator
    line = paint(f"■ {title}", "bold", "cyan")
    if detail:
        line += "  " + paint(detail, "grey")
    log(line, tag=tag)
    _emit("section", title, tag, {"detail": detail} if detail else None)


def log_event(symbol, message, *styles, tag=None, indent="   "):
    log(f"{indent}{paint(symbol, *styles)} {message}", tag=tag)


def log_add(msg, *, dry=False, tag=None, indent="   "):
    log_event("+", msg + (paint("  (dry run)", "grey") if dry else ""), "green", tag=tag, indent=indent)
    _emit("add", msg, tag, {"dry": dry})


def log_remove(msg, *, dry=False, tag=None, indent="   "):
    log_event("-", msg + (paint("  (dry run)", "grey") if dry else ""), "red", tag=tag, indent=indent)
    _emit("remove", msg, tag, {"dry": dry})


def log_hold(msg, *, tag=None, indent="   "):
    log_event("~", paint(msg, "yellow"), "yellow", tag=tag, indent=indent)
    _emit("hold", msg, tag)


def log_miss(msg, *, tag=None, indent="   "):
    log_event("x", paint(msg, "grey"), "grey", tag=tag, indent=indent)
    _emit("miss", msg, tag)


def log_warn(msg, *, tag=None, indent="   "):
    log_event("!", paint(msg, "yellow", "bold"), "yellow", "bold", tag=tag, indent=indent)
    _emit("warn", msg, tag)


def log_note(msg, *, tag=None, indent="   "):
    log_event(".", paint(msg, "grey"), "grey", tag=tag, indent=indent)
    _emit("note", msg, tag)


def log_download(msg, *, tag=None, indent="   "):
    log_event("v", paint(msg, "blue"), "blue", tag=tag, indent=indent)
    _emit("download", msg, tag)


def log_summary(msg, *, tag=None, indent=" "):
    log_event("=", paint(msg, "bold"), "bold", indent=indent, tag=tag)
    _emit("summary", msg, tag)


def fmt_counts(added, removed, missing=0, held=0, deferred=0):
    parts = [paint(f"+{added}", "green", "bold"), paint(f"-{removed}", "red", "bold")]
    extra = []
    if missing:
        extra.append(f"{missing} missing")
    if held:
        extra.append(f"{held} held")
    if deferred:
        extra.append(f"{deferred} deferred")
    tail = f"  ({', '.join(extra)})" if extra else ""
    return " ".join(parts) + paint(tail, "grey")


def fmt_secs(seconds):
    seconds = int(seconds)
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m{seconds % 60:02d}s"

"""Per-device conversation history and short-lived reply audio."""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    messages: list[dict[str, str]] = field(default_factory=list)
    last_seen: float = 0.0


class SessionStore:
    def __init__(self, max_turns: int, idle_seconds: float, clock=time.monotonic) -> None:
        self._max_turns = max_turns
        self._idle = idle_seconds
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def history(self, device_id: str) -> list[dict[str, str]]:
        """Returns a copy of the device's recent turns; idle sessions start fresh."""
        with self._lock:
            s = self._sessions.get(device_id)
            if s is None or self._clock() - s.last_seen > self._idle:
                return []
            return list(s.messages)

    def append(self, device_id: str, user: str, assistant: str) -> None:
        with self._lock:
            s = self._sessions.get(device_id)
            now = self._clock()
            if s is None or now - s.last_seen > self._idle:
                s = self._sessions[device_id] = Session()
            s.messages += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
            s.messages = s.messages[-2 * self._max_turns :]
            s.last_seen = now

    def reset(self, device_id: str) -> None:
        with self._lock:
            self._sessions.pop(device_id, None)


@dataclass
class _Clip:
    created: float
    ready: threading.Event = field(default_factory=threading.Event)
    pcm: bytes | None = None  # None while pending, or if synthesis failed


class AudioStore:
    """Holds synthesized replies until the board fetches them.

    A key can be reserved before its audio exists (later sentences of a reply are still being
    synthesized); get() then waits for fill() or fail().
    """

    def __init__(self, ttl_seconds: float, clock=time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: dict[str, _Clip] = {}
        self._lock = threading.Lock()

    def reserve(self) -> str:
        key = secrets.token_urlsafe(12)
        with self._lock:
            now = self._clock()
            self._items = {k: v for k, v in self._items.items() if now - v.created <= self._ttl}
            self._items[key] = _Clip(now)
        return key

    def fill(self, key: str, pcm: bytes) -> None:
        self._finish(key, pcm)

    def fail(self, key: str) -> None:
        self._finish(key, None)

    def _finish(self, key: str, pcm: bytes | None) -> None:
        with self._lock:
            clip = self._items.get(key)
        if clip is not None:
            clip.pcm = pcm
            clip.ready.set()

    def put(self, pcm: bytes) -> str:
        key = self.reserve()
        self.fill(key, pcm)
        return key

    def get(self, key: str, wait_seconds: float = 0.0) -> bytes | None:
        """The audio, or None if unknown, expired, failed, or still pending after wait_seconds."""
        with self._lock:
            clip = self._items.get(key)
            if clip is None or self._clock() - clip.created > self._ttl:
                return None
        clip.ready.wait(wait_seconds)
        return clip.pcm

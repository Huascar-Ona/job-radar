"""Synthetic Claude Code transcripts for the H1 harness-metrics tests.

Shaped like the records observed in Claude Code 2.1.x, never copied from a
real session. Every field that could carry private content holds ``CANARY``
(or a value containing it), so a test can prove that nothing derived from
such a field reaches any output.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

CANARY: Final = "CANARY-7f3a"
SESSION_ID: Final = "11111111-2222-3333-4444-555555555555"
T0: Final = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


def usage(
    *,
    input_tokens: int = 3,
    output: int = 100,
    cache_write_1h: int = 50,
    cache_write_5m: int = 0,
    cache_read: int = 1000,
    thinking: int | None = 40,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    cache_write = cache_write_1h + cache_write_5m
    top: dict[str, object] = {
        "input_tokens": input_tokens,
        "output_tokens": output,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
    }
    result: dict[str, object] = {
        **top,
        "cache_creation": {
            "ephemeral_5m_input_tokens": cache_write_5m,
            "ephemeral_1h_input_tokens": cache_write_1h,
        },
        "iterations": [{**top, "type": "message"}],
        "service_tier": "standard",
        "speed": "standard",
        "inference_geo": "global",
        "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        "fallback_credit": None,
    }
    if thinking is not None:
        result["output_tokens_details"] = {"thinking_tokens": thinking}
    if extra:
        result.update(extra)
    return result


@dataclass
class TranscriptBuilder:
    session_id: str = SESSION_ID
    lines: list[str] = field(default_factory=list)
    clock: datetime = T0

    def _tick(self) -> str:
        self.clock += timedelta(seconds=10)
        return self.clock.isoformat().replace("+00:00", "Z")

    def raw(self, text: str) -> "TranscriptBuilder":
        self.lines.append(text)
        return self

    def record(self, **fields: object) -> "TranscriptBuilder":
        self.lines.append(json.dumps(fields))
        return self

    def private_records(self) -> "TranscriptBuilder":
        """Records the parser must skip whole: they carry private content."""
        self.record(
            type="session-owner",
            sessionId=self.session_id,
            accountUuid=f"acct-{CANARY}",
            organizationUuid=f"org-{CANARY}",
        )
        self.record(
            type="api-request-blob",
            sessionId=self.session_id,
            message={"role": "user", "content": f"secret prompt {CANARY}"},
        )
        self.record(type="last-prompt", sessionId=self.session_id, lastPrompt=CANARY)
        self.record(type="ai-title", sessionId=self.session_id, aiTitle=CANARY)
        self.record(
            type="file-history-snapshot",
            messageId="x",
            snapshot={"/Users/someone/private.py": CANARY},
        )
        return self

    def prompt(self) -> "TranscriptBuilder":
        return self.record(
            type="user",
            sessionId=self.session_id,
            promptSource="typed",
            origin={"kind": "human"},
            timestamp=self._tick(),
            cwd=f"/Users/someone/{CANARY}",
            gitBranch=CANARY,
            version="2.1.322",
            message={"role": "user", "content": f"please do {CANARY}"},
        )

    def tool_result(self) -> "TranscriptBuilder":
        return self.record(
            type="user",
            sessionId=self.session_id,
            timestamp=self._tick(),
            toolUseResult={"stdout": CANARY},
            message={
                "role": "user",
                "content": [{"type": "tool_result", "content": CANARY}],
            },
        )

    def assistant(
        self,
        message_id: str,
        *,
        blocks: list[dict[str, object]] | None = None,
        usage_fields: dict[str, object] | None = None,
        model: str = "claude-opus-5-5",
        stop_reason: str = "tool_use",
        sidechain: bool = False,
        session_id: str | None = None,
    ) -> "TranscriptBuilder":
        """One line per content block, every line repeating the usage."""
        content = blocks or [{"type": "text", "text": f"answer {CANARY}"}]
        stamp = self._tick()
        for block in content:
            self.record(
                type="assistant",
                sessionId=session_id or self.session_id,
                isSidechain=sidechain,
                timestamp=stamp,
                requestId=f"req_{message_id}",
                message={
                    "id": message_id,
                    "model": model,
                    "role": "assistant",
                    "stop_reason": stop_reason,
                    "content": [block],
                    "usage": usage_fields if usage_fields is not None else usage(),
                },
            )
        return self

    def turn_duration(self, ms: object) -> "TranscriptBuilder":
        return self.record(
            type="system",
            subtype="turn_duration",
            sessionId=self.session_id,
            timestamp=self._tick(),
            durationMs=ms,
        )

    def compaction(self) -> "TranscriptBuilder":
        return self.record(
            type="system",
            subtype="compact_boundary",
            sessionId=self.session_id,
            timestamp=self._tick(),
            content=f"Conversation compacted {CANARY}",
        )

    def write(self, directory: Path) -> Path:
        path = directory / f"{self.session_id}.jsonl"
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        return path


def thinking_block() -> dict[str, object]:
    return {"type": "thinking", "thinking": f"private reasoning {CANARY}"}


def text_block() -> dict[str, object]:
    return {"type": "text", "text": f"visible answer {CANARY}"}


def tool_block(block_id: str, name: str = "Bash") -> dict[str, object]:
    return {
        "type": "tool_use",
        "id": block_id,
        "name": name,
        "input": {"command": f"cat ~/.ssh/{CANARY}"},
        "caller": {"type": "direct"},
    }

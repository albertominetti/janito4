"""ACP agent: protocol handlers bridging janito's agentic loop into ACP.

The agent is a plain object answering ACP JSON-RPC methods (``initialize``,
``session/new``, ``session/prompt``) and notifications (``session/cancel``).
Turn execution reuses the web backend's async generator
``janito.web.backend.agent.loop.stream_prompt``; the ``turn_runner`` parameter
allows tests to inject a fake generator.
"""

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from collections.abc import AsyncGenerator, Callable

from janito.web.backend.agent.loop import stream_prompt
from janito.web.backend.config import WebServerConfig

from .events import map_event, text_block
from .protocol import INVALID_PARAMS, INTERNAL_ERROR, METHOD_NOT_FOUND, RpcError

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1

TurnRunner = Callable[..., AsyncGenerator]


@dataclass
class AcpSession:
    """The per-client-session conversation state."""

    session_id: str
    cwd: str
    messages: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


def blocks_to_text(blocks) -> str:
    """Convert ACP prompt content blocks into a plain-text user message."""
    parts = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            parts.append(block.get("text", ""))
        elif block_type == "resource":
            resource = block.get("resource") or {}
            text = resource.get("text")
            uri = resource.get("uri", "")
            if text:
                parts.append(f"{text}\n[source: {uri}]" if uri else text)
            elif uri:
                parts.append(f"[source: {uri}]")
        elif block_type == "resource_link":
            parts.append(f"[Referenced file: {block.get('uri', '')}]")
        elif block_type in ("image", "audio"):
            parts.append(
                f"[{block_type} content ignored: janito has no {block_type} input capability]"
            )
        else:
            continue
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


class JanitoAgent:
    """ACP agent backed by janito's web-loop turn runner."""

    def __init__(
        self,
        config: WebServerConfig,
        *,
        turn_runner: TurnRunner | None = None,
        writer=None,
    ):
        self.config = config
        self._turn_runner = turn_runner or stream_prompt
        self.writer = writer  # a StdioWriter, wired up by JsonRpcServer
        self._sessions: dict[str, AcpSession] = {}
        self._running: dict[str, asyncio.Task] = {}
        # Serializes turns and keeps chdir session-scoped (see _run_turn).
        self._turn_lock = asyncio.Lock()

    def _agent_info(self) -> dict[str, Any]:
        from janito._version import __version__

        return {"name": "janito", "title": "Janito", "version": __version__}

    async def handle_request(self, method: str, params: dict) -> Any:
        if method == "initialize":
            return self._initialize(params)
        if method == "session/new":
            return await self._new_session(params)
        if method == "session/prompt":
            return await self._prompt(params)
        raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")

    async def handle_notification(self, method: str, params: dict) -> None:
        if method == "session/cancel":
            self._cancel(params)
            return
        logger.debug("Ignoring unknown ACP notification: %s", method)

    def _initialize(self, params: dict) -> dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {
                    "image": False,
                    "audio": False,
                    "embeddedContext": True,
                },
                "mcpCapabilities": {"http": False, "sse": False},
            },
            "agentInfo": self._agent_info(),
            "authMethods": [],
        }

    async def _new_session(self, params: dict) -> dict[str, Any]:
        cwd = params.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise RpcError(INVALID_PARAMS, "cwd is required and must be an absolute path")
        if not os.path.isabs(cwd):
            raise RpcError(INVALID_PARAMS, f"cwd must be an absolute path, got: {cwd!r}")

        mcp_servers = params.get("mcpServers") or []
        if mcp_servers:
            logger.warning(
                "session/new provided %d MCP server(s); janito uses its own configured "
                "MCP services and ignores the provided ones",
                len(mcp_servers),
            )

        session = AcpSession(session_id=uuid.uuid4().hex, cwd=cwd)
        system_prompt = self.config.get_effective_system_prompt()
        if system_prompt and not self.config.no_system_prompt:
            session.messages.append({"role": "system", "content": system_prompt})
        self._sessions[session.session_id] = session
        return {"sessionId": session.session_id}

    async def _prompt(self, params: dict) -> dict[str, Any]:
        session = self._require_session(params)
        prompt_blocks = params.get("prompt")
        if not isinstance(prompt_blocks, list):
            raise RpcError(INVALID_PARAMS, "prompt must be an array of content blocks")
        text = blocks_to_text(prompt_blocks)
        if not text:
            raise RpcError(INVALID_PARAMS, "prompt contains no supported content")

        task = asyncio.current_task()
        if task is None:
            raise RpcError(INTERNAL_ERROR, "session/prompt must run inside a task")
        self._running[session.session_id] = task
        try:
            await self._run_turn(session, text)
        except asyncio.CancelledError:
            logger.info("Prompt for session %s cancelled", session.session_id)
            return {"stopReason": "cancelled"}
        except Exception as exc:  # noqa: BLE001 - boundary: report, then end turn
            logger.exception("Prompt for session %s failed", session.session_id)
            await self._notify(
                session.session_id,
                {"sessionUpdate": "agent_message_chunk", "content": text_block(f"Agent error: {exc}")},
            )
        finally:
            # Only clear our own registration: a newer prompt on the same
            # session may already have replaced it.
            if self._running.get(session.session_id) is task:
                self._running.pop(session.session_id, None)
        return {"stopReason": "end_turn"}

    def _require_session(self, params: dict) -> AcpSession:
        session_id = params.get("sessionId")
        session = self._sessions.get(session_id)
        if session is None:
            raise RpcError(INVALID_PARAMS, f"Unknown session: {session_id!r}")
        return session

    def _cancel(self, params: dict) -> None:
        session_id = params.get("sessionId")
        task = self._running.get(session_id)
        if task is not None:
            logger.info("Cancelling running prompt for session %s", session_id)
            task.cancel()

    async def _run_turn(self, session: AcpSession, text: str) -> None:
        """Run one turn with the session's cwd applied, restoring on exit."""
        async with self._turn_lock:
            previous_cwd = os.getcwd()
            if os.path.abspath(previous_cwd) != os.path.abspath(session.cwd):
                os.chdir(session.cwd)
            try:
                await self._stream_turn(session, text)
            finally:
                os.chdir(previous_cwd)

    async def _stream_turn(self, session: AcpSession, text: str) -> None:
        message_id = uuid.uuid4().hex[:16]
        async for event in self._turn_runner(text, session.messages, self.config):
            update = map_event(event, message_id=message_id, cwd=session.cwd)
            if update:
                await self._notify(session.session_id, update)

    async def _notify(self, session_id: str, update: dict[str, Any]) -> None:
        if self.writer is None:
            return
        await self.writer.write(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": session_id, "update": update},
            }
        )

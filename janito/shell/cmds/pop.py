"""/pop command - return to previous stack level (issue #124)."""

from ..conversation import effective_rows
from .base import CmdHandler
from .registry import register_command


def _resolve_observer(shell):
    """Return the session's explicit turn observer, or ``None``.

    Explicit dependency only (issue #31): ``shell.observer`` (set from the
    turn function's explicit ``observer`` attribute in
    ``InteractiveShell.run``) or ``shell.turn_func.observer``. No closure
    inspection and no concrete-observer fallback -- callers print plain
    text when no observer is configured.
    """
    observer = getattr(shell, "observer", None)
    if observer is not None and hasattr(observer, "on_message"):
        return observer
    turn_func = getattr(shell, "turn_func", None)
    observer = getattr(turn_func, "observer", None)
    if observer is not None and hasattr(observer, "on_message"):
        return observer
    return None


def _last_assistant_message(shell) -> str | None:
    for role, content in reversed(effective_rows(shell)):
        if role == "assistant" and content:
            return content
    return None


class PopCmdHandler(CmdHandler):
    @property
    def name(self) -> str:
        return "/pop"

    @property
    def description(self) -> str:
        return "Return to the previous stack level"

    def handle(self, shell, user_input: str) -> bool:
        if user_input.lower().strip() == self.name:
            try:
                depth = shell.conversation_stack.pop(shell)
            except IndexError:
                print("Nothing to pop. Stack is empty.")
                return True
            label = f"thread [{depth}]" if depth else "main thread"
            print(f"Returned to {label}")
            last = _last_assistant_message(shell)
            if last:
                print("Last Message:")
                observer = _resolve_observer(shell)
                if observer is not None:
                    observer.on_message(last)
                else:
                    print(last)
            return True
        return False


_handler = PopCmdHandler()
register_command(_handler)

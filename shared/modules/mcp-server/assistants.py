"""What to click in each assistant: the steps `smoking-pi connect NAME` prints.

Every assistant connects the same way (connector.py: a URL, then a sign-in
with a pairing code), so nothing here changes what the server does. What
differs is the assistant's own menus: where a remote MCP server is added,
where its instructions go, and where its sign-in returns to -- which the
pairing page shows, so the owner can check it before typing the code.

These are data, not code: adding an assistant is one more entry. Menus move
between versions, so the steps name what to look for, and every printout
ends with the generic words ("custom connector", "remote MCP server") to
fall back on. An unknown name gets GENERIC.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Assistant:
    key: str
    name: str
    # Steps to add the connector, in order; "{url}" is the connector URL.
    add: tuple[str, ...]
    # Where the paragraph from guide.ASSISTANT_INSTRUCTIONS goes.
    instructions: str
    # What the pairing page should say the sign-in returns to; "" if unknown.
    returns_to: str = ""
    aliases: tuple[str, ...] = ()
    note: str = ""
    # On this machine, with the local token: `smoking-pi connect openclaw`.
    local: bool = False


GENERIC = Assistant(
    key="",
    name="your assistant",
    add=("Add the URL as a custom connector (or remote MCP server) in the assistant.",
         "Its sign-in opens a Smoking Pi page in your browser: type the code there."),
    instructions="the assistant's custom instructions, rules or system prompt",
)

ASSISTANTS: tuple[Assistant, ...] = (
    Assistant(
        key="claude",
        name="Claude (claude.ai and the Claude apps)",
        aliases=("claude.ai", "claude-desktop", "claude-app"),
        add=("On claude.ai: Settings > Connectors > Add custom connector.",
             "Name it Smoking Pi and paste the URL. Leave the advanced OAuth fields empty.",
             "Click Connect: a Smoking Pi page opens. Type the code there."),
        instructions="the personal preferences in Claude's settings, or a project's instructions",
        returns_to="claude.ai",
        note="A connector added on claude.ai also shows up in the desktop and mobile apps; "
             "if a chat does not use it, turn it on in that chat's tools menu.",
    ),
    Assistant(
        key="claude-code",
        name="Claude Code",
        aliases=("claudecode", "claude_code"),
        add=("On the computer Claude Code runs on:",
             "  claude mcp add --transport http smoking-pi {url}",
             "In Claude Code, run /mcp, pick smoking-pi and Authenticate: a Smoking Pi "
             "page opens in your browser. Type the code there."),
        instructions="CLAUDE.md (~/.claude/CLAUDE.md applies to every project)",
        returns_to="localhost (Claude Code's own sign-in listener)",
    ),
    Assistant(
        key="chatgpt",
        name="ChatGPT",
        aliases=("openai", "gpt"),
        add=("Settings > Apps & Connectors > Advanced settings: turn on Developer mode.",
             "Back in Apps & Connectors, create a connector: name it Smoking Pi, paste "
             "the URL, authentication OAuth.",
             "Its sign-in opens a Smoking Pi page: type the code there."),
        instructions="Settings > Personalization > Custom instructions",
        returns_to="chatgpt.com",
        note="In a chat, pick Developer mode in the tools menu and turn Smoking Pi on.",
    ),
    Assistant(
        key="cursor",
        name="Cursor, or an agent on Cursor's agent platform",
        add=("Cursor Settings > Tools & MCP > New MCP server, or add to ~/.cursor/mcp.json:",
             '  {"mcpServers": {"smoking-pi": {"url": "{url}"}}}',
             "Cursor lists it as needing a login: click Connect, and type the code on "
             "the Smoking Pi page that opens."),
        instructions="Cursor Settings > Rules (user rules), or the agent's own rules",
        returns_to="cursor.com, or the Cursor app",
    ),
    Assistant(
        key="grok",
        name="Grok, or an agent built on it",
        aliases=("grokbot", "xai"),
        add=("Add the URL wherever the agent takes a remote MCP server (a custom "
             "connector or MCP server).",
             "Its sign-in opens a Smoking Pi page: type the code there."),
        instructions="the agent's system prompt or rules",
        note="An agent that runs on Cursor's agent platform signs in through Cursor: "
             "its page says it returns to cursor.com.",
    ),
    Assistant(
        key="openclaw",
        name="OpenClaw on this machine",
        add=(),
        instructions="the Smoking Pi skill (installed for you)",
        local=True,
    ),
)


def _all_names(a: Assistant) -> tuple[str, ...]:
    return (a.key, *a.aliases)


def by_key(kind: str) -> Assistant | None:
    """The template called ``kind`` (a key or an alias), any case."""
    kind = kind.strip().lower()
    for a in ASSISTANTS:
        if kind in _all_names(a):
            return a
    return None


def for_label(label: str) -> Assistant:
    """The template a connector label names: the label itself (``claude``),
    or one with a suffix (``claude-work``, ``grok.home``). The longest name
    wins, so ``claude-code-laptop`` is Claude Code, not Claude. Anything
    else is GENERIC."""
    label = label.strip().lower()
    best, best_len = GENERIC, 0
    for a in ASSISTANTS:
        for n in _all_names(a):
            if (label == n or label.startswith((n + "-", n + "_", n + "."))) \
                    and len(n) > best_len:
                best, best_len = a, len(n)
    return best


def steps(a: Assistant, url: str) -> list[str]:
    """The numbered steps for ``a``; an indented line continues the step
    before it (a command to run, a file's contents)."""
    out, n = [], 0
    for line in a.add:
        line = line.replace("{url}", url)
        if line.startswith("  "):
            out.append(f"     {line.strip()}")
        else:
            n += 1
            out.append(f"  {n}. {line}")
    return out

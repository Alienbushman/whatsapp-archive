import json
import os
from typing import Generator

import httpx

_DEFAULT_OLLAMA_URL = "http://localhost:11434"
_DEFAULT_MODEL = os.environ.get("OLLAMA_GEN_MODEL", "qwen2.5:3b-instruct")

_STOP_WORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would could should may might shall can tell me about please "
    "what who when where why how give find show list all of in on at "
    "to for from with by into than then that this these those it its "
    "i you we they he she and or but not".split()
)


def _rag_fts_query(question: str) -> str:
    """Extract meaningful keywords from a natural language question for FTS5 MATCH.

    Returns an OR-joined keyword query so partial matches are included.
    """
    tokens = [w.strip('.,?!;:"\'') for w in question.lower().split()]
    keywords = [t for t in tokens if t and t not in _STOP_WORDS and len(t) > 1]
    if not keywords:
        return question
    return " OR ".join(keywords)


_SYSTEM_PROMPT = (
    "You are an analyst helping search a WhatsApp chat archive. "
    "Answer the user's question using ONLY the messages and articles below. "
    "Cite messages by their [N] number. "
    "If the answer isn't in the context, say so clearly."
)


def build_context(
    msg_hits: list[dict],
    art_hits: list[dict],
    max_chars: int = 12000,
) -> list[str]:
    blocks: list[str] = []
    total = 0

    for i, m in enumerate(msg_hits, 1):
        block = f"[{i}] {m['ts']} | {m['chat_name']} | {m['sender']}: {m['body']}"
        if total + len(block) > max_chars:
            break
        blocks.append(block)
        total += len(block)

    offset = len(msg_hits)
    for i, a in enumerate(art_hits, offset + 1):
        import json as _json
        # Try to extract tweet author + text for richer context
        tweet_text = a.get("body") or ""
        author_prefix = ""
        raw_meta = a.get("tweet_meta")
        if raw_meta:
            try:
                tm = _json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                author = tm.get("author_handle") or tm.get("author_name") or ""
                if author:
                    author_prefix = f"@{author}: "
                if not tweet_text:
                    tweet_text = tm.get("text") or ""
            except Exception:
                pass
        content = f"{author_prefix}{tweet_text[:500]}" if tweet_text else (a.get("summary") or "")[:500]
        if not content:
            content = (a.get("summary") or "")[:500]
        block = f"[{i}] {a.get('title', '')} ({a['url']})\n{content}"
        if total + len(block) > max_chars:
            break
        blocks.append(block)
        total += len(block)

    return blocks


def stream_answer(
    question: str,
    context_blocks: list[str],
    ollama_url: str = _DEFAULT_OLLAMA_URL,
    model: str = _DEFAULT_MODEL,
) -> Generator[str, None, None]:
    context_text = "\n\n".join(context_blocks) if context_blocks else "(no context available)"
    user_msg = f"{question}\n\nContext:\n{context_text}"

    with httpx.Client(timeout=120) as client:
        with client.stream(
            "POST",
            f"{ollama_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "stream": True,
                "options": {"temperature": 0.3},
            },
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield f"data: {json.dumps({'token': token})}\n\n"
                    if chunk.get("done"):
                        yield "data: [DONE]\n\n"
                        return
                except (json.JSONDecodeError, KeyError):
                    continue

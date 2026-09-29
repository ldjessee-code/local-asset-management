# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local Ollama tagger. Tests inject a fake; nothing here opens a socket unless called.

Confidence when a model is consulted (temperature 0):

- high if the first answer matches a non-unsure rule (agreement), or
- high if a second, reworded prompt returns the same label (an identical
  prompt at temperature 0 would prove nothing), or
- unsure if the answer is Unsure, otherwise low.

Two retries per call (three attempts). A connection failure becomes
``ModelUnavailable`` so the scan can fall back to rules once.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from lam.tag.bins import DEFAULT_MODEL, DEFAULT_MODEL_URL, LEVEL1_BINS, UNSURE
from lam.tag.errors import TagError

MAX_RETRIES = 2
LEVEL1_LABELS: tuple[str, ...] = (*LEVEL1_BINS, UNSURE)

_BLURBS = {
    "_Maps": "a map or overhead view of a location",
    "_GameMods": "a modification package for a game",
    "_Installers": "a software installer",
    "_Documents": "a document or plain text",
    "_ModelWeights": "a neural-network weight file",
    "_Images": "a picture that is not a map",
    "_Archives": "a compressed archive",
    "_Unknown": "none of the bins fit",
    "Unsure": "you cannot tell",
}


class ModelUnavailable(Exception):
    """The local model server could not be reached."""


class TransientModelError(Exception):
    """The server answered, but the reply was unusable."""


@dataclass(frozen=True)
class TagRequest:
    prompt: str
    labels: tuple[str, ...]
    image: bytes | None
    filename: str
    rule_bin: str
    rule_id: str


@dataclass(frozen=True)
class TagResult:
    label: str


def check_model_name(name: str) -> str:
    if "qwen3.8" in name.casefold():
        raise TagError(f"refusing model name {name!r} (blocked substring qwen3.8)")
    if not name.strip() or any(ch in name for ch in "\r\n"):
        raise TagError("model name is empty")
    return name


def check_model_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or host not in {"127.0.0.1", "localhost", "::1"}:
        raise TagError("model URL must point at localhost")
    return url


def prompt_primary(
    *,
    filename: str,
    extension: str,
    size: int,
    folder: str,
    rule_bin: str,
    rule_id: str,
    labels: tuple[str, ...],
    descriptions: dict[str, str] | None = None,
) -> str:
    listing = _listing(labels, descriptions)
    return (
        "Sort this one file into exactly one label.\n"
        "Reply with JSON only. The label must be exactly one entry from this list, or Unsure.\n"
        f"{listing}\n"
        f"File name: {filename}\n"
        f"Extension: {extension or '(none)'}\n"
        f"Size in bytes: {size}\n"
        f"Folder name: {folder or '(top)'}\n"
        f"Mechanical rule: {rule_bin} ({rule_id})\n"
        "Do not invent a label outside the list.\n"
    )


def prompt_confirm(
    *,
    filename: str,
    extension: str,
    size: int,
    folder: str,
    rule_bin: str,
    rule_id: str,
    labels: tuple[str, ...],
    descriptions: dict[str, str] | None = None,
) -> str:
    """Reworded second look. Temperature 0 makes a repeated prompt meaningless."""
    listing = _listing(labels, descriptions)
    return (
        "Look again, independently of any earlier wording.\n"
        "Choose the single best label for the file described below.\n"
        "If you cannot tell, answer Unsure.\n"
        f"The only allowed labels are:\n{listing}\n"
        f"Name: {filename}\n"
        f"Extension: {extension or '(none)'}\n"
        f"Bytes: {size}\n"
        f"Containing folder: {folder or '(top)'}\n"
        f"A fixed rule guessed {rule_bin} via {rule_id}. "
        "You may keep that guess or pick a different allowed label.\n"
        "Return JSON only.\n"
    )


def _listing(labels: tuple[str, ...], descriptions: dict[str, str] | None) -> str:
    lines: list[str] = []
    extra = descriptions or {}
    for label in labels:
        detail = extra.get(label) or _BLURBS.get(label, "")
        lines.append(f"- {label}: {detail}" if detail else f"- {label}")
    return "\n".join(lines)


def build_chat_body(model: str, prompt: str, labels: tuple[str, ...] | list[str], image: bytes | None) -> dict:
    message: dict = {"role": "user", "content": prompt}
    if image is not None:
        message["images"] = [base64.b64encode(image).decode("ascii")]
    return {
        "model": model,
        "think": False,
        "stream": False,
        "messages": [message],
        "format": {
            "type": "object",
            "properties": {
                "bin": {"type": "string", "enum": list(labels)},
            },
            "required": ["bin"],
        },
        "options": {"temperature": 0},
    }


def parse_model_response(raw: bytes, labels: set[str]) -> str:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransientModelError("model response was not JSON") from exc
    content = ""
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, dict):
            content = message.get("content", "")
        elif "bin" in data:
            content = data
    label = _extract_label(content)
    if label not in labels:
        raise TransientModelError(f"unexpected label {label!r}")
    return label


def _extract_label(content: object) -> str:
    if isinstance(content, dict):
        for key in ("bin", "label", "type"):
            if key in content:
                return str(content[key]).strip()
    text = str(content).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text.strip().strip('"')
    if isinstance(parsed, dict):
        for key in ("bin", "label", "type"):
            if key in parsed:
                return str(parsed[key]).strip()
    return str(parsed).strip()


def urllib_post(url: str, payload: bytes, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise TransientModelError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
        raise ModelUnavailable(str(exc)) from exc


class OllamaTagger:
    """POST ``/api/chat`` on a localhost Ollama. ``poster`` is injectable."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_MODEL_URL,
        poster=None,
        timeout: float = 60.0,
    ) -> None:
        self.model_name = check_model_name(model)
        self.base_url = check_model_url(base_url).rstrip("/")
        self._poster = poster or urllib_post
        self.timeout = timeout

    def classify(self, request: TagRequest) -> TagResult:
        body = build_chat_body(self.model_name, request.prompt, request.labels, request.image)
        payload = json.dumps(body).encode("utf-8")
        url = self.base_url + "/api/chat"
        last: Exception | None = None
        for _attempt in range(MAX_RETRIES + 1):
            try:
                raw = self._call(url, payload)
                return TagResult(label=parse_model_response(raw, set(request.labels)))
            except ModelUnavailable as exc:
                last = exc
            except TransientModelError as exc:
                last = exc
        if isinstance(last, ModelUnavailable):
            raise last
        raise TransientModelError(str(last) if last else "model failed")

    def _call(self, url: str, payload: bytes) -> bytes:
        try:
            return self._poster(url, payload, self.timeout)
        except TransientModelError:
            raise
        except ModelUnavailable:
            raise
        except urllib.error.HTTPError as exc:
            raise TransientModelError(f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            raise ModelUnavailable(str(exc)) from exc

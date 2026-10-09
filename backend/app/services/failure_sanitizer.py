"""Clean-up helpers for failure and step text that is shown to users, logged or stored.

Recorded diagnostics (messages, source lines, tracebacks, URLs, step targets and
typed step values) can carry things a user should never be shown or that are
just noise:
  * secrets typed or passed by the test (passwords, tokens, bearer headers,
    cookies, URL credentials);
  * absolute filesystem paths of the throwaway run directory / interpreter;
  * Selenium's long native "Stacktrace:" block.

This is the single sanitizer used by persistence, the results API, the failure
analysis, the HTML/Excel/PDF reports and the application log. Every function is
pure, never raises on odd input, and is idempotent (safe to apply to text that
was already sanitized, e.g. a stored traceback). Stored rows are never rewritten
for old data: it is sanitized when read or exported.

What it can and cannot do
-------------------------
Masked: values whose *name* looks secret (password=..., "token": "...",
Authorization, cookies, session ids), URL credentials and sensitive query or
fragment parameters, JWTs, values typed into a field whose locator names a
secret (id=password), and any literal that the uploaded script itself passes to
a secret-named field (harvested from the script source, see
collect_known_secrets). Not detectable: a secret with no secret-looking name
that appears nowhere in the script source (for example a value typed into a
field called "code", or entered by a data file); and pixels in screenshots.
"""
from __future__ import annotations

import re
from typing import Iterable

MAX_MESSAGE_CHARS = 1500
MAX_SOURCE_LINE_CHARS = 300
MAX_TRACEBACK_CHARS = 12000
MAX_EXCEPTION_TYPE_CHARS = 255
MASK = "***"

# Names that mark a value as secret. The key may carry a prefix/suffix
# (client_secret, access_token, AWS_SECRET_ACCESS_KEY) and be quoted
# ("password": "x"). "author" is deliberately NOT matched.
_SECRET_WORDS = (
    r"password|passwd|pwd|passcode|passphrase|secret|token|api[_-]?key|access[_-]?key"
    r"|authorization|session[_-]?id|csrf|xsrf"
)
# The key prefix/suffix are length-bounded: an unbounded prefix backtracks
# quadratically on long lines of word characters (a 20k-char line took ~20s,
# enough to stall result persistence for a test that prints a long line).
# Short secrets (PINs, one-time codes): matched only as whole words, so "spinner=on" or
# "pinned=yes" are left alone.
_SHORT_SECRET_WORDS = r"(?<![A-Za-z])(?:pin|otp|cvv)(?![A-Za-z])"
_ANY_SECRET_WORD = rf"(?:{_SECRET_WORDS}|{_SHORT_SECRET_WORDS})"
_SECRET_KV = re.compile(
    # key (keyword + bounded possessive suffix, so "client_secret" / "access_token" match
    # on the keyword and nothing backtracks) + separator, then the value to mask.
    rf"(?i)({_ANY_SECRET_WORD}[A-Za-z0-9_-]{{0,40}}+[\"']?\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
)
_SECRET_WORD_PRESENT = re.compile(rf"(?i){_ANY_SECRET_WORD}")
_COOKIE = re.compile(r"(?i)\b((?:set-)?cookie)(\s*[:=]\s*)[^\r\n]+")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]*@")
_TEXT_URL = re.compile(r"(?i)\b(?:https?|wss?|ftp)://[^\s'\"<>]+")
_URL_SCHEME = re.compile(r"(?i)^[a-z][a-z0-9+.\-]*://")

# Hint that a line / locator deals with a secret field. Letter-boundaries keep
# words such as "passenger", "compass" or "bypass" from matching "pass".
_SENSITIVE_HINT = re.compile(
    r"(?i)(?<![a-z])pass(?![a-z])|pass(word|wd|code|phrase)|pwd|secret|token"
    r"|(?<![a-z])otp(?![a-z])|(?<![a-z])cvv(?![a-z])"
)
_QUOTED = re.compile(r"""(["'])(?:(?!\1).)*\1""")
_QUOTED_CAPTURE = re.compile(r"""(["'])((?:(?!\1).)*)\1""")
_SEND_KEYS_CALL = re.compile(r"send_keys\((.*)\)")
_COMPARE_LITERAL = re.compile(r"""(==|!=)(\s*)(["'])(?:(?!\3).)*\3""")
# pytest assertion output: "[E ][AssertionError: ]assert ..." and "E   - x" / "E   + y" diff lines.
_ASSERT_LINE = re.compile(r"^(\s*(?:E\s+)?(?:AssertionError:\s*)?)(assert\b.*)$")
_ASSERT_DIFF_LINE = re.compile(r"^(\s*E\s+[-+?]\s+).+$")

# Absolute paths (unix or windows, directories may contain spaces) ending in a
# .py file -> keep the basename only.
# Segment length and count are bounded so slash-heavy long lines cannot backtrack badly.
_ABS_PY_PATH = re.compile(
    r"(?:[A-Za-z]:)?[\\/](?:[^\\/:'\"<>|\r\n]{1,255}[\\/]){1,64}([^\\/:'\"<>|\s]{1,255}\.py)"
)
_STACKTRACE_BLOCK = re.compile(r"\n?\s*Stacktrace:.*", re.S)
# Selenium's native driver stack as pytest renders it: "E   Stacktrace:",
# "E   #0 0x... <unknown>", "E   chromedriver!GetHandleVerifier [0x...]".
_NATIVE_STACK_LINE = re.compile(
    r"^\s*(?:E\s+)?(?:Stacktrace:\s*|#\d+\s+0x[0-9a-fA-F]+.*|[\w.]+!\S+.*\[0x[0-9a-fA-F]+.*)$"
)
# Anchored on the literal text (a leading "[^\n]*" rescans the whole line from every
# start position: quadratic on long lines). Only the doc-link tail is removed.
_DOC_LINK = re.compile(r"[;,]?[ \t]*For documentation on this error, please visit:[^\n]*")

# Recorded step actions that type a value into a field.
_TYPE_ACTIONS = {"type", "sendkeys", "send_keys", "typekeys", "settext", "entertext", "editcontent"}


def is_typed_action(action: str | None) -> bool:
    return (action or "").strip().lower() in _TYPE_ACTIONS


def mask_all_typed_values() -> bool:
    """Policy: typed values are masked unless PERSIST_TYPED_VALUES=true.

    A field's name does not say whether what was typed is secret, so the default
    is not to keep any typed value. If settings cannot be read, mask."""
    try:
        from app.core.security import settings

        return not bool(getattr(settings, "persist_typed_values", False))
    except Exception:
        return True
_MIN_KNOWN_SECRET_LENGTH = 4
_MAX_KNOWN_SECRETS = 100


def truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[: max(limit - 15, 0)] + " ...[truncated]"


def is_sensitive_name(value: str | None) -> bool:
    """True when a locator/field name suggests a secret (e.g. id=password)."""
    return bool(value and _SENSITIVE_HINT.search(value))


# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------

_SENSITIVE_PARAM = re.compile(
    r"(?i)pass|pwd|secret|token|auth(?!or)|credential|session|signature|jwt|bearer|otp"
    r"|api[_-]?key|access[_-]?key"
)
_SENSITIVE_PARAM_EXACT = {"key", "sig", "sid", "code", "jsessionid", "phpsessid"}


def _is_sensitive_param(name: str) -> bool:
    lowered = name.strip().lower()
    return (
        bool(_SENSITIVE_PARAM.search(lowered))
        or lowered in _SENSITIVE_PARAM_EXACT
        or lowered.endswith(("_key", "-key"))
    )


def _mask_param_string(params: str) -> str:
    """Masks the value of each sensitive name=value pair, leaving the rest of
    the string byte-for-byte as recorded (no re-encoding)."""
    out = []
    for part in params.split("&"):
        name, sep, _value = part.partition("=")
        out.append(f"{name}={MASK}" if sep and _is_sensitive_param(name) else part)
    return "&".join(out)


def redact_url(url: str | None) -> str | None:
    """Masks credentials and sensitive query/fragment values in a URL while
    keeping the scheme, host, path and harmless parameters for debugging."""
    if not url:
        return url
    text = url.strip()
    try:
        scheme = text.split(":", 1)[0].lower() if ":" in text else ""
        if scheme in ("data", "javascript", "about", "blob", "file") or not _URL_SCHEME.match(text):
            # Not a hierarchical URL: apply the generic name-based rules only.
            return _redact_basic(text)
        rest = text.split("://", 1)[1]
        authority, slash, tail = rest.partition("/")
        if "@" in authority:
            authority = f"{MASK}@{authority.rsplit('@', 1)[1]}"
        tail, hash_sep, fragment = tail.partition("#")
        path, q_sep, query = tail.partition("?")
        result = f"{text.split('://', 1)[0]}://{authority}{slash}{path}"
        if q_sep:
            result += "?" + _mask_param_string(query)
        if hash_sep:
            result += "#" + (_mask_param_string(fragment) if "=" in fragment else fragment)
        return result
    except Exception:  # a malformed URL must never break sanitizing
        return truncate(_redact_basic(text), 300)


def redact_urls_in_text(text: str | None) -> str | None:
    """Applies redact_url to every URL embedded in free text."""
    if not text:
        return text

    def fix(match: re.Match[str]) -> str:
        url, trail = match.group(0), ""
        while url and url[-1] in ".,;:)]}":
            trail = url[-1] + trail
            url = url[:-1]
        return (redact_url(url) or url) + trail

    return _TEXT_URL.sub(fix, text)


# --------------------------------------------------------------------------
# Generic text redaction
# --------------------------------------------------------------------------

def _redact_basic(text: str) -> str:
    # Order matters: URL credentials, cookies, bearer/basic and JWTs first, so
    # "Authorization: Bearer <token>" is fully masked (the key/value rule alone
    # would stop at the space after "Bearer").
    redacted = _URL_USERINFO.sub(lambda m: f"{m.group(1)}{MASK}@", text)
    redacted = _COOKIE.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", redacted)
    redacted = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", redacted)
    redacted = _JWT.sub(MASK, redacted)
    if not _SECRET_WORD_PRESENT.search(redacted):  # cheap fast path: nothing to mask
        return redacted
    return _SECRET_KV.sub(lambda m: f"{m.group(1)}{MASK}", redacted)


def redact_secrets(text: str | None) -> str | None:
    if not text:
        return text
    return redact_urls_in_text(_redact_basic(text))


def strip_paths(text: str | None) -> str | None:
    """Absolute .py paths -> basename (the run directory is a temp path)."""
    if not text:
        return text
    return _ABS_PY_PATH.sub(lambda m: m.group(1), text)


# --------------------------------------------------------------------------
# Known secrets harvested from the uploaded script source
# --------------------------------------------------------------------------

_IDE_STEP_COMMENT = re.compile(r"^\s*#\s*\d+\s*\|\s*(\w+)\s*\|\s*([^|]*?)\s*\|\s*(.*?)\s*$", re.M)
_SECRET_ASSIGNMENT = re.compile(
    # no prefix scan (only the value is used); bounded suffix, possessive
    rf"(?i){_ANY_SECRET_WORD}[\w]{{0,40}}+[\"']?\s*[:=]\s*([\"'])(.{{1,200}}?)\1"
)


def _usable_secret(candidate: str | None) -> bool:
    text = (candidate or "").strip()
    return len(text) >= _MIN_KNOWN_SECRET_LENGTH and text.lower() not in {"true", "false", "none", "null"}


def collect_known_secrets(source_texts: Iterable[str | None]) -> tuple[str, ...]:
    """Literals that an uploaded script itself passes to secret-named fields.

    Reads only the script source (already stored in the database): string
    literals inside send_keys(...) on lines that mention a secret-named field,
    assignments such as password = "x", and Selenium IDE step comments
    ("# 5 | type | id=password | value"). The values can then be masked
    wherever they surface (step values, assertion output, URLs, logs)."""
    found: dict[str, None] = {}

    def add(candidate: str | None) -> None:
        if _usable_secret(candidate):
            found[candidate.strip()] = None

    for text in source_texts:
        if not text:
            continue
        for match in _IDE_STEP_COMMENT.finditer(text):
            action, target, value = match.groups()
            if action.lower() in _TYPE_ACTIONS and is_sensitive_name(target):
                add(value)
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            line = line[:2000]  # bound per-line work; real source lines are far shorter
            if _SENSITIVE_HINT.search(line):
                call = _SEND_KEYS_CALL.search(line)
                if call:
                    for literal in _QUOTED_CAPTURE.finditer(call.group(1)):
                        add(literal.group(2))
            assignment = _SECRET_ASSIGNMENT.search(line) if _SECRET_WORD_PRESENT.search(line) else None
            if assignment:
                add(assignment.group(2))
    return tuple(sorted(found, key=len, reverse=True))[:_MAX_KNOWN_SECRETS]


def collect_typed_values(steps: Iterable[tuple[str | None, str | None]]) -> tuple[str, ...]:
    """Values a run actually typed, from (action, value) pairs of recorded steps.

    At execution time every typed value is already recorded by the compat layer,
    including values that came from fixtures or data modules, so this registry
    needs no knowledge of where a value originated. Each one is treated as
    secret and masked wherever it surfaces (messages, tracebacks, URLs, output)."""
    found: dict[str, None] = {}
    for action, value in steps:
        if is_typed_action(action) and _usable_secret(value):
            found[value.strip()] = None
    return tuple(sorted(found, key=len, reverse=True))[:_MAX_KNOWN_SECRETS]


def mask_known_secrets(text: str | None, secrets: Iterable[str] = ()) -> str | None:
    if not text:
        return text
    for secret in secrets:
        if secret and secret in text:
            text = text.replace(secret, MASK)
    return text


# --------------------------------------------------------------------------
# Source lines, messages, types, tracebacks
# --------------------------------------------------------------------------

def redact_source_line(line: str | None) -> str | None:
    """Masks send_keys(...) string literals when the line hints at a secret,
    then applies the generic secret redaction."""
    if not line:
        return line
    if _SENSITIVE_HINT.search(line):
        line = _SEND_KEYS_CALL.sub(
            lambda m: "send_keys(" + _QUOTED.sub(f'"{MASK}"', m.group(1)) + ")", line
        )
        # A literal compared against a secret-named field, e.g.
        # assert field_password.get_attribute("value") == "x".
        line = _COMPARE_LITERAL.sub(lambda m: f'{m.group(1)}{m.group(2)}"{MASK}"', line)
    return redact_secrets(line)


def has_sensitive_context(source_line: str | None, traceback_text: str | None = None) -> bool:
    """True when the failing statement deals with a secret-named field.

    The statement is the stored source line or, when that is empty (pytest does
    not always capture it for failed asserts), the traceback lines pytest marks
    with a leading ">" as the failing statement."""
    if source_line and _SENSITIVE_HINT.search(source_line):
        return True
    return any(
        line.lstrip().startswith(">") and _SENSITIVE_HINT.search(line)
        for line in (traceback_text or "").splitlines()
    )


def _mask_sensitive_assert_lines(text: str) -> str:
    """For a failure whose failing line involves a secret-named field, pytest's
    assertion output (``assert 'a' == 'b'`` and the ``- x`` / ``+ y`` diff
    lines) prints the compared VALUES without any secret-looking name next to
    them, so mask them. Applied only in that context, never to ordinary
    assertion messages."""
    out = []
    for line in text.split("\n"):
        diff = _ASSERT_DIFF_LINE.match(line)
        if diff:
            line = f"{diff.group(1)}{MASK}"
        else:
            assertion = _ASSERT_LINE.match(line)
            if assertion:
                line = assertion.group(1) + _QUOTED.sub(f'"{MASK}"', assertion.group(2))
        out.append(line)
    return "\n".join(out)


def clean_exception_message(
    message: str | None,
    limit: int | None = MAX_MESSAGE_CHARS,
    sensitive_context: bool = False,
    secrets: Iterable[str] = (),
) -> str | None:
    """Drops Selenium's native stacktrace and doc-link noise, then redacts.
    ``limit=None`` keeps the full length (used for the legacy ``failure``
    fields, whose existing contract must not change)."""
    if not message:
        return message
    cleaned = _STACKTRACE_BLOCK.sub("", message)
    cleaned = _DOC_LINK.sub("", cleaned).strip()
    cleaned = strip_paths(redact_secrets(cleaned)) or ""
    if sensitive_context:
        cleaned = _mask_sensitive_assert_lines(cleaned)
    cleaned = mask_known_secrets(cleaned, secrets) or ""
    return cleaned if limit is None else truncate(cleaned, limit)


def clean_exception_type(exception_type: str | None, secrets: Iterable[str] = ()) -> str | None:
    """Exception class names are harmless, but pytest stores a failed plain
    ``assert`` as its expression text, which can contain values or secrets."""
    if not exception_type:
        return exception_type
    cleaned = mask_known_secrets(redact_source_line(strip_paths(exception_type)), secrets) or ""
    return truncate(cleaned, MAX_EXCEPTION_TYPE_CHARS)


def sanitize_traceback(
    text: str | None,
    limit: int | None = MAX_TRACEBACK_CHARS,
    sensitive_context: bool = False,
    secrets: Iterable[str] = (),
) -> str | None:
    """Traceback text safe to store/show: paths -> basenames, secrets masked
    (including send_keys literals on sensitive lines), native driver stack
    removed, size-capped (``limit=None`` keeps the full length)."""
    if not text:
        return text
    lines = [
        redact_source_line(strip_paths(line)) or ""
        for line in text.splitlines()
        if not _NATIVE_STACK_LINE.match(line)
    ]
    joined = "\n".join(lines)
    if sensitive_context:
        joined = _mask_sensitive_assert_lines(joined)
    joined = mask_known_secrets(joined, secrets) or ""
    return joined if limit is None else truncate(joined, limit)


def sanitize_failure_fields(
    exception_type: str | None,
    exception_message: str | None,
    source_line: str | None,
    raw_traceback: str | None,
    secrets: Iterable[str] = (),
) -> tuple[str | None, str | None, str | None, str | None]:
    """The four stored failure texts, sanitized for the existing ``failure``
    API object and the report failure blocks. Redaction only: lengths are kept
    so the existing contract/output is unchanged. Stored rows are not modified."""
    secrets = tuple(secrets)
    context = has_sensitive_context(source_line, raw_traceback)
    return (
        clean_exception_type(exception_type, secrets),
        clean_exception_message(exception_message, limit=None, sensitive_context=context, secrets=secrets),
        mask_known_secrets(redact_source_line(source_line), secrets),
        sanitize_traceback(raw_traceback, limit=None, sensitive_context=context, secrets=secrets),
    )


# --------------------------------------------------------------------------
# Recorded steps
# --------------------------------------------------------------------------

def sanitize_step_target(target: str | None, secrets: Iterable[str] = ()) -> str | None:
    """Step target for output: URLs lose credentials and sensitive parameters,
    other locators go through the generic rules."""
    if not target:
        return target
    cleaned = redact_url(target) if _URL_SCHEME.match(target.strip()) else redact_secrets(target)
    return mask_known_secrets(cleaned, secrets)


def sanitize_step_value(
    target: str | None,
    value: str | None,
    secrets: Iterable[str] = (),
    action: str | None = None,
) -> str | None:
    """A recorded step value (typed text, selected option, ...).

    Typed values (``action`` type/sendKeys) are masked in full by default (see
    mask_all_typed_values). Other values are masked in full when the locator
    names a secret field or the value is a known secret; otherwise only
    secret-looking parts are masked and ordinary values are kept."""
    if not value:
        return value
    secrets = tuple(secrets)
    if is_typed_action(action) and mask_all_typed_values():
        return MASK
    if is_sensitive_name(target) or any(secret and secret in value for secret in secrets):
        return MASK
    cleaned = redact_url(value) if _URL_SCHEME.match(value.strip()) else redact_secrets(value)
    return mask_known_secrets(cleaned, secrets)


def sanitize_step_error(error: str | None, secrets: Iterable[str] = ()) -> str | None:
    return clean_exception_message(error, limit=None, secrets=secrets)


def sanitize_step(
    target: str | None,
    value: str | None,
    error: str | None,
    secrets: Iterable[str] = (),
    action: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """(target, value, error) of one recorded step, sanitized for output."""
    secrets = tuple(secrets)
    return (
        sanitize_step_target(target, secrets),
        sanitize_step_value(target, value, secrets, action),
        sanitize_step_error(error, secrets),
    )


def sanitize_process_output(
    text: str | None, secrets: Iterable[str] = (), limit: int | None = None
) -> str | None:
    """pytest's captured stdout/stderr (tracebacks, assertion output, prints):
    paths reduced, driver stack removed, secrets and known/typed values masked."""
    return sanitize_traceback(text, limit=limit, secrets=secrets)


def sanitize_identifier(text: str | None, secrets: Iterable[str] = ()) -> str | None:
    """Test ids / names (pytest parametrize ids can embed data values)."""
    if not text:
        return text
    return mask_known_secrets(redact_secrets(text), secrets)

"""Deterministic, authority-safe resource canonicalization.

The functions in this module only derive opaque identities.  They do not open
resources, perform network requests, or attach any live execution path.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path, PurePosixPath
import re
import stat
import unicodedata
from urllib.parse import quote, unquote_to_bytes, urlsplit, urlunsplit

# HTTPX is a directly pinned runtime dependency and requires ``idna``.  Reuse
# that guaranteed IDNA 2008 implementation so authority identities match the
# actual HTTP executor without adding another project dependency or lock entry.
import idna

from magi_agent.ops.safety import canonical_digest, contains_secret_marker, is_secret_key


class CanonicalizationError(ValueError):
    """Raised when a resource cannot be given one unambiguous safe identity."""


_WORKSPACE_REF_RE = re.compile(r"\Aworkspace://(sha256:[0-9a-f]{64})/(.*)\Z")
_VALID_PERCENT_RE = re.compile(r"%(?:[0-9A-Fa-f]{2})")
_INVALID_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ASCII_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_PATH_RAW_SAFE = _ASCII_UNRESERVED | frozenset("!$&'()*+,;=:@")
_QUERY_RAW_SAFE = _ASCII_UNRESERVED | frozenset("!$'()*+,;=:@/?")
_HOST_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_INET_ATON_NUMERIC_HOST_RE = re.compile(
    r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)(?:\.(?:0[xX][0-9A-Fa-f]+|[0-9]+)){0,3}\Z"
)
# These deliberately stay below common filesystem limits.  Canonicalization
# walks existing ancestors more than once to detect swaps, so bounding the
# authority-relative depth and the complete candidate before any filesystem
# operation also bounds adversarial CPU and syscall work.
_MAX_WORKSPACE_RELATIVE_DEPTH = 32
_MAX_WORKSPACE_SEGMENT_BYTES = 255
_MAX_WORKSPACE_CANDIDATE_BYTES = 1_023
_MAX_WORKSPACE_ENCODED_SUFFIX_CHARS = min(
    _MAX_WORKSPACE_RELATIVE_DEPTH * (_MAX_WORKSPACE_SEGMENT_BYTES * 3)
    + _MAX_WORKSPACE_RELATIVE_DEPTH
    - 1,
    _MAX_WORKSPACE_CANDIDATE_BYTES * 3,
)
_MAX_WORKSPACE_REF_CHARS = len("workspace://sha256:") + 64 + 1 + _MAX_WORKSPACE_ENCODED_SUFFIX_CHARS
# These bounds are checked before component canonicalization.  Besides limiting
# memory, they keep percent decoding, query sorting, secret scanning, and path
# normalization deterministic under adversarial input.
_MAX_HTTP_URL_CHARS = 8_192
_MAX_HTTP_URL_BYTES = 8_192
_MAX_HTTP_AUTHORITY_BYTES = 260
_MAX_HTTP_PATH_BYTES = 4_096
_MAX_HTTP_QUERY_BYTES = 4_096
_MAX_HTTP_PATH_SEGMENTS = 128
_MAX_HTTP_QUERY_ITEMS = 128
_MAX_HTTP_COMPONENT_BYTES = 1_024
_MAX_HTTP_PORT_DIGITS = 5


def canonical_file_resource(
    root: str | os.PathLike[str],
    path: str | os.PathLike[str],
) -> str:
    """Return an opaque workspace identity for *path* under *root*.

    Existing symlinks are followed, including a final symlink.  A path that is
    being prepared for creation is resolved through its nearest existing
    directory ancestor while retaining the not-yet-created suffix.  Every
    unresolved suffix segment must already be NFC and casefold-stable so one
    creation authority cannot later resolve through a filesystem spelling alias.
    Authority paths are limited to 32 relative segments, 255 UTF-8 bytes per
    segment, and 1,023 UTF-8 bytes for the complete candidate path.
    """

    root_text = _path_text(root, field_name="root")
    path_text = _path_text(path, field_name="path")
    _validate_forward_workspace_path_budget(root_text, path_text)
    root_path = _resolved_workspace_root(root_text)
    try:
        root_identity_before = _stat_identity(root_path)
    except OSError as exc:
        raise CanonicalizationError("workspace root became unavailable") from exc
    candidate = Path(path_text)
    if not candidate.is_absolute():
        candidate = root_path / candidate

    traversal_first = _validate_existing_prefix_traversal(root_path, candidate)
    first = _resolve_candidate(candidate)
    traversal_second = _validate_existing_prefix_traversal(root_path, candidate)
    second = _resolve_candidate(candidate)
    if first != second or traversal_first != traversal_second:
        raise CanonicalizationError("path identity changed during canonicalization")
    resolved_path, ancestor_identity = first

    try:
        root_identity_after = _stat_identity(root_path)
    except OSError as exc:
        raise CanonicalizationError("workspace root became unavailable") from exc
    if root_identity_before != root_identity_after:
        raise CanonicalizationError("workspace root identity changed during canonicalization")
    if root_identity_after[2] != "directory":
        raise CanonicalizationError("workspace root must be a directory")

    # The root is resolved before either candidate pass.  Re-resolving detects
    # replacement of a symlinked root or another transient identity change.
    try:
        if root_path.resolve(strict=True) != root_path:
            raise CanonicalizationError("workspace root identity changed")
    except (OSError, RuntimeError) as exc:
        raise CanonicalizationError("workspace root became unavailable") from exc

    _ = ancestor_identity
    relative = _relative_to_workspace(root_path, resolved_path)
    _validate_resolved_workspace_path_budget(root_path, relative.parts)
    digest = _workspace_digest(root_path, identity=root_identity_after)
    encoded = "/".join(_encode_workspace_segment(segment) for segment in relative.parts)
    return f"workspace://{digest}/{encoded}"


def workspace_relative_path(
    root: str | os.PathLike[str],
    ref: str,
) -> PurePosixPath:
    """Validate *ref* for *root* and return its safe relative POSIX path.

    The inverse enforces the same documented 32-segment, 255-byte segment, and
    1,023-byte complete-candidate budgets before resolving the workspace root.
    """

    root_text = _path_text(root, field_name="root")
    match, decoded_segments = _parse_workspace_ref_before_filesystem(root_text, ref)
    root_path = _resolved_workspace_root(root_text)

    try:
        root_identity = _stat_identity(root_path)
    except (CanonicalizationError, OSError) as exc:
        raise CanonicalizationError("workspace root identity changed") from exc
    if root_identity[2] != "directory":
        raise CanonicalizationError("workspace root identity changed")
    expected_digest = _workspace_digest(root_path, identity=root_identity)
    if match.group(1) != expected_digest:
        raise CanonicalizationError("workspace resource belongs to a different root")

    if not decoded_segments:
        _revalidate_workspace_root(
            root_path,
            expected_identity=root_identity,
            expected_digest=expected_digest,
        )
        return PurePosixPath(".")
    relative = PurePosixPath(*decoded_segments)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise CanonicalizationError("workspace resource is not a safe relative path")
    try:
        canonical_ref = canonical_file_resource(root_path, Path(*relative.parts))
    except CanonicalizationError as exc:
        raise CanonicalizationError(
            "workspace resource does not match a canonical workspace identity"
        ) from exc
    if canonical_ref != ref:
        raise CanonicalizationError(
            "workspace resource does not match a canonical workspace identity"
        )
    return relative


def canonical_http_resource(url: str) -> str:
    """Return one deterministic identity for an absolute HTTP(S) URL."""

    if type(url) is not str or not url:
        raise CanonicalizationError("URL must be non-empty text")
    if len(url) > _MAX_HTTP_URL_CHARS:
        raise _http_url_budget_error()
    if _has_surrogate(url):
        raise CanonicalizationError("URL must contain valid Unicode")
    if _http_utf8_bytes(url) > _MAX_HTTP_URL_BYTES:
        raise _http_url_budget_error()
    if any(character.isspace() for character in url):
        raise CanonicalizationError("URL must not contain whitespace")
    if _has_control(url):
        raise CanonicalizationError("URL must not contain control characters")
    if "#" in url:
        raise CanonicalizationError("URL fragments are not authority resources")
    if "\\" in url:
        raise CanonicalizationError("URL must not contain backslashes")
    if _INVALID_PERCENT_RE.search(url):
        raise CanonicalizationError("URL has malformed percent encoding")

    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise CanonicalizationError("URL authority is malformed") from exc
    _validate_http_component_budgets(
        netloc=parsed.netloc,
        path=parsed.path,
        query=parsed.query,
    )

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        raise CanonicalizationError("URL must be absolute HTTP or HTTPS")
    if parsed.fragment or "@" in parsed.netloc:
        raise CanonicalizationError("URL userinfo and fragments are forbidden")

    authority = _canonical_http_authority(parsed.netloc, scheme=scheme)
    path = _canonical_http_path(parsed.path)
    query = _canonical_http_query(parsed.query)
    return urlunsplit((scheme, authority, path, query, ""))


def _resolved_workspace_root(root: str | os.PathLike[str]) -> Path:
    root_text = _path_text(root, field_name="root")
    try:
        resolved = Path(root_text).resolve(strict=True)
        if not resolved.is_dir():
            raise CanonicalizationError("workspace root must be an existing directory")
        resolved = _canonical_stored_path(resolved)
    except CanonicalizationError:
        raise
    except (OSError, RuntimeError) as exc:
        raise CanonicalizationError(
            "workspace root must be an existing resolved directory"
        ) from exc
    return resolved


def _http_url_budget_error() -> CanonicalizationError:
    return CanonicalizationError("URL exceeds the canonicalization budget")


def _http_utf8_bytes(value: str) -> int:
    try:
        return len(value.encode("utf-8", errors="strict"))
    except UnicodeError as exc:
        raise CanonicalizationError("URL must contain valid Unicode") from exc


def _validate_http_component_budgets(*, netloc: str, path: str, query: str) -> None:
    if (
        _http_utf8_bytes(netloc) > _MAX_HTTP_AUTHORITY_BYTES
        or _http_utf8_bytes(path) > _MAX_HTTP_PATH_BYTES
        or _http_utf8_bytes(query) > _MAX_HTTP_QUERY_BYTES
        or path.count("/") > _MAX_HTTP_PATH_SEGMENTS
        or (query and query.count("&") + 1 > _MAX_HTTP_QUERY_ITEMS)
    ):
        raise _http_url_budget_error()

    if any(_http_utf8_bytes(segment) > _MAX_HTTP_COMPONENT_BYTES for segment in path.split("/")):
        raise _http_url_budget_error()
    for raw_item in query.split("&") if query else ():
        raw_key, _separator, raw_value = raw_item.partition("=")
        if (
            _http_utf8_bytes(raw_key) > _MAX_HTTP_COMPONENT_BYTES
            or _http_utf8_bytes(raw_value) > _MAX_HTTP_COMPONENT_BYTES
        ):
            raise _http_url_budget_error()


def _workspace_path_budget_error() -> CanonicalizationError:
    return CanonicalizationError("workspace path exceeds the canonicalization budget")


def _encoded_path_bytes(value: str) -> int:
    return len(value.encode("utf-8", errors="strict"))


def _validate_relative_workspace_segments(segments: tuple[str, ...]) -> None:
    if len(segments) > _MAX_WORKSPACE_RELATIVE_DEPTH:
        raise _workspace_path_budget_error()
    if any(_encoded_path_bytes(segment) > _MAX_WORKSPACE_SEGMENT_BYTES for segment in segments):
        raise _workspace_path_budget_error()


def _validate_candidate_byte_budget(candidate: str) -> None:
    if _encoded_path_bytes(candidate) > _MAX_WORKSPACE_CANDIDATE_BYTES:
        raise _workspace_path_budget_error()


def _lexical_absolute_workspace_root(root_text: str) -> Path:
    try:
        return Path(os.path.abspath(root_text))
    except OSError as exc:
        raise CanonicalizationError("workspace root could not be bounded safely") from exc


def _validate_forward_workspace_path_budget(root_text: str, path_text: str) -> None:
    raw_root = _lexical_absolute_workspace_root(root_text)
    raw_path = Path(path_text)
    if raw_path.is_absolute():
        candidate = raw_path
        try:
            relative_parts = raw_path.relative_to(raw_root).parts
        except ValueError:
            # A physical alias of the same root can have a different lexical
            # prefix (and even a different prefix depth).  The total byte cap
            # applies here before I/O; exact relative depth is rechecked after
            # resolving and binding that alias to the root inode.
            relative_parts = raw_path.parts[1:]
    else:
        candidate = raw_root / raw_path
        relative_parts = raw_path.parts
    _validate_relative_workspace_segments(tuple(relative_parts))
    _validate_candidate_byte_budget(os.fspath(candidate))


def _validate_resolved_workspace_path_budget(root: Path, parts: tuple[str, ...]) -> None:
    _validate_relative_workspace_segments(parts)
    _validate_candidate_byte_budget(os.fspath(root.joinpath(*parts)))


def _parse_workspace_ref_before_filesystem(
    root_text: str,
    ref: str,
) -> tuple[re.Match[str], tuple[str, ...]]:
    if type(ref) is not str:
        raise CanonicalizationError("workspace resource must be canonical text")
    if len(ref) > _MAX_WORKSPACE_REF_CHARS:
        raise _workspace_path_budget_error()
    if _has_surrogate(ref) or _has_control(ref):
        raise CanonicalizationError("workspace resource must be canonical text")
    match = _WORKSPACE_REF_RE.fullmatch(ref)
    if match is None:
        raise CanonicalizationError("workspace resource must use the canonical form")

    suffix = match.group(2)
    if len(suffix) > _MAX_WORKSPACE_ENCODED_SUFFIX_CHARS:
        raise _workspace_path_budget_error()
    if suffix == "":
        _validate_candidate_byte_budget(os.fspath(_lexical_absolute_workspace_root(root_text)))
        return match, ()
    if suffix.startswith("/") or suffix.endswith("/") or "//" in suffix:
        raise CanonicalizationError("workspace resource must use canonical path segments")

    encoded_segments = suffix.split("/")
    if len(encoded_segments) > _MAX_WORKSPACE_RELATIVE_DEPTH:
        raise _workspace_path_budget_error()
    decoded_segments: list[str] = []
    for encoded_segment in encoded_segments:
        if len(encoded_segment) > _MAX_WORKSPACE_SEGMENT_BYTES * 3:
            raise _workspace_path_budget_error()
        if _INVALID_PERCENT_RE.search(encoded_segment):
            raise CanonicalizationError("workspace resource has malformed percent encoding")
        try:
            segment = unquote_to_bytes(encoded_segment).decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise CanonicalizationError("workspace resource must contain valid UTF-8") from exc
        if (
            not segment
            or segment in {".", ".."}
            or "/" in segment
            or _has_control(segment)
            or _has_surrogate(segment)
            or quote(segment, safe="") != encoded_segment
        ):
            raise CanonicalizationError("workspace resource has a noncanonical path segment")
        decoded_segments.append(segment)

    decoded = tuple(decoded_segments)
    _validate_relative_workspace_segments(decoded)
    lexical_root = _lexical_absolute_workspace_root(root_text)
    _validate_candidate_byte_budget(os.fspath(lexical_root.joinpath(*decoded)))
    return match, decoded


def _path_text(value: str | os.PathLike[str], *, field_name: str) -> str:
    try:
        text = os.fspath(value)
    except TypeError as exc:
        raise CanonicalizationError(f"{field_name} must be path text") from exc
    except OSError as exc:
        raise CanonicalizationError(f"{field_name} path text is unavailable") from exc
    if type(text) is not str:
        raise CanonicalizationError(f"{field_name} must be valid path text")
    if len(text) > _MAX_WORKSPACE_CANDIDATE_BYTES:
        raise _workspace_path_budget_error()
    if "\x00" in text or _has_surrogate(text) or _has_control(text):
        raise CanonicalizationError(f"{field_name} must be valid path text")
    return text


def _resolve_candidate(path: Path) -> tuple[Path, tuple[int, int, str]]:
    probe = path
    suffix: list[str] = []
    while True:
        try:
            identity_before = _stat_identity(probe)
        except FileNotFoundError as exc:
            try:
                is_broken_symlink = probe.is_symlink()
            except OSError as link_exc:
                raise CanonicalizationError(
                    "path contains an invalid symlink or ancestor"
                ) from link_exc
            if is_broken_symlink:
                raise CanonicalizationError("path contains a broken symlink")
            parent = probe.parent
            if parent == probe:
                raise CanonicalizationError("path has no stable existing ancestor") from exc
            suffix.append(probe.name)
            probe = parent
            continue
        except RuntimeError as exc:
            raise CanonicalizationError("path contains a symlink loop") from exc
        except NotADirectoryError as exc:
            raise CanonicalizationError(
                "nearest existing path ancestor must be a directory"
            ) from exc
        except OSError as exc:
            raise CanonicalizationError("path contains an invalid symlink or ancestor") from exc
        break

    if suffix and identity_before[2] != "directory":
        raise CanonicalizationError("nearest existing path ancestor must be a directory")

    try:
        resolved_ancestor = probe.resolve(strict=True)
        identity_after = _stat_identity(probe)
    except (OSError, RuntimeError) as exc:
        raise CanonicalizationError("path contains a broken or looping symlink") from exc
    if identity_before != identity_after:
        raise CanonicalizationError("path ancestor changed during canonicalization")
    resolved_ancestor = _canonical_stored_path(resolved_ancestor)
    try:
        resolved_ancestor_identity = _stat_identity(resolved_ancestor)
    except OSError as exc:
        raise CanonicalizationError("path ancestor changed during canonicalization") from exc
    if resolved_ancestor_identity != identity_after:
        raise CanonicalizationError("path ancestor changed during canonicalization")

    retained = tuple(reversed(suffix))
    _require_deterministic_creation_suffix(retained)
    try:
        resolved = resolved_ancestor.joinpath(*retained).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise CanonicalizationError("path could not be resolved safely") from exc
    _reject_observable_hard_link(resolved)
    return resolved, identity_after


def _require_deterministic_creation_suffix(segments: tuple[str, ...]) -> None:
    for segment in segments:
        if unicodedata.normalize("NFC", segment) != segment or segment.casefold() != segment:
            raise CanonicalizationError(
                "unresolved path segment must use a deterministic creation spelling"
            )


def _validate_existing_prefix_traversal(
    root: Path,
    candidate: Path,
) -> tuple[tuple[Path, tuple[int, int, str]], ...]:
    remaining_parts = _parts_after_physical_root(root, candidate)
    observed: list[tuple[Path, tuple[int, int, str]]] = []
    current = root
    current_kind: str | None = "directory"
    for part in remaining_parts:
        if part == "..":
            if current_kind == "other":
                raise CanonicalizationError("nearest existing path ancestor must be a directory")
            if current == root:
                raise CanonicalizationError("path traversal moved above the workspace root")
            current = current.parent
            try:
                identity = _stat_identity(current)
                resolved = _canonical_stored_path(current.resolve(strict=True))
            except FileNotFoundError:
                current_kind = None
                continue
            except NotADirectoryError as exc:
                raise CanonicalizationError(
                    "nearest existing path ancestor must be a directory"
                ) from exc
            except (OSError, RuntimeError) as exc:
                raise CanonicalizationError("path traversal could not be verified") from exc
            try:
                resolved_identity = _stat_identity(resolved)
            except OSError as exc:
                raise CanonicalizationError(
                    "path identity changed during canonicalization"
                ) from exc
            if resolved_identity != identity:
                raise CanonicalizationError("path identity changed during canonicalization")
            _require_workspace_containment(root, resolved)
            current = resolved
            current_kind = identity[2]
            observed.append((resolved, identity))
            continue

        if current_kind == "other":
            raise CanonicalizationError("nearest existing path ancestor must be a directory")

        proposed = current / part
        try:
            resolved = proposed.resolve(strict=True)
            identity = _stat_identity(proposed)
        except FileNotFoundError as exc:
            try:
                is_broken_symlink = proposed.is_symlink()
            except OSError as link_exc:
                raise CanonicalizationError("path traversal could not be verified") from link_exc
            if is_broken_symlink:
                raise CanonicalizationError("path contains a broken symlink") from exc
            current = proposed
            current_kind = None
            continue
        except NotADirectoryError as exc:
            raise CanonicalizationError(
                "nearest existing path ancestor must be a directory"
            ) from exc
        except RuntimeError as exc:
            raise CanonicalizationError("path contains a symlink loop") from exc
        except OSError as exc:
            raise CanonicalizationError("path traversal could not be verified") from exc

        resolved = _canonical_stored_path(resolved)
        try:
            resolved_identity = _stat_identity(resolved)
        except OSError as exc:
            raise CanonicalizationError(
                "path identity changed during canonicalization"
            ) from exc
        if resolved_identity != identity:
            raise CanonicalizationError("path identity changed during canonicalization")
        _require_workspace_containment(root, resolved)
        current = resolved
        current_kind = identity[2]
        observed.append((resolved, identity))
    return tuple(observed)


def _parts_after_physical_root(root: Path, candidate: Path) -> tuple[str, ...]:
    if not candidate.is_absolute():
        raise CanonicalizationError("path must be absolute after workspace binding")

    candidate_parts = candidate.parts
    try:
        root_identity = _stat_identity(root)
    except OSError as exc:
        raise CanonicalizationError("workspace root became unavailable") from exc
    current = Path(candidate.anchor)
    try:
        if _stat_identity(current)[:2] == root_identity[:2]:
            return tuple(candidate_parts[1:])
    except OSError:
        pass

    for index, part in enumerate(candidate_parts[1:], start=1):
        current = current / part
        try:
            current_identity = _stat_identity(current)
        except (OSError, RuntimeError):
            continue
        if current_identity[:2] == root_identity[:2]:
            return tuple(candidate_parts[index + 1 :])
    raise CanonicalizationError("absolute path has no physical workspace root prefix")


def _require_workspace_containment(root: Path, path: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CanonicalizationError("path traverses outside the workspace") from exc


def _stat_identity(path: Path) -> tuple[int, int, str]:
    return _identity_from_stat(path.stat())


def _lstat_identity(path: Path) -> tuple[int, int, str]:
    return _identity_from_stat(path.lstat())


def _identity_from_stat(result: os.stat_result) -> tuple[int, int, str]:
    if stat.S_ISDIR(result.st_mode):
        kind = "directory"
    elif stat.S_ISREG(result.st_mode):
        kind = "other"
    else:
        raise CanonicalizationError("existing path must be a regular file or directory")
    return result.st_dev, result.st_ino, kind


def _link_count(path: Path) -> int:
    return path.stat().st_nlink


def _reject_observable_hard_link(path: Path) -> None:
    try:
        identity_before = _stat_identity(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise CanonicalizationError("final path identity could not be verified") from exc
    if identity_before[2] == "directory":
        return

    try:
        link_count_before = _link_count(path)
        identity_after = _stat_identity(path)
        link_count_after = _link_count(path)
    except OSError as exc:
        raise CanonicalizationError("final path identity could not be verified") from exc
    if identity_before != identity_after or link_count_before != link_count_after:
        raise CanonicalizationError("final path identity changed during canonicalization")
    if link_count_after > 1:
        raise CanonicalizationError("existing hard link targets require typed authority")


def _canonical_stored_path(path: Path) -> Path:
    """Recover the filesystem's stored spelling for an existing resolved path."""

    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CanonicalizationError("existing path identity could not be resolved") from exc
    if not resolved.is_absolute():
        raise CanonicalizationError("existing path identity must be absolute")

    current = Path(resolved.anchor)
    for component in resolved.parts[1:]:
        requested = current / component
        try:
            requested_identity = _stat_identity(requested)
            stored_component: str | None = None
            matches: list[str] = []
            alias_candidates: list[os.DirEntry[str]] = []
            alias_key = _filesystem_alias_key(component)
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.name == component:
                        exact_identity = _identity_from_stat(entry.stat(follow_symlinks=False))
                        if exact_identity != requested_identity:
                            raise CanonicalizationError(
                                "stored path identity changed during canonicalization"
                            )
                        stored_component = component
                        break
                    if _filesystem_alias_key(entry.name) == alias_key:
                        alias_candidates.append(entry)
                if stored_component is None:
                    for entry in alias_candidates:
                        try:
                            entry_stat = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if (
                            entry_stat.st_dev == requested_identity[0]
                            and entry_stat.st_ino == requested_identity[1]
                        ):
                            matches.append(entry.name)
        except OSError as exc:
            raise CanonicalizationError("stored path spelling could not be established") from exc

        if stored_component is None:
            if len(matches) == 1:
                stored_component = matches[0]
            else:
                raise CanonicalizationError("stored path spelling is ambiguous")

        current = current / stored_component
        try:
            if _lstat_identity(current) != requested_identity:
                raise CanonicalizationError("stored path identity changed during canonicalization")
        except OSError as exc:
            raise CanonicalizationError(
                "stored path identity changed during canonicalization"
            ) from exc
    return current


def _filesystem_alias_key(name: str) -> str:
    normalized = unicodedata.normalize("NFC", name)
    return unicodedata.normalize("NFC", normalized.casefold())


def _workspace_digest(
    root: Path,
    *,
    identity: tuple[int, int, str] | None = None,
) -> str:
    root_identity = _stat_identity(root) if identity is None else identity
    return canonical_digest(
        {
            "realPath": str(root),
            "stDev": root_identity[0],
            "stIno": root_identity[1],
        }
    )


def _revalidate_workspace_root(
    root: Path,
    *,
    expected_identity: tuple[int, int, str],
    expected_digest: str,
) -> None:
    try:
        identity_before = _lstat_identity(root)
        stored_root = _canonical_stored_path(root)
        stored_identity = _lstat_identity(stored_root)
        current_digest = _workspace_digest(stored_root, identity=stored_identity)
        identity_after = _lstat_identity(root)
    except (CanonicalizationError, OSError, RuntimeError) as exc:
        raise CanonicalizationError("workspace root identity changed before return") from exc
    if (
        stored_root != root
        or expected_identity[2] != "directory"
        or identity_before != expected_identity
        or stored_identity != expected_identity
        or identity_after != expected_identity
        or current_digest != expected_digest
    ):
        raise CanonicalizationError("workspace root identity changed before return")


def _relative_to_workspace(root: Path, path: Path) -> Path:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise CanonicalizationError("path resolves outside the workspace") from exc
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise CanonicalizationError("path does not have canonical workspace components")
    return relative


def _encode_workspace_segment(segment: str) -> str:
    if not segment or segment in {".", ".."} or _has_control(segment) or _has_surrogate(segment):
        raise CanonicalizationError("path contains a noncanonical segment")
    return quote(segment, safe="")


def _canonical_http_authority(netloc: str, *, scheme: str) -> str:
    if "%" in netloc:
        raise CanonicalizationError("URL host must not use percent encoding or zone identifiers")

    bracketed = netloc.startswith("[")
    port_text: str | None = None
    if bracketed:
        closing = netloc.find("]")
        if closing < 0:
            raise CanonicalizationError("URL IPv6 host is malformed")
        raw_host = netloc[1:closing]
        remainder = netloc[closing + 1 :]
        if remainder:
            if not remainder.startswith(":"):
                raise CanonicalizationError("URL authority is malformed")
            port_text = remainder[1:]
    else:
        if netloc.count(":") > 1:
            raise CanonicalizationError("URL IPv6 host must use brackets")
        if ":" in netloc:
            raw_host, port_text = netloc.rsplit(":", 1)
        else:
            raw_host = netloc

    if not raw_host:
        raise CanonicalizationError("URL host is required")
    if port_text is not None:
        if not port_text or not port_text.isascii() or not port_text.isdecimal():
            raise CanonicalizationError("URL port is malformed")
        if len(port_text) > _MAX_HTTP_PORT_DIGITS:
            raise _http_url_budget_error()
        port = int(port_text)
        if port < 1 or port > 65535:
            raise CanonicalizationError("URL port is outside the valid range")
    else:
        port = None

    host = _canonical_host(raw_host, bracketed=bracketed)
    default_port = 80 if scheme == "http" else 443
    rendered_port = "" if port is None or port == default_port else f":{port}"
    if bracketed:
        return f"[{host}]{rendered_port}"
    return f"{host}{rendered_port}"


def _canonical_host(raw_host: str, *, bracketed: bool) -> str:
    if bracketed:
        try:
            return ipaddress.IPv6Address(raw_host).compressed.lower()
        except ValueError as exc:
            raise CanonicalizationError("URL IPv6 host is malformed") from exc

    if raw_host.endswith("."):
        raw_host = raw_host[:-1]
    if not raw_host or ".." in raw_host:
        raise CanonicalizationError("URL host is malformed")
    if not raw_host.isascii():
        raise CanonicalizationError("URL host must use ASCII DNS or IDNA A-label text")

    if re.fullmatch(r"[0-9.]+", raw_host) or _INET_ATON_NUMERIC_HOST_RE.fullmatch(raw_host):
        try:
            return str(ipaddress.IPv4Address(raw_host))
        except ValueError as exc:
            raise CanonicalizationError("URL IPv4 host is malformed") from exc

    ascii_host = raw_host.lower()
    if len(ascii_host) > 253:
        raise CanonicalizationError("URL host is too long")
    labels = ascii_host.split(".")
    if any(_HOST_LABEL_RE.fullmatch(label) is None for label in labels):
        raise CanonicalizationError("URL host has an invalid label")
    for label in labels:
        if label.startswith("xn--"):
            _validate_idna_alabel(label)
    return ascii_host


def _validate_idna_alabel(label: str) -> None:
    """Validate an A-label with the same IDNA 2008 profile used by HTTPX."""

    try:
        decoded = idna.ulabel(label)
        encoded = idna.alabel(decoded).decode("ascii")
    except (idna.IDNAError, UnicodeError) as exc:
        raise CanonicalizationError("URL host is not valid IDNA") from exc
    if encoded != label:
        raise CanonicalizationError("URL host is not valid IDNA")


def _canonical_http_path(raw_path: str) -> str:
    if not raw_path:
        return "/"
    if not raw_path.startswith("/"):
        raise CanonicalizationError("URL path must be absolute")
    canonical_segments = [
        _canonical_url_component(segment, raw_safe=_PATH_RAW_SAFE)
        for segment in raw_path.split("/")
    ]
    return _remove_dot_segments("/".join(canonical_segments)) or "/"


def _canonical_http_query(raw_query: str) -> str:
    if not raw_query:
        return ""
    items: list[tuple[str, str]] = []
    for raw_item in raw_query.split("&"):
        raw_key, separator, raw_value = raw_item.partition("=")
        key = _canonical_url_component(raw_key, raw_safe=_QUERY_RAW_SAFE)
        if not key:
            raise CanonicalizationError("URL query keys must not be empty")
        if separator:
            value = _canonical_url_component(raw_value, raw_safe=_QUERY_RAW_SAFE)
            item = f"{key}={value}"
        else:
            value = ""
            item = key
        decoded_key = _decoded_url_component_text(key)
        decoded_value = _decoded_url_component_text(value)
        normalized_key = decoded_key.replace("-", "_").lower()
        if (
            is_secret_key(decoded_key, include_public_credential_keys=True)
            or normalized_key.replace("_", "") in {"key", "accesskey"}
            or contains_secret_marker(f"{decoded_key}={decoded_value}")
        ):
            raise CanonicalizationError("URL query key or value is classified as secret")
        items.append((key, item))
    items.sort(key=lambda pair: pair[0])
    return "&".join(item for _key, item in items)


def _canonical_url_component(raw: str, *, raw_safe: frozenset[str]) -> str:
    result: list[str] = []
    index = 0
    while index < len(raw):
        character = raw[index]
        if character == "%":
            octets = bytearray()
            while index < len(raw) and raw[index] == "%":
                if _VALID_PERCENT_RE.match(raw, index) is None:
                    raise CanonicalizationError("URL has malformed percent encoding")
                octets.append(int(raw[index + 1 : index + 3], 16))
                index += 3
            try:
                decoded = bytes(octets).decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise CanonicalizationError(
                    "URL percent encoding must contain valid UTF-8"
                ) from exc
            for decoded_character in decoded:
                if _has_control(decoded_character) or _has_surrogate(decoded_character):
                    raise CanonicalizationError(
                        "URL percent encoding contains forbidden characters"
                    )
                if decoded_character in _ASCII_UNRESERVED:
                    result.append(decoded_character)
                else:
                    result.append(_percent_encode(decoded_character))
            continue

        if ord(character) < 128 and character in raw_safe:
            result.append(character)
        else:
            result.append(_percent_encode(character))
        index += 1
    return "".join(result)


def _percent_encode(value: str) -> str:
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise CanonicalizationError("URL must contain valid Unicode") from exc
    return "".join(f"%{octet:02X}" for octet in encoded)


def _decoded_url_component_text(value: str) -> str:
    try:
        return unquote_to_bytes(value).decode("utf-8", errors="strict")
    except UnicodeError as exc:  # canonical components should make this unreachable
        raise CanonicalizationError("URL query must contain valid UTF-8") from exc


def _remove_dot_segments(path: str) -> str:
    segments = path.split("/")
    output: list[str] = []
    root_segments = 1 if path.startswith("/") else 0
    final_index = len(segments) - 1

    for index, segment in enumerate(segments):
        if segment == ".":
            if index == final_index:
                output.append("")
            continue
        if segment == "..":
            if len(output) > root_segments:
                output.pop()
            if index == final_index:
                output.append("")
            continue
        output.append(segment)

    return "/".join(output)


def _has_control(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _has_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)

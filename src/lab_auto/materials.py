"""Best-effort downloads of a task's external material; no GUAP cookies involved."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import tempfile
import time
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from lab_auto.models import MaterialFile
from lab_auto.paths import safe_name

MAX_BYTES = 100 * 1024 * 1024
TIMEOUT = 20
MAX_SECONDS = 60


@dataclass(slots=True)
class MaterialResult:
    file: MaterialFile | None
    warning: str | None = None


def _http_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("material requires an HTTP(S) URL without credentials")
    return url


class _HttpRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, _http_url(newurl))


def local_material_path(
    folder: Path,
    material: MaterialFile | None,
    *,
    relative_directory: str = "materials",
) -> Path | None:
    if material is None:
        return None
    relative = PurePosixPath(material.path)
    directory_relative = PurePosixPath(relative_directory)
    if (
        directory_relative.parts[:1] != ("materials",)
        or any(part in {"", ".", ".."} or safe_name(part) != part for part in directory_relative.parts)
        or relative.parent != directory_relative
        or "\\" in material.path
    ):
        return None
    if safe_name(relative.name) != relative.name:
        return None
    base_directory = folder / "materials"
    directory = folder.joinpath(*directory_relative.parts)
    resolved_base = base_directory.resolve()
    resolved_directory = directory.resolve()
    if (
        base_directory.is_symlink()
        or directory.is_symlink()
        or not (resolved_directory == resolved_base or resolved_base in resolved_directory.parents)
    ):
        return None
    path = folder / material.path
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        return None
    return path if path.is_file() else None


class MaterialDownloader:
    def __init__(self, opener=None) -> None:
        self.opener = opener or build_opener(_HttpRedirects())

    def _open(self, url: str, headers: dict[str, str] | None = None):
        return self.opener.open(Request(_http_url(url), headers=headers or {}), timeout=TIMEOUT)

    def sync(
        self,
        source_url: str | None,
        folder: Path,
        previous: MaterialFile | None = None,
        *,
        relative_directory: str = "materials",
    ) -> MaterialResult:
        cached = previous if local_material_path(
            folder, previous, relative_directory=relative_directory
        ) else None
        if not source_url:
            return MaterialResult(cached)
        try:
            return MaterialResult(self._download(
                source_url, folder, cached, relative_directory=relative_directory
            ))
        except (OSError, URLError, HTTPException, ValueError, TypeError, KeyError) as exc:
            # Do not include signed download URLs or server response bodies in logs.
            reason = f"HTTP {exc.code}" if isinstance(exc, HTTPError) else type(exc).__name__
            if isinstance(exc, ValueError):
                reason = str(exc) or reason
            return MaterialResult(cached, f"Additional material unavailable: {reason}")

    def _download(
        self,
        source_url: str,
        folder: Path,
        cached: MaterialFile | None,
        *,
        relative_directory: str,
    ) -> MaterialFile:
        _http_url(source_url)
        same_source = cached is not None and cached.source_url == source_url
        if urlparse(source_url).hostname in {"disk.yandex.ru", "disk.yandex.com", "yadi.sk"}:
            return self._yandex_file(
                source_url, folder, cached if same_source else None,
                relative_directory=relative_directory,
            )
        if same_source and not cached.etag and not cached.last_modified:
            # Without validators there is no reliable inexpensive change detection.
            return cached
        headers = {}
        if same_source:
            if cached.etag:
                headers["If-None-Match"] = cached.etag
            if cached.last_modified:
                headers["If-Modified-Since"] = cached.last_modified
        try:
            response = self._open(source_url, headers)
        except HTTPError as exc:
            if exc.code == 304 and same_source:
                exc.close()
                return cached
            raise
        with response:
            return self._save(
                response, source_url, folder, cached if same_source else None,
                relative_directory=relative_directory,
            )

    def _yandex_file(
        self,
        source_url: str,
        folder: Path,
        cached: MaterialFile | None,
        *,
        relative_directory: str,
    ) -> MaterialFile:
        # Public REST API: https://yandex.ru/dev/disk-api/doc/ru/reference/public
        api = "https://cloud-api.yandex.net/v1/disk/public/resources"
        query = "?" + urlencode({"public_key": source_url})

        def metadata(url):
            with self._open(url) as response:
                if response.status != 200:
                    raise ValueError(f"unexpected API HTTP {response.status}")
                data = response.read(1024 * 1024 + 1)
                if len(data) > 1024 * 1024:
                    raise ValueError("material metadata is too large")
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise ValueError("invalid material metadata")
                return result

        info = metadata(api + query)
        if info.get("type") != "file":
            raise ValueError("Yandex resource is not a single public file; kept external-only")
        if info.get("size", 0) > MAX_BYTES:
            raise ValueError("material exceeds 100 MiB limit")
        version = info.get("sha256") or info.get("md5") or info.get("modified")
        if cached and version and cached.remote_version == version:
            return cached
        href = metadata(api + "/download" + query)["href"]
        with self._open(href) as response:
            return self._save(response, source_url, folder, cached,
                              suggested_name=info.get("name"), remote_version=version,
                              relative_directory=relative_directory)

    def _save(self, response, source_url: str, folder: Path, cached: MaterialFile | None,
              *, suggested_name: str | None = None, remote_version: str | None = None,
              relative_directory: str = "materials") -> MaterialFile:
        if response.status != 200:
            raise ValueError(f"unexpected HTTP {response.status}")
        content_type = response.headers.get_content_type()
        if content_type in {"text/html", "application/xhtml+xml"}:
            raise ValueError("HTML landing page; kept external-only")
        filename = response.headers.get_filename() or suggested_name
        if not filename:
            candidate = unquote(urlparse(response.geturl()).path.rsplit("/", 1)[-1])
            filename = candidate if Path(candidate).suffix else None
        extension = mimetypes.guess_extension(content_type)
        if not filename and (not extension or content_type in {"text/plain", "application/json"}):
            raise ValueError("response does not identify a downloadable file")
        name = safe_name((filename or "material" + extension).replace("\\", "/").rsplit("/", 1)[-1])
        if Path(name).suffix.lower() in {".html", ".htm", ".xhtml"}:
            raise ValueError("HTML file; kept external-only")
        expected_size = response.headers.get("Content-Length")
        if expected_size and int(expected_size) > MAX_BYTES:
            raise ValueError("material exceeds 100 MiB limit")
        relative = PurePosixPath(relative_directory)
        if relative.parts[:1] != ("materials",) or any(
            part in {"", ".", ".."} or safe_name(part) != part for part in relative.parts
        ):
            raise ValueError("unsafe materials directory")
        base_directory = folder / "materials"
        directory = folder.joinpath(*relative.parts)
        resolved_base = base_directory.resolve()
        resolved_directory = directory.resolve()
        if (
            base_directory.is_symlink()
            or directory.is_symlink()
            or not (resolved_directory == resolved_base or resolved_base in resolved_directory.parents)
        ):
            raise ValueError("unsafe materials directory")
        directory.mkdir(parents=True, exist_ok=True)
        destination = local_material_path(
            folder, cached, relative_directory=relative_directory
        ) if cached else None
        if destination is None:
            destination = directory / name
            index = 2
            while destination.exists() or destination.is_symlink():
                destination = directory / f"{Path(name).stem}-{index}{Path(name).suffix}"
                index += 1
        if destination.resolve().parent != directory.resolve():
            raise ValueError("unsafe material filename")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                started = time.monotonic()
                size = 0
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    if size == 0:
                        encoding = "utf-16" if chunk[:2] in {b"\xff\xfe", b"\xfe\xff"} else "utf-8-sig"
                        prefix = chunk[:4096].decode(encoding, errors="ignore").lstrip().lower()
                        if re.search(r"<(?:!doctype\s+html|html|head|body|script)\b", prefix):
                            raise ValueError("HTML content disguised as a file")
                    size += len(chunk)
                    if size > MAX_BYTES or time.monotonic() - started > MAX_SECONDS:
                        raise ValueError("material download size/time limit exceeded")
                    stream.write(chunk)
                if expected_size and size != int(expected_size):
                    raise ValueError("incomplete material download")
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return MaterialFile(source_url, destination.relative_to(folder).as_posix(),
                            response.headers.get("ETag"), response.headers.get("Last-Modified"), remote_version)

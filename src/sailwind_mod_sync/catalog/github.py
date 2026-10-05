from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from urllib.parse import quote, urlparse

from sailwind_mod_sync.http_util import HttpClient, HttpError, ProgressFn
from sailwind_mod_sync.models import ReleaseAsset, RemoteRelease, parse_mod_version
from sailwind_mod_sync.paths import AppPaths

log = logging.getLogger(__name__)

GITHUB_DOWNLOAD_HELP_TITLE = "Could not download from GitHub"
_GITHUB_DOWNLOAD_PATH = re.compile(
    r"^(https://github\.com/[^/]+/[^/]+)/releases/download/([^/]+)/",
    re.I,
)

GITHUB_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", re.I)
GITLAB_RE = re.compile(r"^https?://gitlab\.com/(.+?)(?:\.git)?/?$", re.I)
_GIT_PATH_STOP = {
    "-",
    "issues",
    "pull",
    "pulls",
    "merge_requests",
    "releases",
    "tags",
    "tree",
    "blob",
    "commit",
    "commits",
    "wiki",
    "wikis",
    "actions",
    "projects",
    "security",
    "settings",
    "discussions",
    "packages",
    "network",
    "pulse",
    "graphs",
}


class RepoRef:
    def __init__(self, host: str, owner: str, repo: str, full_path: str) -> None:
        self.host = host.lower()
        self.owner = owner
        self.repo = repo
        self.full_path = full_path

    @property
    def is_github(self) -> bool:
        return "github.com" in self.host

    @property
    def is_gitlab(self) -> bool:
        return "gitlab.com" in self.host


def canonicalize_repo_url(url: str) -> str:
    text = (url or "").strip().strip("\"'")
    if not text:
        raise ValueError("Repository URL is empty")
    text = text.rstrip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    if not re.match(r"^https?://", text, re.I):
        if re.match(r"^(www\.)?(github\.com|gitlab\.com)/", text, re.I):
            text = f"https://{text}"
        elif "/" in text:
            text = f"https://github.com/{text}"
        else:
            raise ValueError(f"Unsupported repo URL: {url}")
    parsed = urlparse(text)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [item for item in parsed.path.split("/") if item]
    if parts and parts[-1].lower().endswith(".git"):
        parts[-1] = parts[-1][:-4]
    if host == "github.com":
        if len(parts) < 2:
            raise ValueError(f"Unsupported repo URL: {url}")
        return f"https://github.com/{parts[0]}/{parts[1]}"
    if host == "gitlab.com":
        cut: list[str] = []
        for part in parts:
            if part == "-" or part.lower() in _GIT_PATH_STOP:
                break
            cut.append(part)
        if len(cut) < 2:
            raise ValueError(f"Unsupported repo URL: {url}")
        return f"https://gitlab.com/{'/'.join(cut)}"
    raise ValueError(f"Unsupported repo host: {parsed.netloc or url}")


def parse_repo_url(url: str) -> RepoRef:
    text = canonicalize_repo_url(url)
    github = GITHUB_RE.match(text)
    if github:
        owner, repo = github.group(1), github.group(2)
        return RepoRef("github.com", owner, repo, f"{owner}/{repo}")
    gitlab = GITLAB_RE.match(text)
    if gitlab:
        path = gitlab.group(1).strip("/")
        parts = path.split("/")
        if len(parts) < 2:
            raise ValueError(f"Unsupported repo URL: {url}")
        return RepoRef("gitlab.com", parts[0], parts[-1], path)
    parsed = urlparse(text)
    raise ValueError(f"Unsupported repo host: {parsed.netloc or url}")


def repo_page_url(repo: str) -> str | None:
    if not (repo or "").strip():
        return None
    try:
        return canonicalize_repo_url(repo)
    except ValueError:
        return None


def repo_short_name(repo: str) -> str:
    """Return ``owner/repo`` for a GitHub or GitLab URL, the text itself for anything else, or "" when blank."""
    text = (repo or "").strip()
    if not text:
        return ""
    try:
        return parse_repo_url(text).full_path
    except ValueError:
        return text


def pick_zip_asset(assets: list[ReleaseAsset], guid: str, repo_name: str) -> ReleaseAsset:
    return pick_release_asset(assets, guid, repo_name)


def pick_release_asset(
    assets: list[ReleaseAsset],
    guid: str,
    repo_name: str,
    extra_hints: list[str] | tuple[str, ...] = (),
) -> ReleaseAsset:
    usable = [
        asset
        for asset in assets
        if asset.name.lower().endswith((".zip", ".dll")) and asset.download_url
    ]
    if not usable:
        names = ", ".join(asset.name for asset in assets if asset.name) or "none"
        raise FileNotFoundError(
            f"Release has no zip or dll download (files: {names}). "
            "This GitHub release only has source code, or no files at all."
        )
    guid_tail = guid.split(".")[-1].lower()
    repo_l = repo_name.lower()
    hints = [hint.lower() for hint in extra_hints if hint]

    def score(asset: ReleaseAsset) -> int:
        name = asset.name.lower()
        points = 0
        if name.endswith(".zip"):
            points += 20
        if guid_tail and guid_tail in name:
            points += 10
        if repo_l and repo_l in name:
            points += 5
        for hint in hints:
            compact = re.sub(r"[^a-z0-9]+", "", hint)
            compact_name = re.sub(r"[^a-z0-9]+", "", name)
            if hint and hint in name:
                points += 12
            elif compact and compact in compact_name:
                points += 12
        if "source" in name:
            points -= 20
        if name.endswith("-sources.zip") or name.endswith("_source.zip"):
            points -= 20
        return points

    return max(usable, key=score)


class GitHubDownloadError(RuntimeError):
    """Failed or truncated GitHub asset download, with manual-import instructions."""

    title = GITHUB_DOWNLOAD_HELP_TITLE

    @staticmethod
    def is_help_text(text: str) -> bool:
        return "Import Mod DLL/ZIP" in (text or "")


def github_release_page_url(download_url: str, release_url: str = "", repo_url: str = "") -> str:
    if (release_url or "").strip():
        return release_url.strip()
    match = _GITHUB_DOWNLOAD_PATH.match((download_url or "").strip())
    if match:
        return f"{match.group(1)}/releases/tag/{match.group(2)}"
    return (repo_url or "").strip()


def github_manual_download_help(
    *,
    asset_name: str,
    download_url: str = "",
    release_url: str = "",
    repo_url: str = "",
    reason: str = "",
) -> str:
    name = asset_name or "the mod file"
    page = github_release_page_url(download_url, release_url, repo_url)
    lines = [
        f"Could not finish downloading {name} from GitHub.",
        "",
        "Download it in your browser, then add it in Sailwind Mod Synchronizer:",
    ]
    if page:
        lines.append(f"1. Open the GitHub release page:\n   {page}")
    else:
        lines.append("1. Open the mod's GitHub Releases page.")
    lines.extend(
        [
            f"2. Download {name} (or the matching .zip / .dll).",
            "3. In this app, click Import Mod DLL/ZIP on the Pack tab",
            "   (or Download Management → Import mod file…) and choose that file.",
        ]
    )
    if reason:
        lines.extend(["", f"Details: {reason}"])
    return "\n".join(lines)


def download_release_asset(
    http: HttpClient,
    asset: ReleaseAsset,
    dest: Path,
    progress: ProgressFn | None = None,
    *,
    release_url: str = "",
    repo_url: str = "",
) -> None:
    """Download a release file without sending the API token to GitHub's CDN.

    Public ``browser_download_url`` links fail for large assets when Authorization
    is attached. With a token, use the GitHub assets API instead (private repos
    and files over ~50 MB).
    """
    url = asset.api_url if (http.token and asset.api_url) else asset.download_url
    if not url:
        raise FileNotFoundError(f"Release asset {asset.name!r} has no download URL")
    try:
        http.download(url, dest, progress=progress)
        _verify_downloaded_asset(dest, asset)
    except GitHubDownloadError:
        raise
    except Exception as exc:
        _remove_if_exists(dest)
        raise GitHubDownloadError(
            github_manual_download_help(
                asset_name=asset.name,
                download_url=asset.download_url,
                release_url=release_url,
                repo_url=repo_url,
                reason=str(exc).strip() or type(exc).__name__,
            )
        ) from exc


def _verify_downloaded_asset(dest: Path, asset: ReleaseAsset) -> None:
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise HttpError(f"Download of {asset.name or dest.name} was empty")
    expected = int(asset.size or 0)
    actual = dest.stat().st_size
    if expected > 0 and actual != expected:
        raise HttpError(
            f"Incomplete download of {asset.name or dest.name}: got {actual} of {expected} bytes"
        )


def _remove_if_exists(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Could not delete incomplete download %s", path)


def fetch_release(
    http: HttpClient,
    repo_url: str,
    tag: str | None = None,
    paths: AppPaths | None = None,
    progress: ProgressFn | None = None,
) -> RemoteRelease:
    ref = parse_repo_url(repo_url)
    log.info("Fetching %s release tag=%s", ref.full_path, tag or "latest")
    if progress:
        label = tag or "latest"
        progress(f"Checking {ref.full_path} ({label})…")
    if ref.is_github:
        return _github_release(http, ref, tag, paths)
    if ref.is_gitlab:
        return _gitlab_release(http, ref, tag)
    raise ValueError(f"Unsupported repo: {repo_url}")


def _github_release(
    http: HttpClient,
    ref: RepoRef,
    tag: str | None,
    paths: AppPaths | None,
) -> RemoteRelease:
    if tag:
        url = f"https://api.github.com/repos/{ref.full_path}/releases/tags/{tag}"
        etag_path = None
    else:
        url = f"https://api.github.com/repos/{ref.full_path}/releases/latest"
        etag_path = _etag_path(paths, ref) if paths else None
    etag = etag_path.read_text(encoding="utf-8").strip() if etag_path and etag_path.exists() else None
    cache_path = _release_cache_path(paths, ref) if paths and not tag else None
    try:
        data, new_etag, not_modified = http.get_json(url, extra_headers=_github_accept(), etag=etag)
    except HttpError as exc:
        if tag is None and exc.status_code == 404:
            log.info("GitHub /releases/latest 404 for %s; listing releases", ref.full_path)
            return _github_latest_from_list(http, ref, cache_path)
        raise
    if not_modified and cache_path and cache_path.exists():
        log.info("GitHub 304 cache hit for %s", ref.full_path)
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    elif data is None:
        raise HttpError(f"Empty GitHub release response for {ref.full_path}")
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
    if etag_path is not None and new_etag:
        etag_path.parent.mkdir(parents=True, exist_ok=True)
        etag_path.write_text(new_etag, encoding="utf-8")
    return _parse_github_payload(data)


def _github_latest_from_list(http: HttpClient, ref: RepoRef, cache_path: Path | None) -> RemoteRelease:
    url = f"https://api.github.com/repos/{ref.full_path}/releases"
    data, _, _ = http.get_json(url, extra_headers=_github_accept())
    if not isinstance(data, list) or not data:
        raise HttpError(f"No GitHub releases for {ref.full_path}")
    chosen = _pick_github_list_release(data)
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(chosen), encoding="utf-8")
    return _parse_github_payload(chosen)


def _pick_github_list_release(items: list) -> dict:
    usable = [item for item in items if isinstance(item, dict) and not item.get("draft")]
    if not usable:
        raise HttpError("No published GitHub releases")
    usable.sort(key=lambda item: str(item.get("published_at") or item.get("created_at") or ""), reverse=True)
    stable = [item for item in usable if not item.get("prerelease")]
    return (stable or usable)[0]


def _github_accept() -> dict[str, str]:
    return {"Accept": "application/vnd.github+json"}


def _parse_github_payload(data: dict) -> RemoteRelease:
    assets = [
        ReleaseAsset(
            name=str(item.get("name") or ""),
            download_url=str(item.get("browser_download_url") or ""),
            size=int(item.get("size") or 0),
            api_url=str(item.get("url") or ""),
        )
        for item in data.get("assets") or []
        if item.get("browser_download_url")
    ]
    return RemoteRelease(
        tag=str(data.get("tag_name") or ""),
        name=str(data.get("name") or data.get("tag_name") or ""),
        assets=assets,
        html_url=str(data.get("html_url") or ""),
        body=str(data.get("body") or ""),
    )


def _gitlab_release(http: HttpClient, ref: RepoRef, tag: str | None) -> RemoteRelease:
    project = quote(ref.full_path, safe="")
    if tag:
        url = f"https://gitlab.com/api/v4/projects/{project}/releases/{quote(tag, safe='')}"
        data, _, _ = http.get_json(url)
        if not isinstance(data, dict):
            raise HttpError("Unexpected GitLab release payload")
        return _parse_gitlab_payload(data)
    url = f"https://gitlab.com/api/v4/projects/{project}/releases"
    data, _, _ = http.get_json(url)
    if not isinstance(data, list) or not data:
        raise HttpError(f"No GitLab releases for {ref.full_path}")
    return _parse_gitlab_payload(data[0])


def _parse_gitlab_payload(data: dict) -> RemoteRelease:
    links = ((data.get("assets") or {}).get("links")) or []
    assets = [
        ReleaseAsset(
            name=str(item.get("name") or ""),
            download_url=str(item.get("direct_asset_url") or item.get("url") or ""),
        )
        for item in links
        if item.get("direct_asset_url") or item.get("url")
    ]
    return RemoteRelease(
        tag=str(data.get("tag_name") or ""),
        name=str(data.get("name") or data.get("tag_name") or ""),
        assets=assets,
        html_url=str(data.get("_links", {}).get("self") or ""),
    )


def release_version(release: RemoteRelease) -> str | None:
    return parse_mod_version(release.tag) or parse_mod_version(release.name)


def list_releases(
    http: HttpClient,
    repo_url: str,
    limit: int = 12,
    progress: ProgressFn | None = None,
) -> list[RemoteRelease]:
    ref = parse_repo_url(repo_url)
    if progress:
        progress(f"Listing releases for {ref.full_path}…")
    if ref.is_github:
        url = f"https://api.github.com/repos/{ref.full_path}/releases?per_page={max(1, limit)}"
        data, _, _ = http.get_json(url, extra_headers=_github_accept())
        if not isinstance(data, list):
            raise HttpError(f"Unexpected GitHub releases payload for {ref.full_path}")
        items = [item for item in data if isinstance(item, dict) and not item.get("draft")]
        return [_parse_github_payload(item) for item in items[:limit]]
    if ref.is_gitlab:
        project = quote(ref.full_path, safe="")
        url = f"https://gitlab.com/api/v4/projects/{project}/releases"
        data, _, _ = http.get_json(url)
        if not isinstance(data, list):
            raise HttpError(f"Unexpected GitLab releases payload for {ref.full_path}")
        return [_parse_gitlab_payload(item) for item in data[:limit] if isinstance(item, dict)]
    raise ValueError(f"Unsupported repo: {repo_url}")


def fetch_readme(http: HttpClient, repo_url: str, progress: ProgressFn | None = None) -> str:
    ref = parse_repo_url(repo_url)
    if progress:
        progress(f"Fetching README for {ref.full_path}…")
    if ref.is_github:
        url = f"https://api.github.com/repos/{ref.full_path}/readme"
        raw = http.get_bytes(url, extra_headers={"Accept": "application/vnd.github.raw"})
        return _decode_readme(raw)
    if ref.is_gitlab:
        project = quote(ref.full_path, safe="")
        last_error: Exception | None = None
        for name in ("README.md", "readme.md", "README", "README.rst"):
            url = (
                f"https://gitlab.com/api/v4/projects/{project}/repository/files/"
                f"{quote(name, safe='.')}/raw"
            )
            try:
                return _decode_readme(http.get_bytes(url))
            except HttpError as exc:
                last_error = exc
        raise last_error or HttpError(f"No README found for {ref.full_path}")
    raise ValueError(f"Unsupported repo: {repo_url}")


def _decode_readme(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").replace("\x00", "")
    stripped = text.lstrip()
    if stripped.startswith("{") and '"content"' in stripped[:400]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("encoding") == "base64" and payload.get("content"):
            decoded = base64.b64decode(payload["content"])
            text = decoded.decode("utf-8", errors="replace").replace("\x00", "")
    if len(text) > 400_000:
        return text[:400_000] + "\n\n…(truncated)"
    return text


def _etag_path(paths: AppPaths, ref: RepoRef) -> Path:
    key = ref.full_path.replace("/", "_")
    return paths.etag_dir / f"{key}.etag"


def _release_cache_path(paths: AppPaths, ref: RepoRef) -> Path:
    key = ref.full_path.replace("/", "_")
    return paths.etag_dir / f"{key}.release.json"

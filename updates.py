"""Explicit, read-only GitHub release check. Never downloads or executes updates."""

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from version import VERSION

REPOSITORY = "TengJoe/debloom"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"


class UpdateError(RuntimeError):
    pass


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise UpdateError("发布版本信息无效，请稍后重试。")
    return tuple(map(int, match.groups()))


def check_for_update(opener=None):
    request = Request(
        API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Debloom/" + VERSION,
        },
    )
    try:
        with (opener or urlopen)(request, timeout=8) as response:
            data = response.read(262145)
        if len(data) > 262144:
            raise UpdateError("发布版本信息无效，请稍后重试。")
        release = json.loads(data)
        tag = release.get("tag_name", "")
        if (
            not isinstance(tag, str)
            or release.get("draft")
            or release.get("prerelease")
        ):
            raise UpdateError("发布版本信息无效，请稍后重试。")
        latest = version_tuple(tag)
    except HTTPError as error:
        error.close()
        if error.code == 404:
            return {
                "current": VERSION,
                "latest": "",
                "available": False,
                "url": "",
                "status": "unpublished",
            }
        raise UpdateError("无法连接 GitHub，请稍后重试。") from None
    except (URLError, OSError):
        raise UpdateError("无法连接 GitHub，请检查网络后重试。") from None
    except (ValueError, TypeError, AttributeError):
        raise UpdateError("发布版本信息无效，请稍后重试。") from None
    return {
        "current": VERSION,
        "latest": tag.removeprefix("v"),
        "available": latest > version_tuple(VERSION),
        "url": f"https://github.com/{REPOSITORY}/releases/tag/" + quote(tag, safe=""),
        "status": "available" if latest > version_tuple(VERSION) else "current",
    }

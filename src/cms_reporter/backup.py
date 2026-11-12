"""Channel metadata snapshots.

If a channel is hijacked or terminated, Google's recovery forms ask for details
only the owner would have on file. A dated snapshot of every channel in the
network (and a diff against the previous one) is that file - and the diff is an
early warning when a title, handle or description changes unexpectedly.
"""

from __future__ import annotations

import re
import zlib
from datetime import datetime
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from .config import NetworkConfig, Settings

WATCHED = ["Title", "Description", "Custom URL", "Country"]


class ChannelSource(Protocol):
    def channels(self, ids: list[str]) -> list[dict]: ...
    def video_ids(self, playlist_id: str) -> list[str]: ...
    def videos(self, ids: list[str]) -> list[dict]: ...


def _chunks(items: list[str], size: int = 50):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def duration_seconds(iso: str | None) -> int:
    """ISO-8601 duration as returned by the API ('PT1H2M3S', 'P1DT2H') in seconds."""
    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", iso or "")
    if not match:
        return 0
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


class YouTubeDataSource:
    """YouTube Data API v3 with an API key (public data only)."""

    def __init__(self, api_key: str):
        from googleapiclient.discovery import build

        self._api = build("youtube", "v3", developerKey=api_key, cache_discovery=False)

    def channels(self, ids: list[str]) -> list[dict]:
        items: list[dict] = []
        for chunk in _chunks(ids):  # the API accepts at most 50 IDs per call
            response = self._api.channels().list(part="snippet,contentDetails,statistics", id=",".join(chunk)).execute()
            items += response.get("items", [])
        return items

    def video_ids(self, playlist_id: str) -> list[str]:
        ids: list[str] = []
        token = None
        while True:
            response = (
                self._api.playlistItems()
                .list(part="contentDetails", playlistId=playlist_id, maxResults=50, pageToken=token)
                .execute()
            )
            ids += [item["contentDetails"]["videoId"] for item in response.get("items", [])]
            token = response.get("nextPageToken")
            if not token:
                return ids

    def videos(self, ids: list[str]) -> list[dict]:
        items: list[dict] = []
        for chunk in _chunks(ids):
            response = self._api.videos().list(part="snippet,contentDetails,statistics", id=",".join(chunk)).execute()
            items += response.get("items", [])
        return items


class DemoSource:
    """Offline stand-in that answers in the API's response shape with synthetic channels."""

    def __init__(self, cfg: NetworkConfig):
        self._names = {c: p.name for v in cfg.verticals.values() for p in v.producers for c in p.channels}

    @staticmethod
    def _rng(key: str) -> np.random.Generator:
        return np.random.default_rng(zlib.crc32(key.encode()))

    def channels(self, ids: list[str]) -> list[dict]:
        out = []
        for cid in ids:
            rng, name = self._rng(cid), self._names.get(cid, "Demo Channel")
            thumb = f"https://img.example/{cid}"
            out.append(
                {
                    "id": cid,
                    "snippet": {
                        "title": f"{name} {cid[-2:]}",
                        "description": f"Official channel of {name}. Demo data.",
                        "customUrl": f"@{re.sub(r'[^a-z0-9]', '', name.lower())}{cid[-2:]}",
                        "publishedAt": f"20{rng.integers(10, 24)}-0{rng.integers(1, 10)}-15T09:00:00Z",
                        "country": "TR",
                        "thumbnails": {k: {"url": f"{thumb}/{k}.jpg"} for k in ("default", "medium", "high")},
                    },
                    "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid[2:]}},
                    "statistics": {
                        "viewCount": str(rng.integers(10**5, 10**8)),
                        "subscriberCount": str(rng.integers(10**3, 10**6)),
                        "videoCount": str(rng.integers(3, 9)),
                    },
                }
            )
        return out

    def video_ids(self, playlist_id: str) -> list[str]:
        count = int(self._rng("UC" + playlist_id[2:]).integers(3, 9))
        return [f"demo{playlist_id[-4:]}{i:03d}" for i in range(count)]

    def videos(self, ids: list[str]) -> list[dict]:
        out = []
        for vid in ids:
            rng = self._rng(vid)
            out.append(
                {
                    "id": vid,
                    "snippet": {
                        "channelTitle": f"Demo Channel {vid[4:8]}",
                        "title": f"Demo video {vid[-3:]}",
                        "description": "Demo data.",
                        "tags": ["demo"],
                        "publishedAt": "2025-03-01T12:00:00Z",
                    },
                    "statistics": {
                        "viewCount": str(rng.integers(100, 10**6)),
                        "likeCount": str(rng.integers(1, 10**4)),
                        "commentCount": str(rng.integers(0, 500)),
                    },
                    "contentDetails": {
                        "duration": f"PT{rng.integers(1, 59)}M{rng.integers(0, 59)}S",
                        "definition": "hd",
                        "caption": "false",
                    },
                }
            )
        return out


def _channel_row(item: dict) -> dict:
    snippet, stats = item.get("snippet", {}), item.get("statistics", {})
    thumbs = snippet.get("thumbnails", {})
    return {
        "Channel ID": item["id"],
        "Title": snippet.get("title", ""),
        "Description": snippet.get("description", ""),
        "Custom URL": snippet.get("customUrl", ""),
        "Published At": snippet.get("publishedAt", ""),
        "Country": snippet.get("country", ""),
        "Thumbnail Default": thumbs.get("default", {}).get("url", ""),
        "Thumbnail Medium": thumbs.get("medium", {}).get("url", ""),
        "Thumbnail High": thumbs.get("high", {}).get("url", ""),
        "Uploads Playlist": item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", ""),
        # channels can hide these, so never index them directly
        "View Count": stats.get("viewCount"),
        "Subscriber Count": stats.get("subscriberCount"),
        "Video Count": stats.get("videoCount"),
    }


def _video_row(item: dict) -> dict:
    snippet, stats, details = item.get("snippet", {}), item.get("statistics", {}), item.get("contentDetails", {})
    return {
        "Video ID": item["id"],
        "Channel Title": snippet.get("channelTitle", ""),
        "Title": snippet.get("title", ""),
        "Description": snippet.get("description", ""),
        "Tags": "|".join(snippet.get("tags") or []),
        "Published At": snippet.get("publishedAt", ""),
        "View Count": stats.get("viewCount"),
        "Like Count": stats.get("likeCount"),
        "Comment Count": stats.get("commentCount"),
        "Duration (s)": duration_seconds(details.get("duration")),
        "Definition": details.get("definition", ""),
        "Caption": details.get("caption", ""),
    }


def collect(
    cfg: NetworkConfig, source: ChannelSource, include_videos: bool = True
) -> tuple[pd.DataFrame, pd.DataFrame]:
    owners = pd.DataFrame(
        [(c, v.label, p.name) for v in cfg.verticals.values() for p in v.producers for c in p.channels],
        columns=["Channel ID", "Vertical", "Producer"],
    ).drop_duplicates("Channel ID")
    fetched = pd.DataFrame([_channel_row(i) for i in source.channels(owners["Channel ID"].tolist())])
    channels = owners.merge(fetched, on="Channel ID", how="left") if not fetched.empty else owners.assign(Title=None)
    channels.insert(3, "Found", channels["Title"].notna())

    videos = pd.DataFrame()
    if include_videos and "Uploads Playlist" in channels:
        ids: list[str] = []
        for playlist in channels["Uploads Playlist"].dropna():
            if playlist:
                ids += source.video_ids(playlist)
        videos = pd.DataFrame([_video_row(i) for i in source.videos(ids)])
    return channels, videos


def compare(previous: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """What changed between two channel snapshots - the things worth a second look."""
    changes = []
    before = previous.set_index("Channel ID")
    for row in current.to_dict("records"):
        cid = row["Channel ID"]
        if cid not in before.index:
            changes.append((cid, "added to network", "", row.get("Title") or ""))
            continue
        old = before.loc[cid]
        if bool(old.get("Found", True)) and not row.get("Found", True):
            changes.append((cid, "NOT RETURNED BY API (terminated, hidden or hijacked?)", old.get("Title", ""), ""))
            continue
        for column in WATCHED:
            a, b = old.get(column), row.get(column)
            a, b = ("" if pd.isna(a) else str(a)), ("" if b is None or pd.isna(b) else str(b))
            if a != b:
                changes.append((cid, f"{column} changed", a, b))
    for cid in before.index.difference(current["Channel ID"]):
        changes.append((cid, "removed from network config", before.loc[cid].get("Title", ""), ""))
    return pd.DataFrame(changes, columns=["Channel ID", "Change", "Before", "After"])


def snapshot(
    settings: Settings,
    cfg: NetworkConfig,
    source: ChannelSource,
    include_videos: bool = True,
    now: datetime | None = None,
) -> tuple[Path, pd.DataFrame]:
    """Write a dated snapshot folder. Returns (folder, changes since the previous snapshot)."""
    channels, videos = collect(cfg, source, include_videos)
    earlier = sorted(p for p in settings.backups.glob("*/channels.csv"))
    folder = settings.backups / (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)

    channels.to_csv(folder / "channels.csv", index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(folder / "snapshot.xlsx", engine="openpyxl") as writer:
        channels.to_excel(writer, sheet_name="Channels", index=False)
        if not videos.empty:
            videos.to_excel(writer, sheet_name="Videos", index=False)
    if not videos.empty:
        videos.to_csv(folder / "videos.csv", index=False, encoding="utf-8-sig")

    changes = (
        compare(pd.read_csv(earlier[-1]), channels)
        if earlier
        else pd.DataFrame(columns=["Channel ID", "Change", "Before", "After"])
    )
    changes.to_csv(folder / "changes.csv", index=False, encoding="utf-8-sig")
    return folder, changes

"""Audience analytics for a client's channels (views, watch time, age and gender).

Revenue, top assets and geography come straight from the CMS exports; audience
data does not exist there and has to come from the YouTube Analytics API, which
needs OAuth consent from the content owner. `DemoAnalytics` produces synthetic
figures so reports can be built without credentials.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

AGE_GROUPS = ["13-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
GENDERS = ["Female", "Male", "Other"]


@dataclass
class AudienceAnalytics:
    views: int
    watch_hours: float
    subscribers_gained: int
    demographics: pd.DataFrame  # columns: age_group, gender, viewer_pct (sums to 100)
    synthetic: bool = False


class AnalyticsProvider(Protocol):
    def fetch(self, channel_ids: list[str], period: str) -> AudienceAnalytics | None: ...


class NoAnalytics:
    def fetch(self, channel_ids: list[str], period: str) -> AudienceAnalytics | None:
        return None


class DemoAnalytics:
    """Deterministic synthetic audience figures, seeded by channels and month."""

    def fetch(self, channel_ids: list[str], period: str) -> AudienceAnalytics | None:
        if not channel_ids:
            return None
        rng = np.random.default_rng(zlib.crc32(f"{sorted(channel_ids)}/{period}".encode()))
        age = rng.dirichlet(np.array([3, 14, 22, 16, 10, 6, 3]) * 4)
        female = rng.uniform(0.3, 0.6)
        split = np.array([female, 1 - female - 0.02, 0.02])
        grid = np.outer(age, split) * 100
        demographics = pd.DataFrame(
            [(a, g, round(float(grid[i, j]), 2)) for i, a in enumerate(AGE_GROUPS) for j, g in enumerate(GENDERS)],
            columns=["age_group", "gender", "viewer_pct"],
        )
        views = int(rng.integers(40_000, 2_500_000)) * len(channel_ids)
        return AudienceAnalytics(
            views,
            round(views * rng.uniform(0.03, 0.09), 1),
            int(views * rng.uniform(0.0005, 0.003)),
            demographics,
            synthetic=True,
        )


class YouTubeAnalytics:
    """Live figures from the YouTube Analytics API (v2) for a content owner.

    Needs an OAuth client secret file from Google Cloud and, on first use, a
    browser consent by an account with access to the content owner. The token
    is cached next to the secret. Not exercised by the test suite - it cannot
    run without real credentials.
    """

    SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly"]
    _GENDER = {"female": "Female", "male": "Male", "user_specified": "Other"}

    def __init__(self, client_secret: Path, content_owner: str, token_path: Path | None = None):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        token_path = token_path or client_secret.with_name("analytics_token.json")
        creds = Credentials.from_authorized_user_file(str(token_path), self.SCOPES) if token_path.exists() else None
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if not creds or not creds.valid:
            creds = InstalledAppFlow.from_client_secrets_file(str(client_secret), self.SCOPES).run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        self._api = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
        self._owner = content_owner

    def _query(self, channel: str, period: str, **params: str) -> list[list]:
        month = pd.Period(period, freq="M")
        response = (
            self._api.reports()
            .query(
                ids=f"contentOwner=={self._owner}",
                filters=f"channel=={channel}",
                startDate=str(month.start_time.date()),
                endDate=str(month.end_time.date()),
                **params,
            )
            .execute()
        )
        return response.get("rows") or []

    def fetch(self, channel_ids: list[str], period: str) -> AudienceAnalytics | None:
        views = minutes = subscribers = 0.0
        weighted: dict[tuple[str, str], float] = {}
        for channel in channel_ids:
            totals = self._query(channel, period, metrics="views,estimatedMinutesWatched,subscribersGained")
            if not totals:
                continue
            channel_views = float(totals[0][0])
            views += channel_views
            minutes += float(totals[0][1])
            subscribers += float(totals[0][2])
            # viewerPercentage is per channel; weight by views to combine channels
            for age, gender, pct in self._query(
                channel, period, metrics="viewerPercentage", dimensions="ageGroup,gender"
            ):
                key = (age.removeprefix("age").replace("65-", "65+"), self._GENDER.get(gender, "Other"))
                weighted[key] = weighted.get(key, 0.0) + float(pct) * channel_views
        if not views:
            return None
        demographics = pd.DataFrame(
            [(a, g, v / views) for (a, g), v in weighted.items()], columns=["age_group", "gender", "viewer_pct"]
        )
        return AudienceAnalytics(int(views), minutes / 60, int(subscribers), demographics)

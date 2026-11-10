"""YouTube Shorts via the YouTube Data API v3 (resumable upload) and YouTube Analytics API.

Notes
* A Short is a vertical video <= 3 minutes; there is no separate Shorts endpoint.
* Uploads from unverified API projects are locked to private until the project
  passes Google's audit; the client reports that, it does not work around it.
* The API cannot pin comments; the pinned comment is stored for manual pinning.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from ..config import get_settings
from ..providers.http import ProviderError, request
from .base import PlatformClient, TokenSet, UploadOptions, UploadResult, nullable_float, nullable_int

API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"


class YouTubeClient(PlatformClient):
    platform = "youtube"
    scopes = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]

    def __init__(self, client=None):
        super().__init__(client)
        s = get_settings()
        self.client_id, self.client_secret = s.google_client_id, s.google_client_secret

    def missing(self) -> list[str]:
        return [k for k, v in (("GOOGLE_CLIENT_ID", self.client_id),
                               ("GOOGLE_CLIENT_SECRET", self.client_secret)) if not v]

    def authorize_url(self, state: str, redirect_uri: str, code_challenge: str) -> str:
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": " ".join(self.scopes), "access_type": "offline", "prompt": "consent",
            "include_granted_scopes": "true", "state": state,
            "code_challenge": code_challenge, "code_challenge_method": "S256",
        })

    def _token(self, data: dict) -> TokenSet:
        body = request("POST", "https://oauth2.googleapis.com/token", data=data, client=self.http).json()
        return TokenSet(access_token=body["access_token"], refresh_token=body.get("refresh_token", ""),
                        expires_in=body.get("expires_in"), scopes=body.get("scope", "").split())

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        return self._token({"code": code, "client_id": self.client_id, "client_secret": self.client_secret,
                            "redirect_uri": redirect_uri, "grant_type": "authorization_code",
                            "code_verifier": code_verifier})

    def refresh(self, refresh_token: str) -> TokenSet:
        t = self._token({"refresh_token": refresh_token, "client_id": self.client_id,
                         "client_secret": self.client_secret, "grant_type": "refresh_token"})
        t.refresh_token = t.refresh_token or refresh_token
        return t

    def _auth(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def account_info(self, access_token: str) -> dict:
        body = request("GET", f"{API}/channels", params={"part": "snippet,status", "mine": "true"},
                       headers=self._auth(access_token), client=self.http).json()
        items = body.get("items", [])
        if not items:
            raise ProviderError("authorised Google account has no YouTube channel")
        ch = items[0]
        return {"account_id": ch["id"], "account_name": ch["snippet"]["title"],
                "capabilities": {"long_uploads_status": ch.get("status", {}).get("longUploadsStatus")}}

    def eligibility(self, access_token: str, video: dict, options: UploadOptions) -> list[str]:
        problems = []
        if video["duration"] > 180:
            problems.append("YouTube Shorts must be 3 minutes or shorter")
        if video["height"] <= video["width"]:
            problems.append("Shorts must be vertical or square")
        if options.privacy not in ("private", "unlisted", "public"):
            problems.append(f"invalid privacy '{options.privacy}'")
        return problems

    def upload(self, access_token: str, video_path: Path, metadata: dict, options: UploadOptions) -> UploadResult:
        status = {"privacyStatus": options.privacy, "selfDeclaredMadeForKids": False,
                  "containsSyntheticMedia": bool(metadata.get("contains_synthetic_media", False))}
        if options.publish_at:
            status["privacyStatus"] = "private"  # required for scheduled publishing
            status["publishAt"] = options.publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {
            "snippet": {"title": metadata["title"][:100], "description": metadata["description"][:5000],
                        "tags": [k for k in metadata.get("keywords", [])][:15], "categoryId": "20",
                        "defaultLanguage": "en"},
            "status": status,
        }
        size = video_path.stat().st_size
        init = request(
            "POST", f"{UPLOAD}/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={**self._auth(access_token), "X-Upload-Content-Type": "video/mp4",
                     "X-Upload-Content-Length": str(size), "Content-Type": "application/json"},
            json=body, client=self.http,
        )
        location = init.headers.get("location")
        if not location:
            raise ProviderError("YouTube did not return a resumable upload URL")
        with video_path.open("rb") as fh:
            resp = request("PUT", location, content=fh.read(), timeout=900,
                           headers={**self._auth(access_token), "Content-Type": "video/mp4"}, client=self.http)
        video = resp.json()
        vid = video.get("id")
        if not vid:
            raise ProviderError("YouTube upload finished without a video id")
        if options.thumbnail_path and options.thumbnail_path.exists():
            try:
                request("POST", f"{UPLOAD}/thumbnails/set", params={"videoId": vid},
                        headers={**self._auth(access_token), "Content-Type": "image/jpeg"},
                        content=options.thumbnail_path.read_bytes(), client=self.http)
            except ProviderError:
                pass  # custom thumbnails need a verified channel; not fatal
        return UploadResult(status="processing", external_id=vid, url=f"https://youtube.com/shorts/{vid}",
                            detail="uploaded; awaiting processing")

    def status(self, access_token: str, external_id: str | None, tracking_id: str | None) -> UploadResult:
        body = request("GET", f"{API}/videos", params={"part": "status,processingDetails", "id": external_id},
                       headers=self._auth(access_token), client=self.http).json()
        items = body.get("items", [])
        if not items:
            return UploadResult(status="failed", external_id=external_id, detail="video not found (deleted or rejected)")
        st = items[0].get("status", {})
        upload_status = st.get("uploadStatus")
        url = f"https://youtube.com/shorts/{external_id}"
        if upload_status in ("failed", "rejected", "deleted"):
            reason = st.get("failureReason") or st.get("rejectionReason") or upload_status
            return UploadResult(status="failed", external_id=external_id, detail=f"YouTube: {reason}")
        if upload_status != "processed":
            return UploadResult(status="processing", external_id=external_id, url=url, detail=upload_status or "")
        if st.get("privacyStatus") == "public":
            return UploadResult(status="published", external_id=external_id, url=url, detail="public")
        if st.get("publishAt"):
            return UploadResult(status="processing", external_id=external_id, url=url,
                                detail=f"scheduled for {st['publishAt']}")
        return UploadResult(status="drafted", external_id=external_id, url=url,
                            detail=f"processed, {st.get('privacyStatus')}")

    def metrics(self, access_token: str, external_id: str) -> dict:
        stats = request("GET", f"{API}/videos", params={"part": "statistics", "id": external_id},
                        headers=self._auth(access_token), client=self.http).json()
        st = (stats.get("items") or [{}])[0].get("statistics", {})
        out = {"views": nullable_int(st.get("viewCount")), "likes": nullable_int(st.get("likeCount")),
               "comments": nullable_int(st.get("commentCount")), "shares": None, "saves": None,
               "avg_watch_seconds": None, "avg_view_percentage": None, "followers_gained": None,
               "raw": {"statistics": st}}
        try:
            report = request(
                "GET", "https://youtubeanalytics.googleapis.com/v2/reports",
                params={"ids": "channel==MINE", "startDate": "2020-01-01", "endDate": date.today().isoformat(),
                        "metrics": "shares,averageViewDuration,averageViewPercentage,subscribersGained",
                        "filters": f"video=={external_id}"},
                headers=self._auth(access_token), client=self.http,
            ).json()
            headers = [h["name"] for h in report.get("columnHeaders", [])]
            rows = report.get("rows") or []
            if rows:
                row = dict(zip(headers, rows[0]))
                out["shares"] = nullable_int(row.get("shares"))
                out["avg_watch_seconds"] = nullable_float(row.get("averageViewDuration"))
                out["avg_view_percentage"] = nullable_float(row.get("averageViewPercentage"))
                out["followers_gained"] = nullable_int(row.get("subscribersGained"))
            out["raw"]["analytics"] = report.get("rows")
        except ProviderError as exc:
            out["raw"]["analytics_error"] = str(exc)[:300]
        return out

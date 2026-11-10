"""TikTok via the official Content Posting API (v2).

Compliance model
* Default is ``inbox_draft``: the video lands in the creator's TikTok inbox and a
  person finishes posting in the app. This satisfies TikTok's requirement that
  the user sees creator info and consents to each post.
* ``direct_post`` is only used when the Publication carries an explicit,
  per-post consent record from the dashboard (privacy level chosen by a person,
  music-usage confirmation, timestamp, actor). Without it the post is blocked.
* Apps that have not passed TikTok's audit can only post with SELF_ONLY privacy.
"""

from __future__ import annotations

import math
from pathlib import Path
from urllib.parse import urlencode

from ..config import get_settings
from ..providers.http import ProviderError, request
from .base import PlatformClient, TokenSet, UploadOptions, UploadResult, nullable_int

API = "https://open.tiktokapis.com/v2"
CHUNK = 10 * 1024 * 1024  # 5 MB - 64 MB allowed per chunk


def _check(body: dict) -> dict:
    err = body.get("error") or {}
    if err.get("code") not in (None, "ok"):
        transient = err.get("code") in ("rate_limit_exceeded", "internal_error")
        raise ProviderError(f"TikTok {err.get('code')}: {err.get('message')}", transient=transient)
    return body.get("data") or {}


class TikTokClient(PlatformClient):
    platform = "tiktok"
    scopes = ["user.info.basic", "video.upload", "video.publish", "video.list"]

    def __init__(self, client=None):
        super().__init__(client)
        s = get_settings()
        self.client_key, self.client_secret = s.tiktok_client_key, s.tiktok_client_secret

    def missing(self) -> list[str]:
        return [k for k, v in (("TIKTOK_CLIENT_KEY", self.client_key),
                               ("TIKTOK_CLIENT_SECRET", self.client_secret)) if not v]

    def authorize_url(self, state: str, redirect_uri: str, code_challenge: str) -> str:
        return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode({
            "client_key": self.client_key, "scope": ",".join(self.scopes), "response_type": "code",
            "redirect_uri": redirect_uri, "state": state,
            "code_challenge": code_challenge, "code_challenge_method": "S256",
        })

    def _token(self, data: dict) -> TokenSet:
        body = request("POST", f"{API}/oauth/token/", data=data, client=self.http,
                       headers={"Content-Type": "application/x-www-form-urlencoded"}).json()
        if "access_token" not in body:
            raise ProviderError(f"TikTok token error: {body.get('error_description') or body.get('error')}")
        return TokenSet(access_token=body["access_token"], refresh_token=body.get("refresh_token", ""),
                        expires_in=body.get("expires_in"), scopes=body.get("scope", "").split(","),
                        account_id=body.get("open_id", ""))

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        return self._token({"client_key": self.client_key, "client_secret": self.client_secret, "code": code,
                            "grant_type": "authorization_code", "redirect_uri": redirect_uri,
                            "code_verifier": code_verifier})

    def refresh(self, refresh_token: str) -> TokenSet:
        return self._token({"client_key": self.client_key, "client_secret": self.client_secret,
                            "grant_type": "refresh_token", "refresh_token": refresh_token})

    def _auth(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}

    def creator_info(self, access_token: str) -> dict:
        body = request("POST", f"{API}/post/publish/creator_info/query/", headers=self._auth(access_token),
                       client=self.http).json()
        return _check(body)

    def account_info(self, access_token: str) -> dict:
        body = request("GET", f"{API}/user/info/", params={"fields": "open_id,display_name"},
                       headers=self._auth(access_token), client=self.http).json()
        user = _check(body).get("user", {})
        info = self.creator_info(access_token)
        return {"account_id": user.get("open_id", ""), "account_name": user.get("display_name", ""),
                "capabilities": {"privacy_level_options": info.get("privacy_level_options", []),
                                 "max_video_post_duration_sec": info.get("max_video_post_duration_sec"),
                                 "comment_disabled": info.get("comment_disabled")}}

    def eligibility(self, access_token: str, video: dict, options: UploadOptions) -> list[str]:
        problems = []
        info = self.creator_info(access_token)
        max_dur = info.get("max_video_post_duration_sec")
        if max_dur and video["duration"] > max_dur:
            problems.append(f"video is {video['duration']:.0f}s; creator limit is {max_dur}s")
        if options.tiktok_mode == "direct_post":
            consent = options.consent or {}
            required = ("privacy_level", "confirmed_by", "confirmed_at", "music_usage_confirmed")
            if not all(consent.get(k) for k in required):
                problems.append("direct post needs per-post consent (privacy level chosen by a person, "
                                "music usage confirmation) recorded from the dashboard")
            elif consent["privacy_level"] not in info.get("privacy_level_options", []):
                problems.append(f"privacy {consent['privacy_level']} not offered for this creator")
        return problems

    def upload(self, access_token: str, video_path: Path, metadata: dict, options: UploadOptions) -> UploadResult:
        size = video_path.stat().st_size
        chunk = size if size <= 64 * 1024 * 1024 else CHUNK
        count = max(1, math.floor(size / chunk))  # TikTok folds the remainder into the last chunk
        source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
        if options.tiktok_mode == "direct_post":
            consent = options.consent
            payload = {
                "post_info": {
                    "title": metadata["description"][:2200],
                    "privacy_level": consent["privacy_level"],
                    "disable_comment": bool(consent.get("disable_comment", False)),
                    "disable_duet": bool(consent.get("disable_duet", False)),
                    "disable_stitch": bool(consent.get("disable_stitch", False)),
                    "video_cover_timestamp_ms": options.cover_timestamp_ms,
                    "is_aigc": options.ai_disclosure,
                },
                "source_info": source,
            }
            url = f"{API}/post/publish/video/init/"
        else:
            payload = {"source_info": source}
            url = f"{API}/post/publish/inbox/video/init/"
        data = _check(request("POST", url, json=payload, headers=self._auth(access_token), client=self.http).json())
        publish_id, upload_url = data["publish_id"], data["upload_url"]
        with video_path.open("rb") as fh:
            for i in range(count):
                start = i * chunk
                end = size - 1 if i == count - 1 else start + chunk - 1
                fh.seek(start)
                request("PUT", upload_url, content=fh.read(end - start + 1), timeout=900, client=self.http,
                        headers={"Content-Type": "video/mp4", "Content-Length": str(end - start + 1),
                                 "Content-Range": f"bytes {start}-{end}/{size}"})
        return UploadResult(status="processing", tracking_id=publish_id, detail=f"{options.tiktok_mode} uploaded")

    def status(self, access_token: str, external_id: str | None, tracking_id: str | None) -> UploadResult:
        data = _check(request("POST", f"{API}/post/publish/status/fetch/", json={"publish_id": tracking_id},
                              headers=self._auth(access_token), client=self.http).json())
        st = data.get("status", "")
        if st == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id") or []
            vid = str(ids[0]) if ids else None
            return UploadResult(status="published", external_id=vid, tracking_id=tracking_id,
                                url=f"https://www.tiktok.com/video/{vid}" if vid else None,
                                detail="published" if vid else "published (private: no public id)")
        if st == "SEND_TO_USER_INBOX":
            return UploadResult(status="drafted", tracking_id=tracking_id,
                                detail="in creator's TikTok inbox; finish posting in the app")
        if st == "FAILED":
            return UploadResult(status="failed", tracking_id=tracking_id, detail=f"TikTok: {data.get('fail_reason')}")
        return UploadResult(status="processing", tracking_id=tracking_id, detail=st)

    def metrics(self, access_token: str, external_id: str) -> dict:
        data = _check(request(
            "POST", f"{API}/video/query/",
            params={"fields": "id,view_count,like_count,comment_count,share_count,duration"},
            json={"filters": {"video_ids": [external_id]}}, headers=self._auth(access_token), client=self.http,
        ).json())
        v = (data.get("videos") or [{}])[0]
        return {"views": nullable_int(v.get("view_count")), "likes": nullable_int(v.get("like_count")),
                "comments": nullable_int(v.get("comment_count")), "shares": nullable_int(v.get("share_count")),
                "saves": None, "avg_watch_seconds": None, "avg_view_percentage": None,
                "followers_gained": None, "raw": v}

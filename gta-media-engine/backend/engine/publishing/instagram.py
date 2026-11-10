"""Instagram Reels via the Instagram API with Instagram Login (professional accounts only).

Flow: create a REELS container with ``upload_type=resumable`` -> upload bytes to
rupload.facebook.com -> poll ``status_code`` until FINISHED -> ``media_publish``.
The container step and the publish step are separate so the manager only marks
the Reel published after ``media_publish`` returns a media id.

Instagram has no private/draft publishing through the API, so the system never
uploads to Instagram during TEST mode.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from ..config import get_settings
from ..providers.http import ProviderError, request
from .base import PlatformClient, TokenSet, UploadOptions, UploadResult, nullable_float, nullable_int


class InstagramClient(PlatformClient):
    platform = "instagram"
    scopes = ["instagram_business_basic", "instagram_business_content_publish",
              "instagram_business_manage_insights"]

    def __init__(self, client=None):
        super().__init__(client)
        s = get_settings()
        self.app_id, self.app_secret, self.version = s.meta_app_id, s.meta_app_secret, s.meta_graph_version
        self.graph = f"https://graph.instagram.com/{self.version}"
        self.account_id = ""

    def missing(self) -> list[str]:
        return [k for k, v in (("META_APP_ID", self.app_id), ("META_APP_SECRET", self.app_secret)) if not v]

    def authorize_url(self, state: str, redirect_uri: str, code_challenge: str) -> str:
        return "https://www.instagram.com/oauth/authorize?" + urlencode({
            "client_id": self.app_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": ",".join(self.scopes), "state": state,
        })

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        short = request("POST", "https://api.instagram.com/oauth/access_token", client=self.http, data={
            "client_id": self.app_id, "client_secret": self.app_secret, "grant_type": "authorization_code",
            "redirect_uri": redirect_uri, "code": code,
        }).json()
        long = request("GET", "https://graph.instagram.com/access_token", client=self.http, params={
            "grant_type": "ig_exchange_token", "client_secret": self.app_secret,
            "access_token": short["access_token"],
        }).json()
        # Long-lived tokens are refreshed with themselves; store the same token as refresh_token.
        return TokenSet(access_token=long["access_token"], refresh_token=long["access_token"],
                        expires_in=long.get("expires_in"), scopes=self.scopes,
                        account_id=str(short.get("user_id", "")))

    def refresh(self, refresh_token: str) -> TokenSet:
        body = request("GET", "https://graph.instagram.com/refresh_access_token", client=self.http,
                       params={"grant_type": "ig_refresh_token", "access_token": refresh_token}).json()
        return TokenSet(access_token=body["access_token"], refresh_token=body["access_token"],
                        expires_in=body.get("expires_in"), scopes=self.scopes)

    def account_info(self, access_token: str) -> dict:
        me = request("GET", f"{self.graph}/me", client=self.http,
                     params={"fields": "user_id,username,account_type", "access_token": access_token}).json()
        if me.get("account_type") not in ("BUSINESS", "MEDIA_CREATOR", "CREATOR"):
            raise ProviderError("Instagram account must be a professional (Business or Creator) account")
        limit = self.publishing_limit(access_token, str(me["user_id"]))
        return {"account_id": str(me["user_id"]), "account_name": me.get("username", ""),
                "capabilities": {"account_type": me.get("account_type"), "publishing_limit": limit}}

    def publishing_limit(self, access_token: str, user_id: str) -> dict:
        body = request("GET", f"{self.graph}/{user_id}/content_publishing_limit", client=self.http,
                       params={"fields": "quota_usage,config", "access_token": access_token}).json()
        row = (body.get("data") or [{}])[0]
        return {"quota_usage": row.get("quota_usage"), "quota_total": (row.get("config") or {}).get("quota_total")}

    def eligibility(self, access_token: str, video: dict, options: UploadOptions) -> list[str]:
        problems = []
        if not 3 <= video["duration"] <= 900:
            problems.append("Reels must be 3 seconds to 15 minutes")
        user_id = options.consent.get("ig_user_id") or self.account_id
        if user_id:
            lim = self.publishing_limit(access_token, user_id)
            if lim["quota_usage"] is not None and lim["quota_total"] and lim["quota_usage"] >= lim["quota_total"]:
                problems.append(f"Instagram 24h publishing limit reached ({lim['quota_usage']}/{lim['quota_total']})")
        return problems

    def upload(self, access_token: str, video_path: Path, metadata: dict, options: UploadOptions) -> UploadResult:
        user_id = options.consent.get("ig_user_id") or self.account_id
        if not user_id:
            raise ProviderError("Instagram user id unknown; reconnect the account")
        container = request("POST", f"{self.graph}/{user_id}/media", client=self.http, data={
            "media_type": "REELS", "upload_type": "resumable", "caption": metadata["description"][:2200],
            "share_to_feed": "true", "thumb_offset": str(options.cover_timestamp_ms),
            "access_token": access_token,
        }).json()
        cid = container.get("id")
        if not cid:
            raise ProviderError(f"Instagram container not created: {container}")
        size = video_path.stat().st_size
        with video_path.open("rb") as fh:
            request("POST", f"https://rupload.facebook.com/ig-api-upload/{self.version}/{cid}", timeout=900,
                    client=self.http, content=fh.read(),
                    headers={"Authorization": f"OAuth {access_token}", "offset": "0", "file_size": str(size)})
        return UploadResult(status="processing", tracking_id=cid, detail="container uploaded; waiting for processing")

    def status(self, access_token: str, external_id: str | None, tracking_id: str | None) -> UploadResult:
        if external_id:
            media = request("GET", f"{self.graph}/{external_id}", client=self.http,
                            params={"fields": "permalink", "access_token": access_token}).json()
            return UploadResult(status="published", external_id=external_id, tracking_id=tracking_id,
                                url=media.get("permalink"))
        body = request("GET", f"{self.graph}/{tracking_id}", client=self.http,
                       params={"fields": "status_code,status", "access_token": access_token}).json()
        code = body.get("status_code")
        if code in ("ERROR", "EXPIRED"):
            return UploadResult(status="failed", tracking_id=tracking_id, detail=f"Instagram: {body.get('status')}")
        if code != "FINISHED":
            return UploadResult(status="processing", tracking_id=tracking_id, detail=code or "")
        user_id = self.account_id
        pub = request("POST", f"{self.graph}/{user_id}/media_publish", client=self.http,
                      data={"creation_id": tracking_id, "access_token": access_token}).json()
        media_id = pub.get("id")
        if not media_id:
            return UploadResult(status="processing", tracking_id=tracking_id, detail="publish not confirmed yet")
        media = request("GET", f"{self.graph}/{media_id}", client=self.http,
                        params={"fields": "permalink", "access_token": access_token}).json()
        return UploadResult(status="published", external_id=str(media_id), tracking_id=tracking_id,
                            url=media.get("permalink"), detail="published")

    def metrics(self, access_token: str, external_id: str) -> dict:
        metrics = "views,likes,comments,shares,saved,ig_reels_avg_watch_time"
        body = request("GET", f"{self.graph}/{external_id}/insights", client=self.http,
                       params={"metric": metrics, "access_token": access_token}).json()
        vals = {row["name"]: (row.get("values") or [{}])[0].get("value") for row in body.get("data", [])}
        avg_ms = nullable_float(vals.get("ig_reels_avg_watch_time"))
        return {"views": nullable_int(vals.get("views")), "likes": nullable_int(vals.get("likes")),
                "comments": nullable_int(vals.get("comments")), "shares": nullable_int(vals.get("shares")),
                "saves": nullable_int(vals.get("saved")),
                "avg_watch_seconds": None if avg_ms is None else avg_ms / 1000,
                "avg_view_percentage": None, "followers_gained": None, "raw": vals}

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

ROCKETAPI_BASE_URL = "https://v1.rocketapi.io"
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("ROCKETAPI_TIMEOUT", "35"))
MAX_PAGE = 50


def _token() -> str | None:
    return os.environ.get("ROCKETAPI_TOKEN")


def clean_username(value: str) -> str:
    return value.strip().lstrip("@").strip()


class RocketAPIError(Exception):
    def __init__(self, message: str, status: int = 502):
        self.message = message
        self.status = status
        super().__init__(message)


async def rocket_post(client: httpx.AsyncClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = _token()
    if not token:
        raise RocketAPIError("ROCKETAPI_TOKEN روی سرور تنظیم نشده است.", 500)
    try:
        r = await client.post(
            f"{ROCKETAPI_BASE_URL}/{path.lstrip('/')}",
            json=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Token {token}"},
        )
    except httpx.RequestError as e:
        raise RocketAPIError(f"خطا در ارتباط با RocketAPI: {e}") from e
    if r.status_code != 200:
        raise RocketAPIError(http_error(r.status_code), 502)
    try:
        env = r.json()
    except ValueError as e:
        raise RocketAPIError("پاسخ نامعتبر از RocketAPI دریافت شد.") from e
    if not isinstance(env, dict) or env.get("status") != "done":
        raise RocketAPIError("RocketAPI درخواست را کامل نکرد.")
    response = env.get("response") or {}
    body = response.get("body")
    code = response.get("status_code")
    if code != 200 or not isinstance(body, dict):
        raise RocketAPIError(inner_error(code, body))
    return body


def http_error(status: int) -> str:
    return {
        401: "توکن RocketAPI نامعتبر است.",
        402: "اعتبار/سهمیه RocketAPI تمام شده است.",
        403: "دسترسی RocketAPI رد شد.",
        429: "محدودیت درخواست RocketAPI فعال شده است.",
    }.get(status, f"خطای RocketAPI: HTTP {status}")


def inner_error(code: Any, body: Any) -> str:
    msg = body.get("message") or body.get("error_type") if isinstance(body, dict) else ""
    if code == 404:
        return "کاربر یا منبع موردنظر پیدا نشد."
    if code in (400, 401) and "login_required" in str(msg).lower():
        return "این داده نیاز به دسترسی/لاگین دارد یا برای حساب خصوصی قابل دریافت نیست."
    if code == 429:
        return "اینستاگرام موقتاً محدودیت درخواست اعمال کرده است."
    return f"Instagram/RocketAPI درخواست را رد کرد (کد {code})" + (f": {msg}" if msg else "")


def profile_user(body: dict) -> dict | None:
    for obj in (body.get("data"), body):
        if isinstance(obj, dict):
            if isinstance(obj.get("user"), dict):
                return obj["user"]
            if obj.get("id"):
                return obj
    return None


def items(body: dict, keys=("users", "items", "medias", "media", "clips", "stories", "reels")) -> list:
    for key in keys:
        val = body.get(key)
        if isinstance(val, list):
            return val
        data = body.get("data")
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    return []


def cursor(body: dict) -> str | None:
    for key in ("next_max_id", "next_min_id", "max_id", "end_cursor", "next_page_id", "next_page"):
        value = body.get(key)
        if value not in (None, False, ""):
            return str(value)
    return None


def normalize_user(u: dict) -> dict:
    return {
        "id": str(u.get("pk") or u.get("id") or ""),
        "username": u.get("username") or "",
        "full_name": u.get("full_name") or u.get("name") or "",
        "bio": u.get("biography") or u.get("bio") or "",
        "profile_pic_url": u.get("profile_pic_url") or u.get("profile_pic_url_hd") or "",
        "is_private": bool(u.get("is_private", False)),
        "is_verified": bool(u.get("is_verified", False)),
        "followers": u.get("follower_count") or u.get("edge_followed_by", {}).get("count") if isinstance(u.get("edge_followed_by"), dict) else u.get("follower_count"),
        "following": u.get("following_count") or u.get("edge_follow", {}).get("count") if isinstance(u.get("edge_follow"), dict) else u.get("following_count"),
        "posts": u.get("media_count") or u.get("edge_owner_to_timeline_media", {}).get("count") if isinstance(u.get("edge_owner_to_timeline_media"), dict) else u.get("media_count"),
        "external_url": u.get("external_url") or "",
    }


def normalize_media(m: dict) -> dict:
    image = m.get("image_versions2", {}).get("candidates", []) if isinstance(m.get("image_versions2"), dict) else []
    image_url = image[0].get("url") if image and isinstance(image[0], dict) else m.get("display_url") or m.get("thumbnail_url") or ""
    caption = m.get("caption")
    if isinstance(caption, dict):
        caption = caption.get("text")
    return {
        "id": str(m.get("pk") or m.get("id") or ""),
        "code": m.get("code") or m.get("shortcode") or "",
        "type": m.get("media_type") or m.get("product_type") or "",
        "caption": caption or "",
        "image_url": image_url,
        "video_url": m.get("video_url") or "",
        "like_count": m.get("like_count") or m.get("edge_media_preview_like", {}).get("count", 0) if isinstance(m.get("edge_media_preview_like"), dict) else m.get("like_count", 0),
        "comment_count": m.get("comment_count") or m.get("edge_media_to_comment", {}).get("count", 0) if isinstance(m.get("edge_media_to_comment"), dict) else m.get("comment_count", 0),
        "taken_at": m.get("taken_at") or m.get("timestamp") or 0,
        "location": m.get("location") or {},
        "raw": m,
    }


async def fetch_profile_bundle(username: str) -> dict:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        profile_body = await rocket_post(client, "instagram/user/get_web_profile_info", {"username": username})
        user = profile_user(profile_body)
        if not user or not user.get("id"):
            raise RocketAPIError("این یوزرنیم پیدا نشد.", 404)
        uid = int(user["id"])

        # Core account data is fetched together. Optional endpoints failing do not
        # hide the profile: their error is returned beside that section.
        jobs = {
            "info": rocket_post(client, "instagram/user/get_info_by_id", {"id": uid}),
            "media": rocket_post(client, "instagram/user/get_media", {"id": uid, "count": 12}),
            "clips": rocket_post(client, "instagram/user/get_clips", {"id": uid, "count": 12}),
            "followers": rocket_post(client, "instagram/user/get_followers", {"id": uid, "count": 50}),
            "following": rocket_post(client, "instagram/user/get_following", {"id": uid, "count": 50}),
            "stories": rocket_post(client, "instagram/user/get_stories", {"ids": [uid]}),
            "highlights": rocket_post(client, "instagram/user/get_highlights", {"id": uid}),
            "similar": rocket_post(client, "instagram/user/get_similar_accounts", {"id": uid}),
            "about": rocket_post(client, "instagram/user/get_about", {"id": uid}),
            "tags": rocket_post(client, "instagram/user/get_tags", {"id": uid, "count": 50}),
            "guides": rocket_post(client, "instagram/user/get_guides", {"id": uid}),
            "live": rocket_post(client, "instagram/user/get_live", {"id": uid}),
        }
        names = list(jobs)
        results = await asyncio.gather(*(jobs[n] for n in names), return_exceptions=True)
        out: dict[str, Any] = {"profile": normalize_user(user), "sections": {}, "errors": {}}
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                out["errors"][name] = str(result)
                continue
            out["sections"][name] = result

        # Prefer the richer id-based profile when available.
        if "info" in out["sections"]:
            rich = profile_user(out["sections"]["info"])
            if rich:
                out["profile"] = {**out["profile"], **normalize_user(rich)}
        return out


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/profile")
def api_profile():
    username = clean_username(request.args.get("username", ""))
    if not username:
        return jsonify({"error": "یک یوزرنیم وارد کن."}), 400
    try:
        data = asyncio.run(fetch_profile_bundle(username))
        return jsonify(data)
    except RocketAPIError as e:
        return jsonify({"error": e.message}), e.status
    except Exception as e:
        return jsonify({"error": f"خطای غیرمنتظره: {e}"}), 502


@app.post("/api/rocket")
def api_rocket():
    """Small server-side proxy for secondary RocketAPI actions used by the UI.
    The token never reaches the browser."""
    payload = request.get_json(silent=True) or {}
    path = str(payload.pop("path", "")).strip().lstrip("/")
    if not path.startswith("instagram/"):
        return jsonify({"error": "endpoint نامعتبر است."}), 400
    # Explicit allowlist prevents turning this into an arbitrary HTTP proxy.
    allowed = {
        "instagram/media/get_info", "instagram/media/get_info_by_shortcode",
        "instagram/media/get_likes", "instagram/media/get_likes_by_id",
        "instagram/media/get_comments", "instagram/comment/get_likes",
        "instagram/comment/get_replies", "instagram/highlight/get_stories",
        "instagram/media/get_shortcode_by_id", "instagram/media/get_id_by_shortcode",
    }
    if path not in allowed:
        return jsonify({"error": "این endpoint در این پنل فعال نیست."}), 403
    try:
        async def run():
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                return await rocket_post(client, path, payload)
        return jsonify(asyncio.run(run()))
    except RocketAPIError as e:
        return jsonify({"error": e.message}), e.status


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)

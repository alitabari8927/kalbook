from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

ROCKETAPI_BASE_URL = "https://v1.rocketapi.io"
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("ROCKETAPI_TIMEOUT", "35"))


def _token() -> str | None:
    return os.environ.get("ROCKETAPI_TOKEN")


def clean_username(value: str) -> str:
    return value.strip().lstrip("@").strip()


class RocketAPIError(Exception):
    def __init__(self, message: str, status: int = 502):
        self.message = message
        self.status = status
        super().__init__(message)


async def rocket_post(
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:

    token = _token()

    if not token:
        raise RocketAPIError(
            "ROCKETAPI_TOKEN روی سرور تنظیم نشده است.",
            500
        )

    try:
        response = await client.post(
            f"{ROCKETAPI_BASE_URL}/{path.lstrip('/')}",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Token {token}",
            },
        )

    except httpx.RequestError as e:
        raise RocketAPIError(
            f"خطا در ارتباط با RocketAPI: {e}"
        ) from e

    if response.status_code != 200:
        raise RocketAPIError(
            http_error(response.status_code),
            502
        )

    try:
        envelope = response.json()

    except ValueError as e:
        raise RocketAPIError(
            "پاسخ نامعتبر از RocketAPI دریافت شد."
        ) from e

    if not isinstance(envelope, dict):
        raise RocketAPIError(
            "ساختار پاسخ RocketAPI نامعتبر است."
        )

    if envelope.get("status") != "done":
        raise RocketAPIError(
            "RocketAPI درخواست را کامل نکرد."
        )

    response_data = envelope.get("response") or {}

    body = response_data.get("body")
    status_code = response_data.get("status_code")

    if status_code != 200 or not isinstance(body, dict):
        raise RocketAPIError(
            inner_error(status_code, body)
        )

    return body


def http_error(status: int) -> str:

    return {
        401: "توکن RocketAPI نامعتبر است.",
        402: "اعتبار یا سهمیه RocketAPI تمام شده است.",
        403: "دسترسی RocketAPI رد شد.",
        429: "محدودیت درخواست RocketAPI فعال شده است.",
    }.get(
        status,
        f"خطای RocketAPI: HTTP {status}"
    )


def inner_error(code: Any, body: Any) -> str:

    message = ""

    if isinstance(body, dict):
        message = (
            body.get("message")
            or body.get("error_type")
            or ""
        )

    if code == 404:
        return "کاربر یا منبع موردنظر پیدا نشد."

    if code in (400, 401):
        if "login_required" in str(message).lower():
            return (
                "این داده نیاز به دسترسی یا لاگین دارد "
                "یا برای حساب خصوصی قابل دریافت نیست."
            )

    if code == 429:
        return (
            "اینستاگرام موقتاً محدودیت درخواست اعمال کرده است."
        )

    return (
        f"Instagram/RocketAPI درخواست را رد کرد "
        f"(کد {code})"
        + (f": {message}" if message else "")
    )


def profile_user(body: dict) -> dict | None:

    for obj in (
        body.get("data"),
        body,
    ):

        if not isinstance(obj, dict):
            continue

        if isinstance(obj.get("user"), dict):
            return obj["user"]

        if obj.get("id"):
            return obj

    return None


def items(
    body: dict,
    keys=(
        "users",
        "items",
        "medias",
        "media",
        "clips",
        "stories",
        "reels",
    ),
) -> list:

    for key in keys:

        value = body.get(key)

        if isinstance(value, list):
            return value

        data = body.get("data")

        if (
            isinstance(data, dict)
            and isinstance(data.get(key), list)
        ):
            return data[key]

    return []


def cursor(body: dict) -> str | None:

    for key in (
        "next_max_id",
        "next_min_id",
        "max_id",
        "end_cursor",
        "next_page_id",
        "next_page",
    ):

        value = body.get(key)

        if value not in (
            None,
            False,
            "",
        ):
            return str(value)

    return None


def normalize_user(user: dict) -> dict:

    followers = user.get("follower_count")

    if followers is None:
        edge = user.get("edge_followed_by")

        if isinstance(edge, dict):
            followers = edge.get("count")

    following = user.get("following_count")

    if following is None:
        edge = user.get("edge_follow")

        if isinstance(edge, dict):
            following = edge.get("count")

    posts = user.get("media_count")

    if posts is None:
        edge = user.get("edge_owner_to_timeline_media")

        if isinstance(edge, dict):
            posts = edge.get("count")

    return {
        "id": str(
            user.get("pk")
            or user.get("id")
            or ""
        ),

        "username": (
            user.get("username")
            or ""
        ),

        "full_name": (
            user.get("full_name")
            or user.get("name")
            or ""
        ),

        "bio": (
            user.get("biography")
            or user.get("bio")
            or ""
        ),

        "profile_pic_url": (
            user.get("profile_pic_url")
            or user.get("profile_pic_url_hd")
            or ""
        ),

        "is_private": bool(
            user.get("is_private", False)
        ),

        "is_verified": bool(
            user.get("is_verified", False)
        ),

        "followers": followers,
        "following": following,
        "posts": posts,

        "external_url": (
            user.get("external_url")
            or ""
        ),
    }


def normalize_media(media_object: dict) -> dict:

    image_versions = media_object.get(
        "image_versions2"
    )

    candidates = []

    if isinstance(image_versions, dict):
        candidates = image_versions.get(
            "candidates",
            []
        )

    image_url = ""

    if candidates:
        first = candidates[0]

        if isinstance(first, dict):
            image_url = first.get(
                "url",
                ""
            )

    if not image_url:
        image_url = (
            media_object.get("display_url")
            or media_object.get("thumbnail_url")
            or ""
        )

    caption = media_object.get("caption")

    if isinstance(caption, dict):
        caption = caption.get("text")

    like_count = media_object.get(
        "like_count"
    )

    if like_count is None:
        edge = media_object.get(
            "edge_media_preview_like"
        )

        if isinstance(edge, dict):
            like_count = edge.get(
                "count",
                0
            )

    comment_count = media_object.get(
        "comment_count"
    )

    if comment_count is None:
        edge = media_object.get(
            "edge_media_to_comment"
        )

        if isinstance(edge, dict):
            comment_count = edge.get(
                "count",
                0
            )

    return {
        "id": str(
            media_object.get("pk")
            or media_object.get("id")
            or ""
        ),

        "code": (
            media_object.get("code")
            or media_object.get("shortcode")
            or ""
        ),

        "type": (
            media_object.get("media_type")
            or media_object.get("product_type")
            or ""
        ),

        "caption": caption or "",

        "image_url": image_url,

        "video_url": (
            media_object.get("video_url")
            or ""
        ),

        "like_count": like_count or 0,

        "comment_count": comment_count or 0,

        "taken_at": (
            media_object.get("taken_at")
            or media_object.get("timestamp")
            or 0
        ),

        "location": (
            media_object.get("location")
            or {}
        ),

        "raw": media_object,
    }


async def fetch_profile_bundle(
    username: str,
) -> dict:

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SECONDS
    ) as client:

        profile_body = await rocket_post(
            client,
            "instagram/user/get_web_profile_info",
            {
                "username": username
            },
        )

        user = profile_user(
            profile_body
        )

        if not user or not user.get("id"):
            raise RocketAPIError(
                "این یوزرنیم پیدا نشد.",
                404
            )

        uid = int(user["id"])

        jobs = {
            "info": rocket_post(
                client,
                "instagram/user/get_info_by_id",
                {"id": uid},
            ),

            "media": rocket_post(
                client,
                "instagram/user/get_media",
                {
                    "id": uid,
                    "count": 12,
                },
            ),

            "clips": rocket_post(
                client,
                "instagram/user/get_clips",
                {
                    "id": uid,
                    "count": 12,
                },
            ),

            "followers": rocket_post(
                client,
                "instagram/user/get_followers",
                {
                    "id": uid,
                    "count": 50,
                },
            ),

            "following": rocket_post(
                client,
                "instagram/user/get_following",
                {
                    "id": uid,
                    "count": 50,
                },
            ),

            "stories": rocket_post(
                client,
                "instagram/user/get_stories",
                {
                    "ids": [uid],
                },
            ),

            "highlights": rocket_post(
                client,
                "instagram/user/get_highlights",
                {
                    "id": uid,
                },
            ),

            "similar": rocket_post(
                client,
                "instagram/user/get_similar_accounts",
                {
                    "id": uid,
                },
            ),

            "about": rocket_post(
                client,
                "instagram/user/get_about",
                {
                    "id": uid,
                },
            ),

            "tags": rocket_post(
                client,
                "instagram/user/get_tags",
                {
                    "id": uid,
                    "count": 50,
                },
            ),

            "guides": rocket_post(
                client,
                "instagram/user/get_guides",
                {
                    "id": uid,
                },
            ),

            "live": rocket_post(
                client,
                "instagram/user/get_live",
                {
                    "id": uid,
                },
            ),
        }

        names = list(jobs)

        results = await asyncio.gather(
            *(jobs[name] for name in names),
            return_exceptions=True,
        )

        output = {
            "profile": normalize_user(user),
            "sections": {},
            "errors": {},
        }

        for name, result in zip(
            names,
            results,
        ):

            if isinstance(result, Exception):
                output["errors"][name] = str(result)
                continue

            output["sections"][name] = result

        if "info" in output["sections"]:

            rich = profile_user(
                output["sections"]["info"]
            )

            if rich:
                output["profile"] = {
                    **output["profile"],
                    **normalize_user(rich),
                }

        return output


@app.get("/")
def index():
    return render_template(
        "index.html"
    )


@app.get("/api/profile")
def api_profile():

    username = clean_username(
        request.args.get(
            "username",
            ""
        )
    )

    if not username:
        return jsonify({
            "error": "یک یوزرنیم وارد کن."
        }), 400

    try:

        data = asyncio.run(
            fetch_profile_bundle(
                username
            )
        )

        return jsonify(data)

    except RocketAPIError as e:

        return jsonify({
            "error": e.message
        }), e.status

    except Exception as e:

        return jsonify({
            "error": (
                f"خطای غیرمنتظره: {e}"
            )
        }), 502


@app.post("/api/rocket")
def api_rocket():

    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    path = str(
        payload.pop(
            "path",
            ""
        )
    ).strip().lstrip("/")

    if not path.startswith(
        "instagram/"
    ):
        return jsonify({
            "error": "endpoint نامعتبر است."
        }), 400

    allowed = {
        "instagram/media/get_info",
        "instagram/media/get_info_by_shortcode",
        "instagram/media/get_likes",
        "instagram/media/get_likes_by_id",
        "instagram/media/get_comments",
        "instagram/comment/get_likes",
        "instagram/comment/get_replies",
        "instagram/highlight/get_stories",
        "instagram/media/get_shortcode_by_id",
        "instagram/media/get_id_by_shortcode",
    }

    if path not in allowed:
        return jsonify({
            "error": (
                "این endpoint در این پنل فعال نیست."
            )
        }), 403

    try:

        async def run():

            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS
            ) as client:

                return await rocket_post(
                    client,
                    path,
                    payload,
                )

        return jsonify(
            asyncio.run(run())
        )

    except RocketAPIError as e:

        return jsonify({
            "error": e.message
        }), e.status

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 502


if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "8080"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )

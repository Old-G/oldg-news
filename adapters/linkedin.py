"""LinkedIn: the owner's post in his own profile, by his press of «Опубликовать» (spec: «Бот»).

Share on LinkedIn (scope w_member_social) for posts and images, OpenID Connect (openid profile) for who he is.
The token is made in the Developer Portal (OAuth Token Tools) and sent to the bot; it lives 60 days.
"""

import re

import httpx

API = "https://api.linkedin.com"
# Little Text Format: these characters are markup in `commentary`; unescaped, a post is cut or rejected
_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")
_URL_LINE = re.compile(r"\n*\s*https?://\S+\s*$")


class LinkedInError(RuntimeError):
    pass


def escape(text: str) -> str:
    return _RESERVED.sub(r"\\\1", text)


def without_trailing_links(text: str) -> str:
    """The post without bare links on its last lines: the link goes under the post as a card."""
    while (m := _URL_LINE.search(text)) and m.start() > 0:
        text = text[:m.start()]
    return text.strip()


def post_body(author: str, text: str, link: str | None, title: str, image_urn: str | None) -> dict:
    """The /rest/posts request: the text, and under it the link's card with the picture, or the picture alone."""
    body = {
        "author": author,
        "commentary": escape(without_trailing_links(text)),
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if link:
        article = {"source": link, "title": title[:200]}
        if image_urn:
            article["thumbnail"] = image_urn
        body["content"] = {"article": article}
    elif image_urn:
        body["content"] = {"media": {"id": image_urn, "altText": title[:120]}}
    return body


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


class LinkedIn:
    def __init__(self, http: httpx.AsyncClient, token: str, version: str):
        self.http = http
        self.auth = {"Authorization": f"Bearer {token}"}
        self.rest = {**self.auth, "LinkedIn-Version": version, "X-Restli-Protocol-Version": "2.0.0"}

    async def _check(self, r: httpx.Response, what: str) -> httpx.Response:
        if r.status_code >= 400:
            raise LinkedInError(f"{what}: HTTP {r.status_code} {r.text[:300]}")
        return r

    async def whoami(self) -> tuple[str, str]:
        """(urn:li:person:…, name) of the token's owner."""
        r = await self._check(await self.http.get(f"{API}/v2/userinfo", headers=self.auth), "userinfo")
        me = r.json()
        return f"urn:li:person:{me['sub']}", me.get("name") or ""

    async def upload_image(self, owner: str, data: bytes) -> str:
        r = await self._check(await self.http.post(
            f"{API}/rest/images?action=initializeUpload", headers=self.rest,
            json={"initializeUploadRequest": {"owner": owner}}), "image init")
        value = r.json()["value"]
        await self._check(await self.http.put(value["uploadUrl"], headers=self.auth, content=data), "image upload")
        return value["image"]

    async def publish(self, body: dict) -> str:
        """The new post's urn."""
        r = await self._check(await self.http.post(f"{API}/rest/posts", headers=self.rest, json=body), "post")
        urn = r.headers.get("x-restli-id")
        if not urn:
            raise LinkedInError("post: no x-restli-id in the answer")
        return urn

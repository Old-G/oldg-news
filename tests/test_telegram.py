from datetime import datetime, timezone
from types import SimpleNamespace

from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl, PeerChannel

from adapters.telegram import extract_urls, messages_to_items

DATE = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)


def msg(id, text="", entities=None, grouped_id=None, media=None, fwd_from=None):
    return SimpleNamespace(
        id=id, message=text, entities=entities, grouped_id=grouped_id, media=media,
        fwd_from=fwd_from, date=DATE, views=10, forwards=1,
    )


def test_hidden_link_goes_to_text_and_url():
    m = msg(1, "Вышел новый релиз", [MessageEntityTextUrl(offset=10, length=5, url="https://github.com/a/b")])
    (item,), skipped = messages_to_items([m], -1001, "chan")
    assert skipped == 0
    assert item.url == "https://github.com/a/b"
    assert item.text == "Вышел новый релиз\nhttps://github.com/a/b"
    assert item.raw["link"] == "https://t.me/chan/1"


def test_plain_url_offsets_are_utf16():
    text = "🔥 см. example.com/x"  # the emoji is 2 UTF-16 code units
    m = msg(1, text, [MessageEntityUrl(offset=7, length=13)])
    assert extract_urls(m) == ["https://example.com/x"]


def test_web_preview_url_is_kept():
    m = msg(1, "без ссылок в тексте", media=SimpleNamespace(webpage=SimpleNamespace(url="https://a.dev/p")))
    assert extract_urls(m) == ["https://a.dev/p"]


def test_album_becomes_one_item():
    parts = [msg(7, grouped_id=42, media=True), msg(5, "подпись к альбому", grouped_id=42, media=True), msg(6, grouped_id=42, media=True)]
    (item,), skipped = messages_to_items(parts, -1001, None)
    assert skipped == 0
    assert item.external_id == "5"
    assert item.raw["message_ids"] == [5, 6, 7]
    assert item.text == "подпись к альбому"
    assert item.raw["link"] is None


def test_media_without_text_is_skipped():
    items, skipped = messages_to_items([msg(1, media=True), msg(2, "текст")], -1001, None)
    assert [i.external_id for i in items] == ["2"]
    assert skipped == 1


def test_forwarded_channel_is_recorded():
    fwd = SimpleNamespace(from_id=PeerChannel(channel_id=555), channel_post=99)
    (item,), _ = messages_to_items([msg(1, "переслано", fwd_from=fwd)], -1001, None)
    assert item.raw["fwd_from_channel_id"] == 555
    assert item.raw["fwd_from_post_id"] == 99

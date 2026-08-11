"""강의 페이지에서 Cloudflare 영상 주소를 찾는 규칙을 검증한다."""

from __future__ import annotations

from pudufu.client import PuduFuClient


class _Response:
    text = (
        '<iframe src="https://iframe.cloudflarestream.com/'
        '0123456789abcdef0123456789abcdef/iframe"></iframe>'
        '<iframe src="https://customer-example.cloudflarestream.com/'
        '0123456789abcdef0123456789abcdef/iframe"></iframe>'
    )

    def raise_for_status(self) -> None:
        pass


class _Session:
    def __init__(self, *responses: _Response) -> None:
        self.responses = list(responses) or [_Response()]

    def get(self, url: str, timeout: int) -> _Response:
        return self.responses.pop(0)


def test_get_video_source_builds_mp4_and_hls_urls_from_embed_host() -> None:
    client = PuduFuClient()
    client.session = _Session()  # type: ignore[assignment]

    source = client.get_video_source("course", "lesson")

    assert source is not None
    assert source.uid == "0123456789abcdef0123456789abcdef"
    assert source.mp4_url == (
        "https://customer-example.cloudflarestream.com/"
        "0123456789abcdef0123456789abcdef/downloads/default.mp4"
    )
    assert source.hls_url == (
        "https://customer-example.cloudflarestream.com/"
        "0123456789abcdef0123456789abcdef/manifest/video.m3u8"
    )


def test_get_video_source_does_not_take_uid_from_unrelated_customer_url() -> None:
    client = PuduFuClient()
    response = _Response()
    response.text = (
        '<iframe src="https://iframe.cloudflarestream.com/'
        '0123456789abcdef0123456789abcdef/iframe"></iframe>'
        "https://customer-unrelated.cloudflarestream.com/"
        "ffffffffffffffffffffffffffffffff/iframe"
    )
    player_response = _Response()
    player_response.text = "https://customer-correct.cloudflarestream.com/player"
    client.session = _Session(response, player_response)  # type: ignore[assignment]

    source = client.get_video_source("course", "lesson")

    assert source is not None
    assert source.uid == "0123456789abcdef0123456789abcdef"
    assert source.mp4_url.startswith("https://customer-correct.cloudflarestream.com/")


def test_get_video_source_uses_delivery_host_when_customer_host_is_absent() -> None:
    client = PuduFuClient()
    lecture_response = _Response()
    lecture_response.text = (
        '<iframe src="https://iframe.cloudflarestream.com/'
        '0123456789abcdef0123456789abcdef/iframe"></iframe>'
    )
    player_response = _Response()
    player_response.text = "https://customer-player.cloudflarestream.com/some-player-asset"
    client.session = _Session(lecture_response, player_response)  # type: ignore[assignment]

    source = client.get_video_source("course", "lesson")

    assert source is not None
    assert source.mp4_url.startswith("https://customer-player.cloudflarestream.com/")


def test_get_video_source_recognizes_youtube_iframe() -> None:
    client = PuduFuClient()
    response = _Response()
    response.text = '<iframe src="https://www.youtube.com/embed/auFRYPDpiMQ"></iframe>'
    client.session = _Session(response)  # type: ignore[assignment]

    source = client.get_video_source("course", "lesson")

    assert source is not None
    assert source.uid == "auFRYPDpiMQ"
    assert source.mp4_url is None
    assert source.hls_url is None
    assert source.youtube_url == "https://www.youtube.com/watch?v=auFRYPDpiMQ"

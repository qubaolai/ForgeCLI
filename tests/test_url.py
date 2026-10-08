"""URL、DNS 和公网地址检查均不能被本地地址的其他写法绕过."""

import asyncio
import socket

import pytest

from forgecli.tools.builtin.WebFetch import domain_check as checker


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.1/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/",
        "http://100.64.1.1/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fe80::1%25en0]/",
        "http://[ff02::1]/",
        "http://localhost/",
        "http://LOCALHOST./",
        "http://box.local/",
        "http://intranet/",
        "ftp://example.com/",
        "file:///etc/passwd",
        "https://u:p@example.com/",
        "https://example.com:8765/",
        "https://example.com:99999/",
        "https://%31%32%37.0.0.1/",
        "https://example.com\\@127.0.0.1/",
        "https://example.com/\nhello",
        "https://example.com/?access_token=secret",
        "https://example.com/?%61pi_key=secret",
        "https://example.com/?X-Amz-Signature=secret",
        "",
        "https:///missing",
    ],
)
def test_reject_unsafe_urls(url):
    with pytest.raises(ValueError):
        checker.normalize_url(url)


def test_normalize_and_domain_boundaries(monkeypatch):
    assert (
        checker.normalize_url(" HTTPS://Example.COM./docs?q=x#part ")
        == "https://example.com/docs?q=x"
    )
    assert checker.normalize_url("https://例子.中国/").startswith("https://xn--")
    assert checker.domain_matches("a.example.com", "*.example.com")
    assert not checker.domain_matches("example.com", "*.example.com")
    assert not checker.domain_matches("notexample.com", "example.com")
    monkeypatch.setattr(checker, "BLOCK_DOMAIN", ("*.example.com",))
    with pytest.raises(ValueError, match="blocked"):
        checker.normalize_url("https://a.example.com/")


@pytest.mark.parametrize(
    "addresses", [["93.184.216.34"], ["93.184.216.34", "127.0.0.1"], []]
)
def test_dns_all_addresses_must_be_public(monkeypatch, addresses):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses
        ],
    )
    if addresses == ["93.184.216.34"]:
        assert asyncio.run(checker.resolve_and_validate("example.com", 443)) == set(
            addresses
        )
    else:
        with pytest.raises(ValueError):
            asyncio.run(checker.validate_url("https://example.com"))

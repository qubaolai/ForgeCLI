"""本地正文提取与 HTML -> Markdown; 不执行脚本, 不下载页面子资源."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from markdownify import MarkdownConverter

_DROP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "nav",
    "aside",
    "footer",
    "form",
    "button",
    "input",
    "select",
    "textarea",
    "svg",
    "canvas",
    "iframe",
    "object",
    "embed",
    "head",
}
_DROP_ROLES = {"navigation", "banner", "complementary", "contentinfo", "dialog"}
_NOISE = {
    "ad",
    "ads",
    "advertisement",
    "advertising",
    "ad-banner",
    "cookie-banner",
    "cookie-consent",
    "newsletter",
    "social-share",
}
_MAX_NODES = 50_000


def _language(element: Tag) -> str | None:
    for tag in (element, element.find("code")):
        if isinstance(tag, Tag):
            for name in tag.get_attribute_list("class"):
                if isinstance(name, str) and name.startswith(("language-", "lang-")):
                    language = name.split("-", 1)[1]
                    if re.fullmatch(r"[\w+-]{1,30}", language):
                        return language
    return None


class _Converter(MarkdownConverter):
    def convert_pre(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        # 正文中的 Markdown 示例可能自带 ```; 围栏必须比内容中的反引号串更长.
        code = el.get_text().strip("\n")
        fence = "`" * max(
            3, max((len(run) for run in re.findall(r"`+", code)), default=0) + 1
        )
        return f"\n\n{fence}{_language(el) or ''}\n{code}\n{fence}\n\n"


def html_to_markdown(
    body: bytes, url: str, *, encoding: str | None = None
) -> tuple[str, str]:
    """优先 main/article 正文, 清理明确噪音; 保留技术文档的表格、链接和代码."""
    soup = BeautifulSoup(body, "html.parser", from_encoding=encoding)
    nodes = soup.find_all(True)
    if len(nodes) > _MAX_NODES:
        raise ValueError("WebFetch page contains too many HTML elements")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in nodes:
        # 父节点删除时, 其子节点可能已失去 attrs.
        if not tag.attrs or tag.parent is None:
            if tag.name in _DROP_TAGS and tag.parent is not None:
                tag.decompose()
            continue
        classes = set(tag.get_attribute_list("class"))
        style = re.sub(r"\s+", "", str(tag.get("style", "")).lower())
        if (
            tag.name in _DROP_TAGS
            or tag.get("role") in _DROP_ROLES
            or tag.has_attr("hidden")
            or str(tag.get("aria-hidden", "")).lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
            or classes & _NOISE
            or tag.get("id") in _NOISE
        ):
            tag.decompose()
    for image in soup.find_all("img"):
        image.replace_with(str(image.get("alt", "")))
    for link in soup.find_all("a"):
        href = link.get("href")
        if not isinstance(href, str):
            continue
        target = urljoin(url, href)
        try:
            parsed = urlsplit(target)
            safe = (
                parsed.scheme in ("http", "https")
                and parsed.hostname
                and not parsed.username
                and not parsed.password
            )
        except ValueError:
            safe = False
        if safe:
            link["href"] = target
        else:
            del link["href"]
    # 优先主体容器, 避免把多个文章中的第一个误当整个页面.
    root = soup.find("main") or soup.find(None, attrs={"role": "main"})
    articles = soup.find_all("article")
    if root is None:
        root = articles[0] if len(articles) == 1 else (soup.body or soup)
    converter = _Converter(
        heading_style="ATX",
        bullets="-",
        autolinks=False,
        code_language_callback=_language,
        strip=["img"],
    )
    try:
        markdown = converter.convert(str(root)).strip()
    except RecursionError as exc:
        raise ValueError("WebFetch page HTML nesting is too deep") from exc
    if not markdown:
        raise ValueError(
            "WebFetch found no readable HTML; page may require JavaScript or login"
        )
    return title, markdown

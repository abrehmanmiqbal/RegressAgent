"""The static project site (docs/index.html, served by GitHub Pages): no scripts, no external
resources, honest status."""
from html.parser import HTMLParser
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
PAGE = (DOCS / "index.html").read_text(encoding="utf-8")
REPO = "https://github.com/abrehmanmiqbal/RegressAgent"


class _Audit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.problems, self.links = [], []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "iframe", "object", "embed", "form"):
            self.problems.append(tag)
        for key, value in attrs:
            if key.startswith("on") or key == "style":
                self.problems.append(f"{tag}[{key}]")
            if key in ("src", "href") and value:
                self.links.append((tag, value))


def test_site_files_exist():
    assert (DOCS / "site.css").is_file() and (DOCS / ".nojekyll").is_file()


def test_no_scripts_forms_inline_styles_or_third_party_resources():
    audit = _Audit()
    audit.feed(PAGE)
    assert audit.problems == []
    for tag, link in audit.links:
        local = link.startswith(("#", "site.css"))
        repo = link == REPO or link.startswith(REPO + "/")
        assert local or repo, f"unexpected link: {link}"
        if tag in ("link", "img", "source"):
            assert local, f"external resource: {link}"


def test_the_site_is_honest():
    lowered = PAGE.lower()
    assert "alpha" in lowered and "not on pypi" in lowered
    assert "pip install regressagent" not in lowered  # that does not work yet
    assert "real browser" in lowered  # the UI limitation is stated
    assert "not a sandbox" in lowered  # Tier A is not oversold
    assert "regressed" in PAGE and "tool_order" in PAGE  # the real captured output is shown

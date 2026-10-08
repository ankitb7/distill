from markdown_it import MarkdownIt


def render_digest_markdown(markdown: str) -> str:
    """Render stored Markdown without executing embedded HTML or unsafe links."""
    parser = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable("table")
    tokens = parser.parse(markdown)
    for token in tokens:
        for child in token.children or []:
            if child.type == "link_open":
                child.attrSet("target", "_blank")
                child.attrSet("rel", "noopener noreferrer")
    return parser.renderer.render(tokens, parser.options, {})

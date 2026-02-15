from datetime import datetime
from xml.etree import ElementTree

import httpx

from ai_pulse.models import CollectedArticle, Source

ARXIV_API_URL = "http://export.arxiv.org/api/query"

ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


class ArxivCollector:
    source_name = "arxiv"

    async def collect(self, config: dict) -> list[CollectedArticle]:
        arxiv_config = config.get("sources", {}).get("arxiv", {})
        if not arxiv_config.get("enabled", False):
            return []

        categories = arxiv_config.get("categories", ["cs.AI", "cs.LG", "cs.CL"])
        keywords = arxiv_config.get("keywords", ["large language model", "LLM"])
        max_results = arxiv_config.get("max_results", 20)

        cat_query = " OR ".join(f"cat:{c}" for c in categories)
        kw_query = " OR ".join(f'ti:"{k}" OR abs:"{k}"' for k in keywords)
        query = f"({cat_query}) AND ({kw_query})"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                ARXIV_API_URL,
                params={
                    "search_query": query,
                    "start": 0,
                    "max_results": max_results,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                },
            )
            resp.raise_for_status()

        return self._parse_feed(resp.text)

    def _parse_feed(self, xml_text: str) -> list[CollectedArticle]:
        root = ElementTree.fromstring(xml_text)
        articles = []

        for entry in root.findall(f"{ATOM_NS}entry"):
            article = self._parse_entry(entry)
            if article:
                articles.append(article)

        return articles

    def _parse_entry(self, entry: ElementTree.Element) -> CollectedArticle | None:
        id_elem = entry.find(f"{ATOM_NS}id")
        title_elem = entry.find(f"{ATOM_NS}title")
        if id_elem is None or title_elem is None:
            return None

        arxiv_id = (id_elem.text or "").strip()
        url = arxiv_id.replace("http://", "https://").replace("/abs/", "/pdf/")
        if "/pdf/" not in url:
            url = arxiv_id

        title = " ".join((title_elem.text or "").split())

        authors = []
        for author in entry.findall(f"{ATOM_NS}author"):
            name = author.find(f"{ATOM_NS}name")
            if name is not None and name.text:
                authors.append(name.text.strip())

        published = None
        pub_elem = entry.find(f"{ATOM_NS}published")
        if pub_elem is not None and pub_elem.text:
            try:
                published = datetime.fromisoformat(pub_elem.text.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        summary_elem = entry.find(f"{ATOM_NS}summary")
        summary = " ".join((summary_elem.text or "").split()) if summary_elem is not None else ""

        categories = []
        for cat in entry.findall(f"{ARXIV_NS}primary_category") + entry.findall(
            f"{ATOM_NS}category"
        ):
            term = cat.get("term")
            if term:
                categories.append(term)

        return CollectedArticle(
            url=url,
            title=title,
            author=", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
            source=Source.ARXIV,
            source_id=arxiv_id,
            published_at=published,
            tags=categories,
            summary=summary[:500],
        )

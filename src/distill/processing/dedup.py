from difflib import SequenceMatcher

from distill.db import Database
from distill.models import Article


def _choose_canonical(left: Article, right: Article) -> tuple[Article, Article]:
    """Prefer the Article with the richest evidence, then engagement, then stable age."""

    def quality(article: Article) -> tuple[int, int, int, int]:
        return (
            article.content_length or len(article.content_text or ""),
            len(article.summary or ""),
            (article.points or 0) + (article.comment_count or 0),
            -article.id,
        )

    canonical = max((left, right), key=quality)
    return canonical, right if canonical is left else left


def run_title_dedup(db: Database, threshold: float = 0.85) -> int:
    articles = db.get_all_articles(non_duplicate_only=True)
    marked = 0

    for i, a in enumerate(articles):
        if a.is_duplicate:
            continue
        for b in articles[i + 1 :]:
            if b.is_duplicate:
                continue
            ratio = SequenceMatcher(None, a.title.lower(), b.title.lower()).ratio()
            if ratio >= threshold:
                canonical, duplicate = _choose_canonical(a, b)
                db.mark_duplicate(duplicate.id, canonical.id, ratio, "title_similarity")
                marked += 1

    return marked


def run_embedding_dedup(
    db: Database, model_name: str = "all-MiniLM-L6-v2", threshold: float = 0.88
) -> int:
    from sentence_transformers import SentenceTransformer

    articles = db.get_all_articles(non_duplicate_only=True)
    if len(articles) < 2:
        return 0

    texts = []
    for a in articles:
        text = a.title
        if a.content_text:
            text += " " + a.content_text[:500]
        texts.append(text)

    model = SentenceTransformer(model_name)
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    db.save_embeddings({article.id: embeddings[i].tobytes() for i, article in enumerate(articles)})

    # Cosine similarity (embeddings are already normalized, so dot product = cosine)
    sim_matrix = embeddings @ embeddings.T
    marked = 0
    skip_ids: set[int] = set()

    for i in range(len(articles)):
        if articles[i].id in skip_ids:
            continue
        for j in range(i + 1, len(articles)):
            if articles[j].id in skip_ids:
                continue
            similarity = float(sim_matrix[i][j])
            if similarity >= threshold:
                canonical, duplicate = _choose_canonical(articles[i], articles[j])
                db.mark_duplicate(duplicate.id, canonical.id, similarity, "embedding_cosine")
                skip_ids.add(duplicate.id)
                marked += 1

    return marked

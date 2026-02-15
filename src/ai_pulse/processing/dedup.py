from difflib import SequenceMatcher

from ai_pulse.db import Database


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
                canonical = a if a.id < b.id else b
                duplicate = b if a.id < b.id else a
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

    # Store embeddings in DB
    for i, article in enumerate(articles):
        emb_bytes = embeddings[i].tobytes()
        db.conn.execute(
            "UPDATE articles SET embedding = ? WHERE id = ?",
            (emb_bytes, article.id),
        )
    db.conn.commit()

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
                canonical = articles[i] if articles[i].id < articles[j].id else articles[j]
                duplicate = articles[j] if articles[i].id < articles[j].id else articles[i]
                db.mark_duplicate(duplicate.id, canonical.id, similarity, "embedding_cosine")
                skip_ids.add(duplicate.id)
                marked += 1

    return marked

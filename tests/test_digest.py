import tempfile
from pathlib import Path

from ai_pulse.db import Database
from ai_pulse.models import CollectedArticle, ScoreBreakdown, Source
from ai_pulse.outputs.digest import generate_digest


def test_generate_digest_with_articles():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    db.init_schema()

    for i in range(5):
        article = CollectedArticle(
            url=f"https://example.com/digest-test-{i}",
            title=f"Digest Test Article {i}",
            author=f"Author {i}",
            source=Source.HACKERNEWS,
            points=100 - i * 10,
        )
        aid = db.insert_article(article)
        db.update_content(aid, f"Content for article {i}. " * 20)
        score = ScoreBreakdown(
            engagement_score=0.8 - i * 0.1,
            technical_depth=0.7,
            novelty=0.6,
            applicability=0.9,
            composite_score=0.75 - i * 0.05,
            reasoning=f"Reason for article {i}",
        )
        db.insert_score(aid, score)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        path = generate_digest(db, output_dir, top_n=5)
        assert path.exists()
        content = path.read_text()
        assert "Distill Digest" in content
        assert "Digest Test Article 0" in content
        assert "Author 0" in content
        assert "0.75" in content

    db.close()


def test_generate_digest_empty_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    db.init_schema()

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        path = generate_digest(db, output_dir, top_n=5)
        assert path.exists()
        content = path.read_text()
        assert "0 top articles" in content

    db.close()

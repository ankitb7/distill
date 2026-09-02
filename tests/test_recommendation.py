from distill.models import CollectedArticle, ScoreBreakdown, Source
from distill.processing.recommendation import ReadingSlateRequest, select_reading_slate


def _insert_assessed(tmp_db, index: int, domain: str, score: float, source=Source.RSS) -> int:
    article_id = tmp_db.insert_article(
        CollectedArticle(
            url=f"https://{domain}/article-{index}",
            title=f"Article {index} about agent migration pipelines",
            source=source,
            content_text=f"Detailed implementation evidence {index} " * 20,
            content_length=700,
        )
    )
    tmp_db.insert_score(
        article_id,
        ScoreBreakdown(composite_score=score, score_version="v1", status="success"),
    )
    return article_id


def test_reading_slate_filters_noise_and_caps_domains(tmp_db):
    first = _insert_assessed(tmp_db, 1, "repetitive.example", 0.9)
    second = _insert_assessed(tmp_db, 2, "repetitive.example", 0.8)
    _insert_assessed(tmp_db, 3, "repetitive.example", 0.7)
    distinct = _insert_assessed(tmp_db, 4, "distinct.example", 0.65)
    _insert_assessed(tmp_db, 5, "noise.example", 0.2)

    slate = select_reading_slate(
        tmp_db,
        {
            "recommendation": {
                "minimum_score": 0.35,
                "max_per_domain": 2,
                "max_per_source": 10,
            }
        },
        ReadingSlateRequest(limit=5, exclude_last_week=False),
    )

    ids = [article.id for article, _ in slate]
    assert ids == [first, second, distinct]


def test_reading_slate_excludes_failed_assessments(tmp_db):
    successful = _insert_assessed(tmp_db, 1, "success.example", 0.6)
    failed = _insert_assessed(tmp_db, 2, "failed.example", 0.9)
    tmp_db.insert_score(
        failed,
        ScoreBreakdown(composite_score=0.9, score_version="v1", status="failed"),
    )

    slate = select_reading_slate(
        tmp_db,
        {"recommendation": {"minimum_score": 0}},
        ReadingSlateRequest(limit=5, exclude_last_week=False),
    )

    assert [article.id for article, _ in slate] == [successful]


def test_reading_slate_requires_each_personal_quality_gate(tmp_db):
    useful = _insert_assessed(tmp_db, 1, "useful.example", 0.8)
    irrelevant = _insert_assessed(tmp_db, 2, "irrelevant.example", 0.8)
    tmp_db.insert_score(
        useful,
        ScoreBreakdown(
            composite_score=0.8,
            relevance=0.8,
            applicability=0.8,
            evidence_quality=0.7,
            noise_penalty=0.1,
            score_version="v1",
            status="success",
        ),
    )
    tmp_db.insert_score(
        irrelevant,
        ScoreBreakdown(
            composite_score=0.8,
            relevance=0.1,
            applicability=0.8,
            evidence_quality=0.9,
            noise_penalty=0,
            score_version="v1",
            status="success",
        ),
    )

    slate = select_reading_slate(
        tmp_db,
        {
            "recommendation": {
                "minimum_score": 0.35,
                "minimum_relevance": 0.6,
                "minimum_applicability": 0.5,
                "minimum_evidence_quality": 0.4,
                "maximum_noise_penalty": 0.45,
            }
        },
        ReadingSlateRequest(limit=5, exclude_last_week=False),
    )

    assert [article.id for article, _ in slate] == [useful]


def test_reading_slate_backfills_requested_limit_with_relevant_articles(tmp_db):
    qualified = _insert_assessed(tmp_db, 1, "qualified.example", 0.8)
    fallback = _insert_assessed(tmp_db, 2, "qualified.example", 0.3)
    unrelated = _insert_assessed(tmp_db, 3, "unrelated.example", 0.7)
    tmp_db.insert_score(
        qualified,
        ScoreBreakdown(
            composite_score=0.8,
            relevance=0.8,
            applicability=0.8,
            evidence_quality=0.8,
            score_version="v1",
            status="success",
        ),
    )
    tmp_db.insert_score(
        fallback,
        ScoreBreakdown(
            composite_score=0.3,
            relevance=0.6,
            applicability=0.4,
            evidence_quality=0.3,
            score_version="v1",
            status="success",
        ),
    )
    tmp_db.insert_score(
        unrelated,
        ScoreBreakdown(
            composite_score=0.7,
            relevance=0.1,
            applicability=0.8,
            evidence_quality=0.8,
            score_version="v1",
            status="success",
        ),
    )

    slate = select_reading_slate(
        tmp_db,
        {
            "recommendation": {
                "minimum_score": 0.35,
                "minimum_relevance": 0.6,
                "minimum_applicability": 0.5,
                "minimum_evidence_quality": 0.4,
                "fill_to_limit": True,
                "fallback_minimum_relevance": 0.4,
                "max_per_domain": 1,
            }
        },
        ReadingSlateRequest(limit=2, exclude_last_week=False),
    )

    assert [article.id for article, _ in slate] == [qualified, fallback]

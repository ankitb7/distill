from unittest.mock import AsyncMock, patch

import pytest

from distill.models import CollectedArticle, Source
from distill.outputs.podcast import generate_podcast
from distill.outputs.podcast_providers import _parse_script
from distill.outputs.podcast_titles import episode_title, extract_title
from distill.outputs.podcastfy_worker import clean_dialogue


def test_title_metadata_is_not_spoken():
    assert extract_title('TITLE: "When agents need to prove their work"\n[Alex] Hello.') == (
        "When agents need to prove their work"
    )
    assert _parse_script("TITLE: Evidence before approval\n[Alex] Hello.\n[Sarah] Yes.") == [
        ("alex", "Hello."),
        ("sarah", "Yes."),
    ]
    dialogue = "<Person1>Hello.</Person1><Person2>Yes.</Person2>"
    assert clean_dialogue("TITLE: Evidence before approval\n" + dialogue) == (
        "<Person1>Hello.</Person1>\n<Person2>Yes.</Person2>"
    )


@pytest.mark.parametrize(
    "script",
    [
        "[Alex] Hello.\nTITLE: This is part of the dialogue",
        "<Person1>Hello</Person1>\nTITLE: Spoken text",
        "TITLE:\nNot a metadata line",
        "TITLE: <script>unsafe</script>",
        "TITLE: " + "x" * 101,
        "TITLE: Hi",
    ],
)
def test_invalid_or_spoken_title_is_ignored(script):
    assert extract_title(script) is None


def test_missing_title_has_readable_fallback(tmp_path):
    missing = tmp_path / "missing.md"
    assert episode_title(missing, ["<b>Reliable agents</b>"]) == "Reliable agents"
    assert episode_title(missing, []) == "AI engineering briefing"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_name,extension", [("gemini-api-tts", "md"), ("podcastfy-edge", "txt")]
)
async def test_generated_title_is_saved_from_current_providers_script(
    tmp_db, tmp_path, provider_name, extension
):
    aid = tmp_db.insert_article(
        CollectedArticle(
            title="Article",
            url="https://example.com/title",
            source=Source.RSS,
            content_text="content " * 100,
        )
    )

    async def render(source, output_dir, on_status):
        script = output_dir / f"podcast-script-{source.label}.{extension}"
        script.write_text("TITLE: Evidence before approval\n[Alex] Hello.\n[Sarah] Yes.")
        other = "txt" if extension == "md" else "md"
        (output_dir / f"podcast-script-{source.label}.{other}").write_text("TITLE: Stale title")
        audio = output_dir / "episode.mp3"
        audio.write_bytes(b"audio")
        return audio

    provider = AsyncMock()
    provider.generate.side_effect = render
    with patch("distill.outputs.podcast.get_podcast_provider", return_value=provider):
        await generate_podcast(
            tmp_db, {"podcast": {"provider": provider_name}}, tmp_path, article_ids=[aid]
        )
    episode = tmp_db.list_podcasts()[0]
    assert episode.podcast_title == "Evidence before approval"
    assert tmp_db.get_podcast_articles(episode.week_label)[0].url == "https://example.com/title"

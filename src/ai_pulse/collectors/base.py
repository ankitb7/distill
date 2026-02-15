from typing import Protocol

from ai_pulse.models import CollectedArticle


class Collector(Protocol):
    source_name: str

    async def collect(self, config: dict) -> list[CollectedArticle]: ...

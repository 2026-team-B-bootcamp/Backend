from abc import ABC, abstractmethod


class IcebreakerProvider(ABC):
    @abstractmethod
    def generate_icebreaker(self, target_name: str, tags: list[str]) -> str:
        """Return a conversation-starter question about `target_name` given their tags."""
        raise NotImplementedError

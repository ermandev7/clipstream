from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0

    @property
    def ends_sentence(self) -> bool:
        return self.text.rstrip().endswith((".", "?", "!", "…", "?»", "!»", '."'))


@dataclass
class Transcript:
    language: str = "es"
    words: list[Word] = field(default_factory=list)

    def slice(self, start: float, end: float) -> list[Word]:
        return [w for w in self.words if w.end > start and w.start < end]

    def text_between(self, start: float, end: float) -> str:
        return " ".join(w.text.strip() for w in self.slice(start, end)).strip()

    def shifted(self, offset: float) -> "Transcript":
        return Transcript(self.language, [Word(w.start + offset, w.end + offset, w.text, w.prob) for w in self.words])

    def to_dict(self) -> dict:
        return {"language": self.language, "words": [asdict(w) for w in self.words]}

    @classmethod
    def from_dict(cls, d: dict) -> "Transcript":
        return cls(d.get("language", "es"), [Word(**w) for w in d.get("words", [])])

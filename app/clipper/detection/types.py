from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Candidate:
    """Momento candidato a clip. Tiempos en segundos absolutos de la fuente."""

    start: float
    end: float
    peak: float
    signal_score: float  # 0-100, señales baratas
    signals: dict[str, float] = field(default_factory=dict)  # aporte por señal (0-1)
    llm_score: float | None = None  # 0-100
    llm_reason: str = ""
    title: str = ""
    hook: str = ""
    hashtags: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def score(self) -> float:
        if self.llm_score is None:
            return self.signal_score
        return round(0.4 * self.signal_score + 0.6 * self.llm_score, 1)

    def signals_label(self) -> str:
        """Texto corto para la tabla: 'Chat ×8, risa, gancho'."""
        parts: list[str] = []
        chat_x = self.signals.get("chat_x")
        if chat_x and chat_x >= 1.5:
            parts.append(f"Chat ×{chat_x:.0f}")
        if self.signals.get("audio", 0) >= 0.5:
            parts.append("audio")
        if self.signals.get("laughter", 0) >= 0.5:
            parts.append("risa")
        if self.signals.get("applause", 0) >= 0.5:
            parts.append("aplausos")
        if self.signals.get("scream", 0) >= 0.5:
            parts.append("gritos")
        if self.signals.get("donation", 0) >= 0.3:
            parts.append("donaciones")
        if self.signals.get("sub", 0) >= 0.3:
            parts.append("subs")
        if self.signals.get("viewers", 0) >= 0.5:
            parts.append("espectadores")
        if self.signals.get("keywords", 0) >= 0.5:
            parts.append("palabras clave")
        if self.llm_score is not None and self.llm_score >= 70:
            parts.append("gancho")
        return ", ".join(parts) if parts else "—"

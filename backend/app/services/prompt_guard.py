from app.models.schemas import PromptScanResult


class PromptGuardService:
    _patterns = (
        "ignore previous instructions",
        "send all files",
        "reveal secret",
        "bypass policy",
    )

    def scan_text(self, text: str) -> PromptScanResult:
        lowered = text.lower()
        reasons = [pattern for pattern in self._patterns if pattern in lowered]
        return PromptScanResult(flagged=bool(reasons), reasons=reasons)


prompt_guard_service = PromptGuardService()

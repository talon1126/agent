import os

from app.message_schemas import TranscriptionResult


def transcribe_audio(
    *,
    provider: str,
    model: str,
    audio_url: str | None,
    audio_base64: str | None,
    mime_type: str | None,
) -> TranscriptionResult:
    if provider == "mock":
        return TranscriptionResult(
            provider="mock",
            model=model,
            transcript="Please check order ord_100",
            confidence=0.99,
            metadata={
                "mime_type": mime_type,
                "input": "audio_url" if audio_url else "audio_base64",
            },
        )

    if provider == "qwen":
        endpoint = os.getenv("QWEN_API_ENDPOINT")
        api_key = os.getenv("QWEN_API_KEY")
        if not endpoint or not api_key:
            return TranscriptionResult(
                provider="qwen",
                model=model,
                error="Qwen transcription is not configured. Provide QWEN_API_ENDPOINT and QWEN_API_KEY.",
                metadata={"mime_type": mime_type},
            )
        return TranscriptionResult(
            provider="qwen",
            model=model,
            error="Qwen transcription request shape is pending provider details.",
            metadata={"endpoint": endpoint, "mime_type": mime_type},
        )

    return TranscriptionResult(
        provider=provider,
        model=model,
        error=f"Unsupported transcription provider: {provider}",
    )

import hashlib
from pathlib import Path

from pydantic import BaseModel, Field


ENGLISH_TRANSCRIPTS = [
    "I am feeling okay today, just a little tired and wanted to check in.",
    "I have been feeling a bit stressed this week, but overall I am managing okay.",
    "Today has been better and I mostly feel steady, though I have had a few anxious moments.",
]


class SpeechmaticsVoiceConfig(BaseModel):
    language: str = "en"
    domain: str | None = None
    output_locale: str | None = None
    max_delay: float = 0.7
    enable_diarization: bool = False
    include_partials: bool = False
    additional_vocab: list[str] = Field(default_factory=list)


class SpeechmaticsVoiceTranscript(BaseModel):
    transcript: str
    language: str
    domain: str | None = None
    output_locale: str | None = None
    provider: str = "speechmatics"
    mode: str = "voice_sdk_stub"


def build_voice_config(
    *,
    language: str = "en",
    domain: str | None = None,
    output_locale: str | None = None,
    max_delay: float = 0.7,
    enable_diarization: bool = False,
    include_partials: bool = False,
    additional_vocab: list[str] | None = None,
) -> SpeechmaticsVoiceConfig:
    return SpeechmaticsVoiceConfig(
        language=language,
        domain=domain,
        output_locale=output_locale,
        max_delay=max_delay,
        enable_diarization=enable_diarization,
        include_partials=include_partials,
        additional_vocab=additional_vocab or [],
    )


async def transcribe_with_config(
    audio_file_path: str,
    config: SpeechmaticsVoiceConfig,
) -> SpeechmaticsVoiceTranscript:
    if config.language == "en":
        digest = hashlib.sha256(Path(audio_file_path).name.encode("utf-8")).hexdigest()
        index = int(digest[:8], 16) % len(ENGLISH_TRANSCRIPTS)
        transcript = ENGLISH_TRANSCRIPTS[index]

        if config.additional_vocab:
            transcript = f"{transcript} I also wanted to mention {config.additional_vocab[0]}."
    else:
        transcript = f"[{config.language}] Placeholder transcript for demo voice note."

    return SpeechmaticsVoiceTranscript(
        transcript=transcript,
        language=config.language,
        domain=config.domain,
        output_locale=config.output_locale,
    )


async def transcribe(
    audio_file_path: str,
    language: str = "en",
) -> str:
    transcript = await transcribe_with_config(
        audio_file_path,
        build_voice_config(language=language),
    )
    return transcript.transcript

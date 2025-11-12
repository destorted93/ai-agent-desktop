from pathlib import Path
from typing import Optional, Union, Dict, Any
from datetime import datetime

from openai import OpenAI


class TTSConfig:
    """Lightweight configuration for TTS.

    Parameters:
        model:        TTS model name. Defaults to env `TTS_MODEL` or 'gpt-4o-mini-tts'.
        voice:        Voice preset. Defaults to env `TTS_VOICE` or 'coral'.
        instructions: Optional style/tone instructions for the voice.
        audio_format: Output audio format, e.g., 'mp3' or 'wav'. Defaults to env
                      `TTS_AUDIO_FORMAT` or 'mp3'.
        output_dir:   Default directory for generated files when no file path is
                      provided. Defaults to 'tts/generated' (module-local).
    """

    def __init__(
        self,
        model: Optional[str] = None,
        voice: Optional[str] = None,
        instructions: Optional[str] = None,
        audio_format: Optional[str] = None,
        output_dir: Optional[Union[str, Path]] = None,
    ):
        import os

        self.model = model or os.getenv("TTS_MODEL", "gpt-4o-mini-tts")
        self.voice = voice or os.getenv("TTS_VOICE", "coral")
        self.instructions = instructions or os.getenv("TTS_INSTRUCTIONS")
        self.audio_format = (audio_format or os.getenv("TTS_AUDIO_FORMAT", "mp3")).lower()
        # Resolve default output directory: env -> arg -> module-local 'generated'
        env_output_dir = os.getenv("TTS_OUTPUT_DIR")
        if output_dir is not None:
            self.output_dir = Path(output_dir)
        elif env_output_dir:
            self.output_dir = Path(env_output_dir)
        else:
            # default to 'tts/generated' next to this module
            self.output_dir = Path(__file__).resolve().parent / "generated"


class TTSClient:
    """Simple Text-to-Speech client using the OpenAI Python SDK.

    Designed to be imported by tools (e.g., under `tools/`) and other modules.
    """

    def __init__(self, config: Optional[TTSConfig] = None, client: Optional[OpenAI] = None):
        self.config = config or TTSConfig()
        self.client = client or OpenAI()

    def synthesize_to_file(
        self,
        input_text: str,
        file_path: Optional[Union[str, Path]] = None,
        *,
        model: Optional[str] = None,
        voice: Optional[str] = None,
        instructions: Optional[str] = None,
        audio_format: Optional[str] = None,
    ) -> str:
        """Generate speech audio and write it to `file_path`.

        Returns the absolute file path as a string on success.
        """
        if not isinstance(input_text, str) or not input_text.strip():
            raise ValueError("input_text must be a non-empty string")

        # Resolve parameters with fallbacks to config
        model = model or self.config.model
        voice = voice or self.config.voice
        audio_format = (audio_format or self.config.audio_format or "mp3").lower()
        instructions = instructions or self.config.instructions

        # Determine destination path
        if file_path is None:
            # Default location inside the module folder: tts/generated
            default_dir = (self.config.output_dir or (Path(__file__).resolve().parent / "generated"))
            ext = audio_format if audio_format in {"mp3", "wav"} else "mp3"
            filename = f"speech_{datetime.now().strftime('%Y%m%d-%H%M%S')}.{ext}"
            path = default_dir / filename
        else:
            path = Path(file_path)
            if path.is_dir():
                # If given a directory, place a timestamped default file name inside it
                ext = audio_format if audio_format in {"mp3", "wav"} else "mp3"
                filename = f"speech_{datetime.now().strftime('%Y%m%d-%H%M%S')}.{ext}"
                path = path / filename
        # Ensure destination directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Build request kwargs; only include instructions if provided to remain
        # compatible with SDKs that may not accept it.
        request_kwargs: Dict[str, Any] = {
            "model": model,
            "voice": voice,
            "input": input_text,
            "format": audio_format,
        }
        if instructions:
            request_kwargs["instructions"] = instructions

        # Stream directly to file to avoid loading full audio in memory
        with self.client.audio.speech.with_streaming_response.create(**request_kwargs) as response:
            response.stream_to_file(path)

        return str(path.resolve())

    def synthesize_bytes(
        self,
        input_text: str,
        *,
        model: Optional[str] = None,
        voice: Optional[str] = None,
        instructions: Optional[str] = None,
        audio_format: Optional[str] = None,
    ) -> bytes:
        """Generate speech audio and return raw bytes.

        Useful when a tool wants to handle the audio content itself rather than
        writing to disk.
        """
        if not isinstance(input_text, str) or not input_text.strip():
            raise ValueError("input_text must be a non-empty string")

        model = model or self.config.model
        voice = voice or self.config.voice
        audio_format = (audio_format or self.config.audio_format or "mp3").lower()
        instructions = instructions or self.config.instructions

        request_kwargs: Dict[str, Any] = {
            "model": model,
            "voice": voice,
            "input": input_text,
            "format": audio_format,
        }
        if instructions:
            request_kwargs["instructions"] = instructions

        # Non-streaming, fetch full response in memory
        result = self.client.audio.speech.create(**request_kwargs)
        # The SDK returns a Response object with .to_bytes()
        return result.to_bytes()

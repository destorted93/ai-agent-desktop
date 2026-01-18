"""Audio transcription service using OpenAI Whisper."""

import io
from pathlib import Path
from typing import Optional, Dict, Any, Union

from openai import OpenAI


class TranscribeService:
    """In-process audio transcription service."""
    
    ALLOWED_EXTENSIONS = {"wav", "mp3", "m4a", "flac", "ogg", "webm"}
    
    def __init__(
        self,
        client: Optional[OpenAI] = None,
        model: str = "gpt-4o-transcribe",
        default_language: str = "en",
    ):
        """Initialize the transcription service.
        
        Args:
            client: OpenAI client instance (creates new one if not provided)
            model: Transcription model to use
            default_language: Default language for transcription
        """
        self.client = client or OpenAI()
        self.model = model
        self.default_language = default_language
    
    def transcribe_file(
        self,
        file_path: Union[str, Path],
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transcribe an audio file.
        
        Args:
            file_path: Path to the audio file
            language: Language code (defaults to configured language)
            
        Returns:
            Dict with transcription result or error
        """
        path = Path(file_path)
        
        if not path.exists():
            return {"status": "error", "message": "File not found"}
        
        ext = path.suffix.lower().lstrip(".")
        if ext not in self.ALLOWED_EXTENSIONS:
            return {"status": "error", "message": f"Unsupported format: {ext}"}
        
        lang = language or self.default_language
        
        try:
            with open(path, "rb") as f:
                transcription = self.client.audio.transcriptions.create(
                    model=self.model,
                    file=f,
                    language=lang,
                    prompt="Transcribe the audio with natural punctuation.",
                )
            
            text = getattr(transcription, "text", None)
            if not text:
                return {"status": "error", "message": "No transcription returned"}
            
            return {
                "status": "success",
                "text": text,
                "language": lang,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def transcribe_bytes(
        self,
        audio_data: bytes,
        filename: str = "audio.wav",
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transcribe audio from bytes.
        
        Args:
            audio_data: Raw audio bytes
            filename: Filename hint for format detection
            language: Language code
            
        Returns:
            Dict with transcription result or error
        """
        ext = filename.split(".")[-1].lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            return {"status": "error", "message": f"Unsupported format: {ext}"}
        
        lang = language or self.default_language
        
        try:
            audio_file = io.BytesIO(audio_data)
            audio_file.name = filename
            
            transcription = self.client.audio.transcriptions.create(
                model=self.model,
                file=audio_file,
                language=lang,
                prompt="Transcribe the audio with natural punctuation.",
            )
            
            text = getattr(transcription, "text", None)
            if not text:
                return {"status": "error", "message": "No transcription returned"}
            
            return {
                "status": "success",
                "text": text,
                "language": lang,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def transcribe(
        self,
        audio_data: bytes,
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transcribe audio data - convenience wrapper.
        
        Args:
            audio_data: Raw audio bytes (WAV format expected)
            language: Language code
            
        Returns:
            Dict with transcription result or error
        """
        return self.transcribe_bytes(audio_data, filename="audio.wav", language=language)

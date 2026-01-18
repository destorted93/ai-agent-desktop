"""Text-to-speech service using OpenAI."""

from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Union

from openai import OpenAI

from ..storage import get_app_data_dir


class TTSService:
    """In-process text-to-speech service."""
    
    def __init__(
        self,
        client: Optional[OpenAI] = None,
        model: str = "gpt-4o-mini-tts",
        voice: str = "coral",
        audio_format: str = "mp3",
        output_dir: Optional[Path] = None,
    ):
        """Initialize the TTS service.
        
        Args:
            client: OpenAI client instance
            model: TTS model to use
            voice: Voice preset
            audio_format: Output format (mp3/wav)
            output_dir: Directory for generated files
        """
        self.client = client or OpenAI()
        self.model = model
        self.voice = voice
        self.audio_format = audio_format.lower()
        self.output_dir = output_dir or (get_app_data_dir() / "tts")
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def synthesize_to_file(
        self,
        text: str,
        file_path: Optional[Union[str, Path]] = None,
        voice: Optional[str] = None,
        instructions: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate speech and save to file.
        
        Args:
            text: Text to synthesize
            file_path: Output file path (auto-generated if not provided)
            voice: Override default voice
            instructions: Style/tone instructions
            
        Returns:
            Dict with file path or error
        """
        if not text or not text.strip():
            return {"status": "error", "message": "Empty text"}
        
        voice = voice or self.voice
        
        # Determine output path
        if file_path is None:
            ext = self.audio_format if self.audio_format in {"mp3", "wav"} else "mp3"
            filename = f"speech_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
            path = self.output_dir / filename
        else:
            path = Path(file_path)
            if path.is_dir():
                ext = self.audio_format if self.audio_format in {"mp3", "wav"} else "mp3"
                filename = f"speech_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
                path = path / filename
        
        path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            kwargs = {
                "model": self.model,
                "voice": voice,
                "input": text,
                "response_format": self.audio_format,
            }
            if instructions:
                kwargs["instructions"] = instructions
            
            with self.client.audio.speech.with_streaming_response.create(**kwargs) as response:
                response.stream_to_file(path)
            
            return {
                "status": "success",
                "file_path": str(path.resolve()),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def synthesize_bytes(
        self,
        text: str,
        voice: Optional[str] = None,
        instructions: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate speech and return bytes.
        
        Args:
            text: Text to synthesize
            voice: Override default voice
            instructions: Style/tone instructions
            
        Returns:
            Dict with audio bytes or error
        """
        if not text or not text.strip():
            return {"status": "error", "message": "Empty text"}
        
        voice = voice or self.voice
        
        try:
            kwargs = {
                "model": self.model,
                "voice": voice,
                "input": text,
                "response_format": self.audio_format,
            }
            if instructions:
                kwargs["instructions"] = instructions
            
            result = self.client.audio.speech.create(**kwargs)
            
            return {
                "status": "success",
                "audio_bytes": result.content,
                "format": self.audio_format,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

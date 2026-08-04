"""TTS service for kiosk voice synthesis."""

import asyncio
import logging
import os
from typing import AsyncGenerator, Optional

import openai

from backend.schemas.client_settings import TtsSettings
from backend.services.client_settings_service import get_kiosk_settings_service
from backend.services.openai_tts_processor import process_tts_streams
from backend.services.text_segmenter import process_text_chunks

logger = logging.getLogger(__name__)


class TTSService:
    """
    Service for Text-to-Speech generation.

    Supports queue-based streaming for low-latency audio playback
    and non-streaming synthesis for simple use cases.
    """

    def __init__(self):
        self._openai_client: Optional[openai.AsyncOpenAI] = None
        self._connection_warmed: bool = False
        logger.info("TTSService initialized")

    @property
    def openai_client(self) -> openai.AsyncOpenAI:
        """Lazy-initialize OpenAI client."""
        if self._openai_client is None:
            self._openai_client = openai.AsyncOpenAI(
                api_key=os.getenv("OPENAI_API_KEY")
            )
        return self._openai_client

    async def warm_connection(self) -> None:
        """
        Pre-warm the OpenAI TTS connection by establishing TLS handshake.

        Call this when a voice session connects (if TTS is enabled) to save
        ~100-300ms on the first TTS request. Safe to call multiple times.
        """
        if self._connection_warmed:
            return

        settings = self.get_settings()
        if not settings.enabled:
            return

        try:
            # Access the client to ensure it's created
            client = self.openai_client
            # Make a minimal request to establish the connection
            # We use models.list() as it's fast and doesn't cost tokens
            # The httpx client will keep the connection alive for reuse
            await client.models.list()
            self._connection_warmed = True
            logger.info("TTS connection pre-warmed successfully")
        except Exception as e:
            logger.warning(f"TTS connection pre-warm failed (non-fatal): {e}")

    def get_settings(self) -> TtsSettings:
        """Get the kiosk's current TTS settings."""
        return get_kiosk_settings_service().get_tts()

    async def synthesize(self, text: str) -> bytes:
        """
        Synthesize text to audio (non-streaming).

        Returns raw audio bytes in the configured format.
        Returns empty bytes if TTS is disabled.
        """
        settings = self.get_settings()

        if not settings.enabled:
            logger.debug("TTS is disabled, skipping synthesis")
            return b""

        if not text.strip():
            return b""

        try:
            response = await self.openai_client.audio.speech.create(
                model=settings.model,
                voice=settings.voice,
                input=text,
                speed=settings.speed,
                response_format=settings.response_format,
            )
            audio_data = response.content
            logger.info(f"Synthesized {len(text)} chars -> {len(audio_data)} bytes")
            return audio_data
        except Exception as e:
            logger.error(f"TTS synthesis failed: {e}")
            return b""

    async def synthesize_streaming(
        self,
        text: str,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream TTS audio for a single text.

        Yields audio chunks as they become available.
        Respects stop_event for early termination.
        """
        settings = self.get_settings()

        if not settings.enabled:
            logger.debug("TTS is disabled, skipping streaming synthesis")
            return

        if not text.strip():
            return

        if stop_event is None:
            stop_event = asyncio.Event()

        chunk_size = settings.stream_chunk_bytes

        try:
            async with self.openai_client.audio.speech.with_streaming_response.create(
                model=settings.model,
                voice=settings.voice,
                input=text,
                speed=settings.speed,
                response_format=settings.response_format,
            ) as response:
                async for audio_chunk in response.iter_bytes(chunk_size):
                    if stop_event.is_set():
                        logger.debug("TTS streaming stopped by event")
                        return
                    yield audio_chunk

        except Exception as e:
            logger.error(f"TTS streaming failed: {e}")

    async def create_streaming_pipeline(
        self,
        stop_event: asyncio.Event,
    ) -> tuple[asyncio.Queue, asyncio.Queue, asyncio.Task, asyncio.Task]:
        """
        Create a full TTS streaming pipeline with text segmentation.

        Returns:
            - chunk_queue: Feed text chunks from LLM here
            - audio_queue: Read audio chunks from here
            - segmenter_task: Task running the text segmenter
            - tts_task: Task running the TTS processor

        Usage:
            chunk_queue, audio_queue, seg_task, tts_task = await tts.create_streaming_pipeline(stop_event)

            # Feed text chunks
            await chunk_queue.put("Hello ")
            await chunk_queue.put("world!")
            await chunk_queue.put(None)  # Signal end

            # Read audio chunks
            while True:
                audio = await audio_queue.get()
                if audio is None:
                    break
                # Send audio to client
        """
        settings = self.get_settings()

        chunk_queue: asyncio.Queue = asyncio.Queue()
        phrase_queue: asyncio.Queue = asyncio.Queue()
        audio_queue: asyncio.Queue = asyncio.Queue()

        # Create segmenter task
        segmenter_task = asyncio.create_task(
            process_text_chunks(
                chunk_queue=chunk_queue,
                phrase_queue=phrase_queue,
                delimiters=settings.delimiters,
                use_segmentation=settings.use_segmentation,
                first_phrase_min_chars=settings.first_phrase_min_chars,
                stop_event=stop_event,
                log_enabled=settings.segmentation_logging_enabled,
            )
        )

        # Create TTS processor task with pre-warmed client
        tts_task = asyncio.create_task(
            process_tts_streams(
                phrase_queue=phrase_queue,
                audio_queue=audio_queue,
                stop_event=stop_event,
                settings=settings,
                openai_client=self._openai_client,
            )
        )

        logger.debug("Created TTS streaming pipeline")
        return chunk_queue, audio_queue, segmenter_task, tts_task

    def get_sample_rate(self) -> int:
        """Get the sample rate for the current kiosk settings."""
        return self.get_settings().sample_rate

"""
STT service supporting Deepgram and Azure Speech engines.
Uses synchronous SDK patterns with threading for reliable connection management.
"""

import asyncio
import logging
import threading
from typing import Callable, Optional, Protocol

from deepgram import DeepgramClient
from deepgram.core.events import EventType

from backend.config import get_settings
from backend.schemas.client_settings import SttSettings
from backend.services.client_settings_service import get_kiosk_settings_service

try:
    import azure.cognitiveservices.speech as speechsdk
except Exception:  # pragma: no cover - optional dependency
    speechsdk = None

logger = logging.getLogger(__name__)

# Audio settings (must match frontend sender)
SAMPLE_RATE = 16000


class SttSessionProtocol(Protocol):
    """Common interface for STT session implementations."""

    session_id: str

    def connect(self) -> bool: ...
    def send_audio(self, data: bytes) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def close(self) -> None: ...

    @property
    def is_connected(self) -> bool: ...


class DeepgramSession:
    """Manages a single Deepgram connection using the SDK v5 sync pattern."""

    def __init__(
        self,
        api_key: str,
        session_id: str,
        on_transcript: Callable[[str, bool], None],
        on_error: Optional[Callable[[str], None]] = None,
        on_speech_start: Optional[
            Callable[[], None]
        ] = None,  # Called when Deepgram detects speech start
        # Mode selection
        mode: str = "conversation",
        # Conversation mode (Flux v2) settings
        eot_threshold: float = 0.7,
        eot_timeout_ms: int = 5000,
        eager_eot_threshold: Optional[float] = None,
        keyterms: Optional[list[str]] = None,
        # Command mode (Nova-3 v1) settings
        command_model: str = "nova-3-en",
        command_utterance_end_ms: int = 1000,
        command_endpointing: int = 300,
        command_interim_results: bool = True,
        command_smart_format: bool = True,
        command_numerals: bool = True,
        event_loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        self.api_key = api_key
        self.session_id = session_id
        self.on_transcript = on_transcript
        self.on_error = on_error
        self.on_speech_start = on_speech_start  # VAD callback

        # Mode selection
        self.mode = mode

        # Conversation mode (Flux v2) settings
        self.eot_threshold = eot_threshold
        self.eot_timeout_ms = eot_timeout_ms
        self.eager_eot_threshold = eager_eot_threshold
        self.keyterms = keyterms or []

        # Command mode (Nova-3 v1) settings
        self.command_model = command_model
        self.command_utterance_end_ms = command_utterance_end_ms
        self.command_endpointing = command_endpointing
        self.command_interim_results = command_interim_results
        self.command_smart_format = command_smart_format
        self.command_numerals = command_numerals

        # Store the main event loop reference for scheduling callbacks from worker thread
        self._event_loop = event_loop

        self._client = DeepgramClient(api_key=api_key)
        self._context_manager = None
        self._socket = None
        self._ready = threading.Event()
        self._running = False
        self._listening_thread = None
        self._paused = False  # Track pause state

    def _handle_message(self, result):
        """Handle transcript messages from Deepgram."""
        try:
            # For v2 (Flux): transcript is at top level with event type
            event = getattr(result, "event", None)

            # Speech start detection: v2=StartOfTurn, v1=SpeechStarted
            if event in ("StartOfTurn", "SpeechStarted"):
                logger.info(f"--- {event} for {self.session_id} ---")
                # Notify that speech has started (used for VAD gating after barge-in)
                if self.on_speech_start:
                    if asyncio.iscoroutinefunction(self.on_speech_start):
                        if self._event_loop is not None:
                            asyncio.run_coroutine_threadsafe(
                                self.on_speech_start(), self._event_loop
                            )
                    else:
                        self.on_speech_start()
                return

            transcript = getattr(result, "transcript", None)
            if transcript:
                # v2 (Flux) response - transcript at top level
                is_end_of_turn = event == "EndOfTurn"
                logger.info(
                    f"Transcript for {self.session_id}: '{transcript}' (eot={is_end_of_turn})"
                )

                # Call callback - need to schedule in asyncio loop if it's async
                if asyncio.iscoroutinefunction(self.on_transcript):
                    if self._event_loop is not None:
                        # Use the stored event loop reference (main loop)
                        asyncio.run_coroutine_threadsafe(
                            self.on_transcript(transcript, is_end_of_turn),
                            self._event_loop,
                        )
                    else:
                        logger.error("No event loop available for transcript callback")
                else:
                    self.on_transcript(transcript, is_end_of_turn)
            else:
                # v1 style: check for channel.alternatives (Nova models)
                channel = getattr(result, "channel", None)
                if channel is not None:
                    # Deepgram SDK may return channel as object, dict, or list
                    if isinstance(channel, (list, tuple)) and len(channel) > 0:
                        channel = channel[0]

                    alternatives = getattr(channel, "alternatives", None)
                    if alternatives is None and isinstance(channel, dict):
                        alternatives = channel.get("alternatives", [])

                    if alternatives and len(alternatives) > 0:
                        alt = alternatives[0]
                        # Handle both object and dict access
                        transcript_text = getattr(alt, "transcript", None)
                        if transcript_text is None and hasattr(alt, "__getitem__"):
                            transcript_text = alt.get("transcript", "")

                        is_final = getattr(result, "is_final", False)
                        speech_final = getattr(result, "speech_final", False)

                        if transcript_text:
                            logger.info(
                                f"Transcript for {self.session_id}: '{transcript_text}' "
                                f"(is_final={is_final}, speech_final={speech_final})"
                            )
                            # Use is_final as the signal - it indicates segment is complete
                            # speech_final comes separately and often has no text
                            if asyncio.iscoroutinefunction(self.on_transcript):
                                if self._event_loop is not None:
                                    asyncio.run_coroutine_threadsafe(
                                        self.on_transcript(transcript_text, is_final),
                                        self._event_loop,
                                    )
                                else:
                                    logger.error(
                                        "No event loop available for transcript callback"
                                    )
                            else:
                                self.on_transcript(transcript_text, is_final)
                        elif speech_final:
                            # UtteranceEnd with no new transcript - ignore, don't send empty
                            logger.debug(
                                f"Speech final (no new transcript) for {self.session_id}"
                            )
        except Exception as e:
            logger.error(
                f"Error processing transcript for {self.session_id}: {e}", exc_info=True
            )

    def _on_open(self, _):
        logger.info(f"✅ Deepgram connected for {self.session_id}")
        self._ready.set()

    def _on_close(self, _):
        logger.info(f"Deepgram disconnected for {self.session_id}")
        self._ready.clear()

    def _on_error(self, error):
        logger.error(f"Deepgram error for {self.session_id}: {error}")
        if self.on_error:
            if asyncio.iscoroutinefunction(self.on_error):
                if self._event_loop is not None:
                    asyncio.run_coroutine_threadsafe(
                        self.on_error(str(error)), self._event_loop
                    )
                else:
                    logger.error("No event loop available for error callback")
            else:
                self.on_error(str(error))

    def connect(self) -> bool:
        """Connect to Deepgram."""
        if self.mode == "command":
            # Command mode: Use Nova-3 with v1 API
            params = {
                "model": self.command_model,
                "encoding": "linear16",
                "sample_rate": str(SAMPLE_RATE),
                "interim_results": str(self.command_interim_results).lower(),
                "utterance_end_ms": str(self.command_utterance_end_ms),
                "endpointing": str(self.command_endpointing),
                "smart_format": str(self.command_smart_format).lower(),
                "numerals": str(self.command_numerals).lower(),
                # Keep profanity filtering disabled for command mode.
                "profanity_filter": "false",
                "vad_events": "true",
            }

            logger.info(
                f"Connecting to Deepgram Nova (v1) for {self.session_id} with settings: "
                f"model={self.command_model}, utterance_end_ms={self.command_utterance_end_ms}, "
                f"endpointing={self.command_endpointing}"
            )

            try:
                # Use v1 API for Nova models
                self._context_manager = self._client.listen.v1.connect(**params)
                self._socket = self._context_manager.__enter__()

                # Register handlers
                self._socket.on(EventType.OPEN, self._on_open)
                self._socket.on(EventType.MESSAGE, self._handle_message)
                self._socket.on(EventType.ERROR, self._on_error)
                self._socket.on(EventType.CLOSE, self._on_close)

                # Start listening in background thread
                def listen_loop():
                    try:
                        self._socket.start_listening()
                    except Exception as e:
                        if self._running:
                            logger.error(f"Listen error for {self.session_id}: {e}")

                self._running = True
                self._listening_thread = threading.Thread(
                    target=listen_loop, daemon=True
                )
                self._listening_thread.start()

                # Wait for connection
                if not self._ready.wait(timeout=10.0):
                    raise RuntimeError(
                        f"Failed to connect to Deepgram for {self.session_id}"
                    )

                logger.info(
                    f"Deepgram session ready for {self.session_id} (command mode)"
                )
                return True

            except Exception as e:
                logger.error(
                    f"Failed to connect to Deepgram for {self.session_id}: {e}",
                    exc_info=True,
                )
                return False
        else:
            # Conversation mode: Use Flux with v2 API
            params = {
                "model": "flux-general-en",
                "encoding": "linear16",
                "sample_rate": str(SAMPLE_RATE),
                "eot_threshold": str(self.eot_threshold),
                "eot_timeout_ms": str(self.eot_timeout_ms),
            }

            # Add optional eager EOT threshold
            if self.eager_eot_threshold is not None:
                params["eager_eot_threshold"] = str(self.eager_eot_threshold)

            # Add keyterms (Deepgram supports multiple keyterm params)
            if self.keyterms:
                params["keyterm"] = self.keyterms

            logger.info(
                f"Connecting to Deepgram Flux (v2) for {self.session_id} with settings: "
                f"eot_threshold={self.eot_threshold}, eot_timeout_ms={self.eot_timeout_ms}, "
                f"eager_eot={self.eager_eot_threshold}, keyterms={len(self.keyterms)}"
            )

            try:
                # Use v2 for Flux turn-taking
                self._context_manager = self._client.listen.v2.connect(**params)
                self._socket = self._context_manager.__enter__()

                # Register handlers
                self._socket.on(EventType.OPEN, self._on_open)
                self._socket.on(EventType.MESSAGE, self._handle_message)
                self._socket.on(EventType.ERROR, self._on_error)
                self._socket.on(EventType.CLOSE, self._on_close)

                # Start listening in background thread
                def listen_loop():
                    try:
                        self._socket.start_listening()
                    except Exception as e:
                        if self._running:
                            logger.error(f"Listen error for {self.session_id}: {e}")

                self._running = True
                self._listening_thread = threading.Thread(
                    target=listen_loop, daemon=True
                )
                self._listening_thread.start()

                # Wait for connection
                if not self._ready.wait(timeout=10.0):
                    raise RuntimeError(
                        f"Failed to connect to Deepgram for {self.session_id}"
                    )

                logger.info(
                    f"Deepgram session ready for {self.session_id} (conversation mode)"
                )
                return True

            except Exception as e:
                logger.error(
                    f"Failed to connect to Deepgram for {self.session_id}: {e}",
                    exc_info=True,
                )
                return False

    def send_audio(self, data: bytes):
        """Send audio to Deepgram."""
        if self._socket and self._ready.is_set():
            if self._paused:
                return  # Drop audio when paused

            try:
                self._socket.send_media(data)
            except Exception as e:
                logger.error(f"Error sending audio for {self.session_id}: {e}")

    def pause(self):
        """Pause audio streaming (mute) and start keepalive."""
        self._paused = True
        self._start_keepalive()
        logger.info(f"Deepgram session {self.session_id} PAUSED (keepalive started)")

    def resume(self):
        """Resume audio streaming (unmute) and stop keepalive."""
        self._stop_keepalive()
        self._paused = False
        logger.info(f"Deepgram session {self.session_id} RESUMED")

    def _start_keepalive(self):
        """Start sending KeepAlive messages every 5 seconds."""
        if hasattr(self, "_keepalive_timer") and self._keepalive_timer:
            return  # Already running

        def send_keepalive():
            if self._paused and self._socket and self._ready.is_set():
                try:
                    import json

                    self._socket.send(json.dumps({"type": "KeepAlive"}))
                    logger.debug(f"Sent KeepAlive for {self.session_id}")
                except Exception as e:
                    logger.warning(f"KeepAlive failed for {self.session_id}: {e}")

            if self._paused:
                self._keepalive_timer = threading.Timer(5.0, send_keepalive)
                self._keepalive_timer.daemon = True
                self._keepalive_timer.start()

        send_keepalive()

    def _stop_keepalive(self):
        """Stop the keepalive timer."""
        if hasattr(self, "_keepalive_timer") and self._keepalive_timer:
            self._keepalive_timer.cancel()
            self._keepalive_timer = None

    @property
    def is_connected(self) -> bool:
        """Check if the Deepgram connection is alive."""
        return self._ready.is_set() and self._socket is not None

    def close(self):
        """Close connection."""
        self._running = False
        self._ready.clear()
        self._stop_keepalive()  # Stop keepalive timer
        self._paused = False
        if self._context_manager:
            try:
                self._context_manager.__exit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error closing Deepgram for {self.session_id}: {e}")
            self._context_manager = None
            self._socket = None
        logger.info(f"Deepgram session closed for {self.session_id}")


class AzureSttSession:
    """Manages a single Azure Speech STT session using the push stream pattern."""

    def __init__(
        self,
        session_id: str,
        on_transcript: Callable[[str, bool], None],
        on_error: Optional[Callable[[str], None]] = None,
        on_speech_start: Optional[Callable[[], None]] = None,
        event_loop: Optional[asyncio.AbstractEventLoop] = None,
        # Azure-specific settings
        silence_timeout_ms: int = 500,
        initial_silence_timeout_ms: int = 5000,
        enable_dictation: bool = True,
    ):
        if speechsdk is None:
            raise RuntimeError("azure-cognitiveservices-speech is not installed")

        settings = get_settings()
        if not settings.azure_speech_key or not settings.azure_speech_region:
            raise RuntimeError(
                "Azure Speech is not configured (AZURE_SPEECH_KEY / AZURE_SPEECH_REGION)"
            )

        self.session_id = session_id
        self.on_transcript = on_transcript
        self.on_error = on_error
        self.on_speech_start = on_speech_start
        self._event_loop = event_loop
        self._connected = False
        self._paused = False
        self._stopped = False

        self._speech_config = speechsdk.SpeechConfig(
            subscription=settings.azure_speech_key.get_secret_value(),
            region=settings.azure_speech_region,
        )
        self._speech_config.speech_recognition_language = settings.azure_speech_language

        # Segmentation silence timeout — how long silence finalizes a segment
        self._speech_config.set_property(
            speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs,
            str(silence_timeout_ms),
        )
        # Initial silence timeout — how long to wait for first speech
        self._speech_config.set_property(
            speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs,
            str(initial_silence_timeout_ms),
        )
        # Dictation mode — automatic punctuation and capitalization
        if enable_dictation:
            self._speech_config.enable_dictation()

        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=SAMPLE_RATE,
            bits_per_sample=16,
            channels=1,
        )
        self._push_stream = speechsdk.audio.PushAudioInputStream(stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=self._push_stream)
        self._recognizer = speechsdk.SpeechRecognizer(
            speech_config=self._speech_config,
            audio_config=audio_config,
        )
        self._wire_events()

    def _schedule_callback(self, coro_or_func, *args) -> None:
        """Schedule a callback, handling both sync and async callables."""
        if asyncio.iscoroutinefunction(coro_or_func):
            if self._event_loop is not None:
                asyncio.run_coroutine_threadsafe(coro_or_func(*args), self._event_loop)
        else:
            coro_or_func(*args)

    def _wire_events(self) -> None:
        def on_recognizing(evt):
            if self._paused:
                return
            text = evt.result.text if evt.result else ""
            if text:
                # Interim result → is_final=False
                self._schedule_callback(self.on_transcript, text, False)

        def on_recognized(evt):
            if self._paused:
                return
            result = evt.result
            if result is None:
                return
            if result.reason == speechsdk.ResultReason.RecognizedSpeech and result.text:
                # Final result → is_final=True
                self._schedule_callback(self.on_transcript, result.text, True)

        def on_canceled(evt):
            code = evt.cancellation_details.reason if evt.cancellation_details else None
            # EndOfStream is normal when we close the push stream — not an error
            if code == speechsdk.CancellationReason.EndOfStream:
                return
            details = getattr(evt, "error_details", "Unknown cancellation")
            if self.on_error:
                self._schedule_callback(self.on_error, str(details))

        def on_speech_start_detected(_evt):
            if self.on_speech_start:
                self._schedule_callback(self.on_speech_start)

        self._recognizer.recognizing.connect(on_recognizing)
        self._recognizer.recognized.connect(on_recognized)
        self._recognizer.canceled.connect(on_canceled)
        self._recognizer.speech_start_detected.connect(on_speech_start_detected)

    def connect(self) -> bool:
        """Start continuous recognition."""
        try:
            self._recognizer.start_continuous_recognition_async().get()
            self._connected = True
            logger.info(f"Azure STT session ready for {self.session_id}")
            return True
        except Exception as e:
            logger.error(
                f"Failed to start Azure STT for {self.session_id}: {e}", exc_info=True
            )
            return False

    def send_audio(self, data: bytes) -> None:
        """Push audio bytes into the Azure push stream."""
        if not self._stopped and not self._paused:
            self._push_stream.write(data)

    def pause(self) -> None:
        self._paused = True
        logger.info(f"Azure STT session {self.session_id} PAUSED")

    def resume(self) -> None:
        self._paused = False
        logger.info(f"Azure STT session {self.session_id} RESUMED")

    @property
    def is_connected(self) -> bool:
        return self._connected and not self._stopped

    def close(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._connected = False
        try:
            self._recognizer.stop_continuous_recognition_async().get()
        except Exception:
            pass
        try:
            self._push_stream.close()
        except Exception:
            pass
        logger.info(f"Azure STT session closed for {self.session_id}")


class STTService:
    """
    Manages streaming STT sessions (Deepgram and Azure).
    Uses synchronous SDK patterns with threading for reliable connection management.
    """

    def __init__(self):
        settings = get_settings()
        api_key = (
            settings.deepgram_api_key.get_secret_value()
            if settings.deepgram_api_key
            else None
        )
        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY is not set")

        self.api_key = api_key
        self.sessions: dict[str, DeepgramSession | AzureSttSession] = {}

    def get_settings(self) -> SttSettings:
        """Get the kiosk's STT settings."""
        return get_kiosk_settings_service().get_stt()

    async def create_session(
        self,
        session_id: str,
        on_transcript: Callable[[str, bool], None],
        on_error: Optional[Callable[[str], None]] = None,
        on_speech_start: Optional[Callable[[], None]] = None,
    ):
        """
        Start a new live transcription session.
        Routes to Azure or Deepgram based on kiosk settings.
        """
        try:
            # Close existing session if any
            if session_id in self.sessions:
                await self.close_session(session_id)

            stt_settings = self.get_settings()

            loop = asyncio.get_running_loop()

            # Use Azure engine for command mode when configured
            use_azure = (
                stt_settings.mode == "command"
                and stt_settings.command_engine == "azure"
            )

            if use_azure:
                session: DeepgramSession | AzureSttSession = AzureSttSession(
                    session_id=session_id,
                    on_transcript=on_transcript,
                    on_error=on_error,
                    on_speech_start=on_speech_start,
                    event_loop=loop,
                    silence_timeout_ms=stt_settings.azure_silence_timeout_ms,
                    initial_silence_timeout_ms=stt_settings.azure_initial_silence_timeout_ms,
                    enable_dictation=stt_settings.azure_enable_dictation,
                )
            else:
                # IMPORTANT: The Deepgram listener runs in a background thread,
                # which has no asyncio event loop. We must capture the main loop
                # here (in async context) and pass it to DeepgramSession for
                # thread-safe callback scheduling via run_coroutine_threadsafe().
                session = DeepgramSession(
                    api_key=self.api_key,
                    session_id=session_id,
                    on_transcript=on_transcript,
                    on_error=on_error,
                    on_speech_start=on_speech_start,
                    # Mode selection
                    mode=stt_settings.mode,
                    # Conversation mode (Flux v2) settings
                    eot_threshold=stt_settings.eot_threshold,
                    eot_timeout_ms=stt_settings.eot_timeout_ms,
                    keyterms=stt_settings.keyterms,
                    # Command mode (Nova-3 v1) settings
                    command_model=stt_settings.command_model,
                    command_utterance_end_ms=stt_settings.command_utterance_end_ms,
                    command_endpointing=stt_settings.command_endpointing,
                    command_interim_results=stt_settings.command_interim_results,
                    command_smart_format=stt_settings.command_smart_format,
                    command_numerals=stt_settings.command_numerals,
                    event_loop=loop,
                )

            # Connect in thread pool to not block asyncio
            success = await loop.run_in_executor(None, session.connect)

            if success:
                self.sessions[session_id] = session
                engine = "azure" if use_azure else "deepgram"
                logger.info(
                    f"STT session created for {session_id} "
                    f"(mode={stt_settings.mode}, engine={engine})"
                )
                return True
            else:
                logger.error(f"Failed to create STT session for {session_id}")
                return False

        except Exception as e:
            logger.error(f"Failed to create STT session: {e}", exc_info=True)
            if on_error:
                if asyncio.iscoroutinefunction(on_error):
                    await on_error(str(e))
                else:
                    on_error(str(e))
            return False

    async def stream_audio(self, session_id: str, audio_bytes: bytes):
        """Send audio data to the live connection."""
        session = self.sessions.get(session_id)
        if session:
            # Run in executor to not block asyncio
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, session.send_audio, audio_bytes)
        else:
            logger.warning(f"No session found for {session_id} when streaming audio")

    async def close_session(self, session_id: str):
        """Close the live connection."""
        session = self.sessions.pop(session_id, None)
        if session:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, session.close)
            logger.info(f"STT session closed for {session_id}")

    def pause_session(self, session_id: str):
        """Pause a specific STT session."""
        session = self.sessions.get(session_id)
        if session:
            session.pause()

    def resume_session(self, session_id: str):
        """Resume a specific STT session."""
        session = self.sessions.get(session_id)
        if session:
            session.resume()

    def is_session_connected(self, session_id: str) -> bool:
        """Check if a session exists and is connected."""
        session = self.sessions.get(session_id)
        return session is not None and session.is_connected

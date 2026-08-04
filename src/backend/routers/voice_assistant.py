import asyncio
import base64
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.services.client_settings_service import get_kiosk_settings_service
from backend.services.kiosk_chat_service import KioskChatService
from backend.services.stt_service import STTService
from backend.services.tts_service import TTSService
from backend.services.voice_session import VoiceConnectionManager

router = APIRouter(prefix="/api/voice", tags=["Voice Assistant"])
logger = logging.getLogger(__name__)

async def handle_connection(
    websocket: WebSocket,
    client_id: str,
    manager: VoiceConnectionManager,
    stt_service: STTService,
    tts_service: TTSService,
    kiosk_chat_service: KioskChatService,
):
    """
    Main loop for handling a single client's WebSocket connection.

    Uses queue-based TTS pipeline:
    - TextSegmenter splits LLM chunks into phrases at delimiters (min length from settings)
    - TTSProcessor synthesizes phrases and broadcasts audio chunks via WebSocket
    - Frontend plays audio immediately using Web Audio API
    """
    await manager.connect(websocket, client_id)

    settings_service = get_kiosk_settings_service()
    chat_service = kiosk_chat_service

    # Pre-warm TTS connection for faster first response (runs in background)
    asyncio.create_task(tts_service.warm_connection())

    tts_cancel_event = asyncio.Event()
    tts_task: Optional[asyncio.Task] = None

    async def cancel_tts():
        nonlocal tts_task, tts_cancel_event
        tts_cancel_event.set()
        if tts_task and not tts_task.done():
            tts_task.cancel()
            logger.info(f"Cancelled active TTS task for {client_id}")

    async def start_stt_session():
        """Helper to start the STT session with callbacks."""

        async def on_transcript_received(text: str, is_final: bool):
            logger.debug(f"Transcript ({client_id}): {text} (Final: {is_final})")

            # Send transcript to THIS client only (session isolation)
            await manager.send_message(
                client_id, {"type": "transcript", "text": text, "is_final": is_final}
            )

            # User spoke, so update activity
            session = manager.get_session(client_id)
            if session:
                session.update_activity()

            if is_final:
                # Transition to PROCESSING (this client only)
                await manager.update_state(client_id, "PROCESSING")

                # Generate LLM response with streaming + queue-based TTS
                nonlocal tts_cancel_event, tts_task
                tts_cancel_event = asyncio.Event()
                full_response = ""
                response_interrupted = False

                tts_settings = tts_service.get_settings()
                tts_enabled = tts_settings.enabled

                # Create TTS streaming pipeline
                (
                    chunk_queue,
                    audio_queue,
                    segmenter_task,
                    tts_processor_task,
                ) = await tts_service.create_streaming_pipeline(tts_cancel_event)

                # Get sample rate for audio playback
                sample_rate = tts_settings.sample_rate

                # Signal start of TTS audio stream (to THIS client only)
                await manager.send_message(
                    client_id,
                    {
                        "type": "tts_audio_start",
                        "sample_rate": sample_rate,
                        "streaming": True,
                        "buffering_enabled": tts_settings.buffering_enabled,
                        "startup_delay_enabled": tts_settings.startup_delay_enabled,
                        "low_latency_audio": tts_settings.low_latency_audio,
                        "initial_buffer_sec": tts_settings.initial_buffer_sec,
                        "max_ahead_sec": tts_settings.max_ahead_sec,
                        "min_chunk_sec": tts_settings.min_chunk_sec,
                    },
                )

                # Start audio sender task (streams audio chunks as they arrive)
                async def send_audio_chunks():
                    chunk_index = 0
                    try:
                        while True:
                            audio_chunk = await audio_queue.get()
                            if audio_chunk is None:
                                break
                            await manager.send_message(
                                client_id,
                                {
                                    "type": "tts_audio_chunk",
                                    "data": base64.b64encode(audio_chunk).decode(
                                        "utf-8"
                                    ),
                                    "chunk_index": chunk_index,
                                    "is_last": False,
                                },
                            )
                            chunk_index += 1
                    except Exception as e:
                        logger.error(f"Audio sender error: {e}")
                    finally:
                        await manager.send_message(
                            client_id,
                            {
                                "type": "tts_audio_chunk",
                                "data": "",
                                "chunk_index": chunk_index,
                                "is_last": True,
                            },
                        )

                audio_sender_task = asyncio.create_task(send_audio_chunks())
                await manager.update_state(client_id, "SPEAKING")

                try:
                    # Signal start of streaming response (to THIS client only)
                    await manager.send_message(
                        client_id, {"type": "assistant_response_start"}
                    )

                    async for event in chat_service.generate_response_streaming(
                        text, client_id
                    ):
                        if tts_cancel_event.is_set():
                            response_interrupted = True
                            logger.info(f"LLM stream interrupted for {client_id}")
                            break

                        if event["type"] == "text_chunk":
                            chunk = event["content"]
                            full_response += chunk
                            await manager.send_message(
                                client_id,
                                {"type": "assistant_response_chunk", "text": chunk},
                            )

                            # Feed chunk directly to the TTS pipeline (segmentation happens internally)
                            await chunk_queue.put(chunk)

                        elif event["type"] == "tool_status":
                            await manager.send_message(
                                client_id,
                                {
                                    "type": "tool_status",
                                    "status": event["status"],
                                    "name": event["name"],
                                },
                            )
                        elif event["type"] == "error":
                            full_response = event.get(
                                "message", "Sorry, I encountered an error."
                            )
                            await chunk_queue.put(full_response)
                            break

                    # Signal end to chunk queue
                    await chunk_queue.put(None)

                    # Signal end of streaming (to THIS client only)
                    await manager.send_message(
                        client_id,
                        {
                            "type": "assistant_response_end",
                            "text": full_response,
                            "interrupted": response_interrupted,
                        },
                    )

                except Exception as e:
                    logger.error(
                        f"LLM generation failed for {client_id}: {e}", exc_info=True
                    )
                    await chunk_queue.put("Sorry, I couldn't process that request.")
                    await chunk_queue.put(None)
                    await manager.send_message(
                        client_id,
                        {
                            "type": "assistant_response_end",
                            "text": "Sorry, I couldn't process that request.",
                        },
                    )

                # Wait for TTS processing to complete
                try:
                    await segmenter_task
                    await tts_processor_task
                    await audio_sender_task
                except asyncio.CancelledError:
                    logger.info("TTS tasks were cancelled")

                # Transition back based on conversation mode
                interrupted = response_interrupted or tts_cancel_event.is_set()
                if interrupted:
                    logger.info(
                        f"Response interrupted for {client_id}, leaving state as-is"
                    )
                elif tts_enabled:
                    logger.info(
                        "TTS stream complete for %s, waiting for playback end",
                        client_id,
                    )
                else:
                    try:
                        stt_settings = settings_service.get_stt()
                        if stt_settings.mode == "conversation":
                            logger.info(
                                f"Conversation mode active for {client_id}, listening for reply"
                            )
                            await manager.update_state(client_id, "LISTENING")
                        else:
                            await manager.update_state(client_id, "IDLE")
                    except Exception as e:
                        logger.error(f"Error transitioning state after speaking: {e}")
                        await manager.update_state(client_id, "IDLE")

        async def on_stt_error(error: str):
            logger.error(f"STT Error for {client_id}: {error}")
            await manager.update_state(client_id, "IDLE")

        return await stt_service.create_session(
            client_id,
            on_transcript_received,
            on_stt_error,
        )

    try:
        while True:
            # Receive message from Pi
            data = await websocket.receive_json()
            event_type = data.get("type")

            if event_type == "heartbeat":
                # Respond with heartbeat or just ignore/log
                # The Pi expects we just stay alive
                pass

            elif event_type == "connection_ready":
                logger.info(f"Client {client_id} ready.")

            elif event_type == "wakeword_detected":
                confidence = data.get("confidence", 0.0)
                logger.info(
                    f"Wake word detected for {client_id} (confidence: {confidence})"
                )

                # Get session to check for debounce and pending state
                session = manager.get_session(client_id)
                if not session:
                    logger.warning(f"No session found for {client_id}")
                    continue

                now = datetime.utcnow()
                WAKEWORD_DEBOUNCE_MS = (
                    1000  # Ignore duplicate wakewords within 1 second
                )

                # Debounce: ignore if we just processed a wakeword
                if session.last_wakeword_time:
                    elapsed_ms = (
                        now - session.last_wakeword_time
                    ).total_seconds() * 1000
                    if elapsed_ms < WAKEWORD_DEBOUNCE_MS:
                        logger.warning(
                            f"Ignoring duplicate wakeword for {client_id} "
                            f"(only {elapsed_ms:.0f}ms since last)"
                        )
                        continue

                # Prevent concurrent session creation
                if session.stt_session_pending:
                    logger.warning(
                        f"Ignoring wakeword for {client_id} - STT session creation pending"
                    )
                    continue

                # Mark pending and update timestamp
                session.stt_session_pending = True
                session.last_wakeword_time = now

                # New conversation starts with wake word -> Clear history
                chat_service.clear_history(client_id)

                await manager.update_state(client_id, "LISTENING")

                # Start STT processing and confirm when ready
                try:
                    success = await start_stt_session()
                    if success:
                        # Send confirmation to frontend that STT session is ready
                        await manager.send_message(
                            client_id, {"type": "stt_session_ready"}
                        )
                        logger.info(f"STT session ready for {client_id}")
                    else:
                        logger.error(f"Failed to start STT session for {client_id}")
                        await manager.send_message(
                            client_id,
                            {
                                "type": "stt_session_error",
                                "error": "Failed to start STT",
                            },
                        )
                finally:
                    session.stt_session_pending = False

            elif event_type == "wakeword_barge_in":
                logger.info(f"Barge-in for {client_id}")

                # Interrupt TTS - send to THIS client only (session isolation)
                await manager.send_message(client_id, {"type": "interrupt_tts"})
                await cancel_tts()

                # Close STT session and return to IDLE (user can tap to start fresh)
                await stt_service.close_session(client_id)
                await manager.update_state(client_id, "IDLE")

            elif event_type in ("audio_chunk", "audio_data"):
                session = manager.get_session(client_id)
                # Only process audio if we are in listening mode
                if session and session.state == "LISTENING":
                    payload = data.get("data")
                    if payload is None:
                        payload = data.get("audio")
                    if payload:
                        audio_b64 = (
                            payload.get("audio")
                            if isinstance(payload, dict)
                            else payload
                        )
                        if not audio_b64:
                            logger.warning(
                                f"Received {event_type} event without audio for {client_id}"
                            )
                            continue
                        chunk = base64.b64decode(audio_b64)
                        logger.debug(
                            f"Received audio chunk for {client_id}: {len(chunk)} bytes"
                        )
                        await stt_service.stream_audio(client_id, chunk)

                        # Check for listen timeout (for THIS client only)
                        try:
                            # If we are listening but haven't heard/done anything for X seconds, go to IDLE
                            stt_settings = settings_service.get_stt()
                            listen_timeout_seconds = stt_settings.listen_timeout_seconds
                            silence_duration_ms = (
                                datetime.utcnow() - session.last_activity
                            ).total_seconds() * 1000

                            if listen_timeout_seconds > 0:
                                silence_timeout_ms = listen_timeout_seconds * 1000
                            else:
                                silence_timeout_ms = None

                            if (
                                silence_timeout_ms
                                and silence_duration_ms > silence_timeout_ms
                            ):
                                logger.info(
                                    "Listen timeout for %s (%.0fms > %.0fms) - Returning to IDLE",
                                    client_id,
                                    silence_duration_ms,
                                    silence_timeout_ms,
                                )
                                await manager.update_state(client_id, "IDLE")
                        except Exception as e:
                            logger.error(f"Error checking listen timeout: {e}")

                    else:
                        logger.warning(
                            f"Received {event_type} event without data for {client_id}"
                        )
                else:
                    if not session:
                        logger.warning(
                            f"Received audio_chunk but no session for {client_id}"
                        )
                    else:
                        # logger.debug(f"Received audio_chunk but state is {session.state}, not LISTENING")
                        pass

            elif event_type == "tts_playback_start":
                logger.info(
                    f"TTS playback started for {client_id} - Muting THIS client (State -> SPEAKING)"
                )
                await manager.update_state(client_id, "SPEAKING")
                # Pause STT for this client only (session isolation)
                stt_service.pause_session(client_id)

            elif event_type == "tts_playback_end":
                logger.info(f"TTS playback ended for {client_id}")
                # Always resume STT session (session isolation)
                stt_service.resume_session(client_id)
                # Check mode to determine next state
                try:
                    stt_settings = settings_service.get_stt()
                    if stt_settings.mode == "conversation":
                        logger.info(
                            f"Conversation mode ON - resuming listening for {client_id}"
                        )
                        await manager.update_state(client_id, "LISTENING")
                    else:
                        logger.info(f"Command mode - {client_id} going to IDLE")
                        await manager.update_state(client_id, "IDLE")
                except Exception as e:
                    logger.error(f"Error checking STT mode: {e}")
                    await manager.update_state(client_id, "IDLE")

            elif event_type == "clear_session":
                # User clicked "New" - clean up everything for fresh start
                logger.info(f"User clearing session for {client_id}")
                await stt_service.close_session(client_id)
                chat_service.clear_history(client_id)
                session = manager.get_session(client_id)
                if session:
                    session.stt_session_pending = False
                await manager.update_state(client_id, "IDLE")

            elif event_type == "pause_listening":
                logger.info(f"User paused listening for {client_id}")
                # Just pause the STT session (keep connection alive with KeepAlive)
                stt_service.pause_session(client_id)
                await manager.update_state(client_id, "IDLE")

            elif event_type == "resume_listening":
                # Resume from pause - unpause existing session OR create new one if dead
                logger.info(f"User resumed listening for {client_id}")

                # Check if session exists AND is still connected to Deepgram
                if stt_service.is_session_connected(client_id):
                    stt_service.resume_session(client_id)
                    await manager.update_state(client_id, "LISTENING")
                    await manager.send_message(client_id, {"type": "stt_session_ready"})
                    logger.info(f"STT session resumed for {client_id}")
                else:
                    # Session doesn't exist or connection is dead, create new one
                    if client_id in stt_service.sessions:
                        logger.info(
                            f"Session for {client_id} exists but connection is dead, recreating"
                        )
                        await stt_service.close_session(client_id)
                    else:
                        logger.info(
                            f"No existing session for {client_id}, creating new one"
                        )
                    await manager.update_state(client_id, "LISTENING")
                    success = await start_stt_session()
                    if success:
                        await manager.send_message(
                            client_id, {"type": "stt_session_ready"}
                        )
                        logger.info(f"STT session ready for {client_id}")
                    else:
                        logger.error(f"Failed to start STT session for {client_id}")
                        await manager.send_message(
                            client_id,
                            {
                                "type": "stt_session_error",
                                "error": "Failed to start STT",
                            },
                        )

            elif event_type == "stream_end":
                logger.info(f"Stream end for {client_id}")
                pass

            elif event_type == "alarm_acknowledge":
                alarm_id = data.get("alarm_id")
                if alarm_id:
                    alarm_scheduler = getattr(
                        websocket.app.state, "alarm_scheduler", None
                    )
                    if alarm_scheduler:
                        success = await alarm_scheduler.acknowledge_alarm(alarm_id)
                        logger.info(f"Alarm {alarm_id} acknowledged: {success}")
                        await manager.send_message(
                            client_id,
                            {
                                "type": "alarm_acknowledged",
                                "alarm_id": alarm_id,
                                "success": success,
                            },
                        )

            elif event_type == "alarm_snooze":
                alarm_id = data.get("alarm_id")
                snooze_minutes = data.get("snooze_minutes", 5)
                if alarm_id:
                    alarm_scheduler = getattr(
                        websocket.app.state, "alarm_scheduler", None
                    )
                    if alarm_scheduler:
                        new_alarm = await alarm_scheduler.snooze_alarm(
                            alarm_id, snooze_minutes
                        )
                        if new_alarm:
                            logger.info(
                                f"Alarm {alarm_id} snoozed for {snooze_minutes}m -> {new_alarm.alarm_id}"
                            )
                            await manager.send_message(
                                client_id,
                                {
                                    "type": "alarm_snoozed",
                                    "original_alarm_id": alarm_id,
                                    "new_alarm": new_alarm.to_dict(),
                                },
                            )

    except WebSocketDisconnect:
        logger.info(f"Client {client_id} disconnected")
        manager.disconnect(client_id)
        await stt_service.close_session(client_id)
    except Exception as e:
        logger.error(f"Unexpected error for {client_id}: {e}")
        manager.disconnect(client_id)
        await stt_service.close_session(client_id)


@router.websocket("/connect")
async def voice_connect(websocket: WebSocket):
    # Depending on how the Pi connects, it might send client_id in query param or header?
    # Or we generate one.
    # Plan doesn't specify auth yet, so let's generate or use a header.
    # Let's assume a query param ?client_id=... or default to "pi_1"
    client_id = websocket.query_params.get("client_id", "default_pi")

    app_state = websocket.app.state

    # Ensure services are initialized
    if not hasattr(app_state, "voice_manager"):
        logger.error("Voice Manager not initialized")
        await websocket.close(code=1000, reason="Server not ready")
        return

    manager = app_state.voice_manager
    stt_service = app_state.stt_service
    tts_service = app_state.tts_service
    kiosk_chat_service = getattr(app_state, "kiosk_chat_service", None)

    if kiosk_chat_service is None:
        logger.error("KioskChatService not initialized")
        await websocket.close(code=1011, reason="Server not ready")
        return

    await handle_connection(
        websocket,
        client_id,
        manager,
        stt_service,
        tts_service,
        kiosk_chat_service,
    )

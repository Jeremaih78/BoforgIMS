import json

from django.http import StreamingHttpResponse


def server_sent_event_stream(chunks):
    """Translate AIStreamChunk objects into framework-neutral SSE events."""
    for chunk in chunks:
        if chunk.is_final:
            payload = {
                "message_id": chunk.result.message_id,
                "conversation_id": chunk.result.conversation_id,
                "usage_log_id": chunk.result.usage_log_id,
            }
            yield _event("complete", payload)
        elif chunk.delta:
            yield _event("delta", {"text": chunk.delta})


def streaming_http_response(chunks):
    response = StreamingHttpResponse(
        server_sent_event_stream(chunks), content_type="text/event-stream"
    )
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response


def _event(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

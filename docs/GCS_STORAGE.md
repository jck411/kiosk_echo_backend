# GCS-Backed Attachment Storage

## Status: ✅ Implemented

This document describes the Google Cloud Storage (GCS) attachment system currently in production.

## Overview

All user uploads and model-generated images are stored in **private Google Cloud Storage** with signed URLs for secure, temporary access. No files are stored locally beyond ephemeral streaming.

### Key Features

- **Private storage**: All objects in GCS bucket with IAM-based access control
- **Signed URLs**: Time-limited access tokens (default 7-day TTL)
- **Automatic refresh**: Expired URLs are regenerated when serving chat history
- **Metadata in SQLite**: Database stores blob names, URLs, and expiration timestamps
- **Background cleanup**: Scheduled job removes expired attachments from GCS and database
- **Size validation**: Configurable upload limits (default 10MB)

## Architecture

```
Upload Request → FastAPI → AttachmentService
                              ↓
                         [Validate size/type]
                              ↓
                      [Upload to GCS]
                              ↓
                    [Generate signed URL]
                              ↓
                  [Store metadata in SQLite]
                              ↓
                Return signed URL to client
```

When serving chat history:
```
History Request → Repository → Attachments with expired URLs
                                        ↓
                              [Auto-regenerate signed URLs]
                                        ↓
                              Update database + return
```

## Configuration

### Environment Variables

```bash
# Required
GCS_BUCKET_NAME=your-bucket-name
GCP_PROJECT_ID=your-project-id
GOOGLE_APPLICATION_CREDENTIALS=credentials/sa.json

# Optional (with defaults)
ATTACHMENTS_MAX_SIZE_BYTES=10485760  # 10MB
ATTACHMENTS_RETENTION_DAYS=7
```

### Service Account Permissions

The service account must have these IAM roles on the bucket:

- `storage.objects.create` - Upload new attachments
- `storage.objects.get` - Generate signed URLs
- `storage.objects.delete` - Cleanup expired attachments

Example IAM policy:
```json
{
  "bindings": [
    {
      "role": "roles/storage.objectAdmin",
      "members": ["serviceAccount:your-sa@project.iam.gserviceaccount.com"]
    }
  ]
}
```

## Implementation Details

### Blob Naming Convention

Attachments use a structured path:
```
{session_id}/{attachment_id}__{sanitized_filename}
```

Example:
```
b9647eebdf3063cb0ddf9b55914aaeb2/a7de4893__screenshot.png
```

This ensures:
- Session-based organization
- No filename collisions
- Safe characters only (alphanumeric, underscore, hyphen, dot)

### Database Schema

```sql
CREATE TABLE attachments (
    attachment_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    gcs_blob TEXT NOT NULL,              -- Full GCS object path
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    signed_url TEXT NOT NULL,            -- Current valid URL
    signed_url_expires_at TEXT NOT NULL, -- ISO8601 timestamp
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES conversations(session_id)
);
```

### Supported MIME Types

```python
ALLOWED_ATTACHMENT_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "application/pdf",
}
```

Additional types can be added to `AttachmentService.ALLOWED_ATTACHMENT_MIME_TYPES`.

## API Flow

### Upload Attachment

**Request:**
```http
POST /api/uploads
Content-Type: multipart/form-data

file: (binary)
session_id: "abc123..."
```

**Response:**
```json
{
  "attachment": {
    "id": "a7de4893...",
    "displayUrl": "https://storage.googleapis.com/bucket/...?X-Goog-Signature=...",
    "deliveryUrl": "https://storage.googleapis.com/bucket/...?X-Goog-Signature=...",
    "mimeType": "image/png",
    "sizeBytes": 245678
  }
}
```

The `displayUrl` and `deliveryUrl` are identical signed URLs valid for 7 days (configurable via `ATTACHMENTS_RETENTION_DAYS`).

### Retrieve Chat History

When fetching messages, the backend:

1. Queries messages with attachment references
2. Checks each attachment's `signed_url_expires_at`
3. If expired, generates new signed URL and updates database
4. Returns messages with fresh URLs

This ensures URLs in responses are always valid.

## Code Organization

```
src/backend/services/
  gcs.py                  # GCS client wrapper (upload, delete, sign)
  attachments.py          # Service layer (validation, metadata)
  attachments_naming.py   # Blob name sanitization

src/backend/routers/
  uploads.py             # POST /api/uploads endpoint

src/backend/repository.py
  # SQLite operations for attachments table
```

### Key Classes

**`GCSService`** (`services/gcs.py`):
- `upload_bytes(blob_name, data, content_type)` - Upload to GCS
- `delete_blob(blob_name)` - Remove object
- `sign_get_url(blob_name, expires_delta)` - Generate signed URL

**`AttachmentService`** (`services/attachments.py`):
- `save_user_upload(session_id, upload)` - Handle FastAPI UploadFile
- `save_model_image_bytes(session_id, data, mime_type)` - Store generated images
- `delete_attachment(attachment_id)` - Remove from GCS and database
- `ensure_fresh_signed_url(attachment)` - Regenerate if expired

## Background Jobs

### Cleanup Task

Located in `src/backend/tasks/cleanup.py`, runs periodically to:

1. Query attachments where `signed_url_expires_at < now()`
2. Delete GCS objects via `gcs.delete_blob()`
3. Remove database records

Currently triggered on application startup. For production, consider:
- Cron job calling cleanup endpoint
- Celery/Redis-based task queue
- Cloud Scheduler (if running on GCP)

### GCS Lifecycle Policy (Optional)

As a safety net, configure a bucket lifecycle rule:

```json
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {
          "age": 9,
          "matchesPrefix": [""]
        }
      }
    ]
  }
}
```

This auto-deletes objects older than retention period + buffer (e.g., 7 days retention + 2 days = 9 days).

## Testing

Tests are in `tests/test_attachments.py` and cover:

- Upload validation (size, type)
- GCS blob creation
- Signed URL generation
- Expired URL refresh
- Cleanup operations

Run tests:
```bash
uv run pytest tests/test_attachments.py -v
```

Note: Tests use mocked GCS client to avoid real API calls and costs.

## Troubleshooting

### Symptom: Upload fails with "Permission denied"

**Solution:**
- Verify service account has `storage.objects.create` permission
- Check `GOOGLE_APPLICATION_CREDENTIALS` points to valid JSON
- Ensure bucket exists: `gsutil ls gs://your-bucket-name`

### Symptom: Images not loading in UI

**Possible causes:**
1. Signed URL expired (should auto-refresh on next history fetch)
2. CORS not configured on bucket
3. Network/firewall blocking GCS

**Debug steps:**
```bash
# Check URL manually
curl -I "https://storage.googleapis.com/bucket/path?X-Goog-Signature=..."

# Check database
sqlite3 data/chat_sessions.db "SELECT attachment_id, signed_url_expires_at FROM attachments;"

# Force refresh by fetching history
curl http://localhost:8000/api/chat/session/{id}/messages
```

### Symptom: Cleanup not running

**Solution:**
- Check logs for scheduled task errors
- Manually trigger: call cleanup function from Python shell
- Verify retention period is reasonable (not too long)

## Performance Considerations

- **Upload latency**: Network latency to GCS (typically 50-200ms)
- **Signed URL generation**: Cryptographic signing (~5ms per URL)
- **Database queries**: Indexed by `attachment_id` and `session_id` (fast)
- **Concurrent uploads**: GCS handles parallelism well, no bottleneck

For high-volume scenarios:
- Consider GCS multipart upload for files >5MB
- Batch signed URL generation
- Use GCS CDN if serving globally

## Security Best Practices

1. **Keep bucket private**: Never enable public access
2. **Short TTL**: Use shortest viable signed URL expiration
3. **Rotate service accounts**: Periodically generate new SA keys
4. **Audit access**: Enable GCS audit logs
5. **Validate uploads**: Always check MIME type and size server-side
6. **No sensitive filenames**: Sanitize to prevent info disclosure

## References

- [Google Cloud Storage Python Client](https://cloud.google.com/python/docs/reference/storage/latest)
- [Signed URLs Documentation](https://cloud.google.com/storage/docs/access-control/signed-urls)
- [GCS Lifecycle Management](https://cloud.google.com/storage/docs/lifecycle)

## Related Documentation

- [REFERENCE.md](REFERENCE.md) - Operations guide with troubleshooting
- [README.md](../README.md) - Project setup and quick start

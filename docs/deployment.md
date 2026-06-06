# Deployment

Deploy Magi Agent as a Python runtime with explicit configuration, secrets, and
storage.

## Deployment checklist

- Pin the runtime image or package version.
- Set provider credentials through a secret manager.
- Mount workspace and durable state where required.
- Keep external tool authority explicit.
- Expose only the endpoints needed by your surface.
- Monitor health, event output, and evidence records.
- Verify rollback before broadening authority.

## Local first

Run the local dashboard and focused tests before deploying. A deployment should
not be considered ready until the exact enabled tools, model path, and evidence
requirements have been verified.


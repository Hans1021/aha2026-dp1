"""logs helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
from datetime import datetime
from datetime import timezone
from pathlib import Path
import hashlib
import json
import os
import re
import uuid


# keep per-call exports beside the chronological event stream.
class AILogger:
    # allocate a fresh run directory and retain source provenance.
    def __init__(self, root, run_id=None, *, source_dir):
        self.root = Path(root).resolve()
        source_dir = Path(source_dir).expanduser()
        self.source_dir = (source_dir if source_dir.is_absolute() else self.root / source_dir).resolve()
        if not self.source_dir.is_relative_to(self.root) or not self.source_dir.is_dir():
            raise ValueError("source_dir must be an existing directory inside the repository")
        self.log_root = self.root / "ai_logs" / self.source_dir.name
        self.run_id = run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8])
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.run_id):
            raise ValueError("run_id must be a single directory name")
        self.run_dir = self.log_root / "runs" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.events_path = self.run_dir / "events.jsonl"
        self.history_path = self.run_dir / "history.jsonl"
        self._secrets = set()
        self._context = {}
        self._candidate = {}
        self._exchanges = {}
        self._finalized = set()

    # redact explicit keys and configured environment credentials.
    def register_secret(self, secret):
        if secret:
            self._secrets.add(secret)

    # redact plain and JSON-escaped credentials inside nested SDK response strings.
    def _redact(self, value):
        secrets = self._secrets | {os.environ.get(name) for name in
                                   ("PORTKEY_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY")}
        secrets = sorted(filter(None, secrets), key=len, reverse=True)

        # cover every escaped spelling that can fit inside this string.
        def redact_string(text):
            for secret in secrets:
                variants = {secret}
                for ascii_only in (False, True):
                    encoded = secret
                    while len(encoded) <= len(text):
                        variants.add(encoded)
                        escaped = json.dumps(encoded, ensure_ascii=ascii_only)[1:-1]
                        if escaped == encoded:
                            break
                        encoded = escaped
                for variant in sorted(variants, key=len, reverse=True):
                    text = text.replace(variant, "[redacted]")
            return text

        # retain JSON structure while scrubbing strings and object keys.
        def scrub(item):
            if isinstance(item, str):
                return redact_string(item)
            if isinstance(item, dict):
                return {redact_string(key): scrub(content) for key, content in item.items()}
            if isinstance(item, list):
                return [scrub(content) for content in item]
            return item

        return scrub(json.loads(json.dumps(value, ensure_ascii=False)))

    # hash exported, redacted JSON so readers can verify its contents.
    @staticmethod
    def _digest(value):
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    # replace a pretty export atomically after flushing its complete record.
    def _write_export(self, path, record):
        temporary = path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(self._redact(record), indent=2, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    # retain actual responses without treating model text as validated artifacts.
    def _export_event(self, event):
        kind = event["kind"]
        if kind == "context":
            self._context = {key: event[key] for key in
                             ("source_sha256", "notebook_code_sha256", "revision", "seed", "mode", "target")
                             if key in event}
            return
        if kind == "candidate":
            self._candidate = {key: event[key] for key in ("attempt", "stage", "target") if key in event}
            return
        request_id = event.get("request_id")
        if not request_id:
            return
        # each actual API attempt receives its own numbered, pending export.
        if kind == "prompt":
            if request_id in self._exchanges:
                raise ValueError("Duplicate prompt request_id")
            stage = event.get("stage", "generation")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", stage):
                raise ValueError("stage must be a simple name")
            request = dict(event.get("parameters", {}))
            request["messages"] = event.get("messages", [])
            record = {
                "stage": stage, "timestamp": event["timestamp"],
                "model": request.get("model"), "run_id": self.run_id,
                "request_id": request_id, "candidate": dict(self._candidate),
                "parameter_attempt": event.get("parameter_attempt", 1),
                "base_url": event.get("base_url"), "timeout_s": event.get("timeout_s"),
                "simulation_seed": event.get("simulation_seed"),
                "sdk_max_retries": event.get("sdk_max_retries"),
                "source_sha256": self._context.get("source_sha256", {}),
                "notebook_code_sha256": self._context.get("notebook_code_sha256"),
                "revision": self._context.get("revision"),
                "context": dict(self._context), "request": request,
                "request_sha256": self._digest(request), "status": "pending",
            }
            path = self.run_dir / f"{len(self._exchanges) + 1:02d}_{stage}.json"
            self._exchanges[request_id] = (path, record)
            self._write_export(path, record)
            return
        # validation after API completion remains in events, separate from API status.
        if request_id not in self._exchanges or request_id in self._finalized:
            return
        path, record = self._exchanges[request_id]
        if kind == "output":
            raw = event.get("raw")
            try:
                response = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                response = raw
            response = self._redact(response)
            record["response"] = response
            record["response_format"] = event.get("raw_format")
            if "status_code" in event:
                record["http_status_code"] = event["status_code"]
            record["response_sha256"] = self._digest(response)
            # extract only text actually present in the provider response.
            choices = response.get("choices") if isinstance(response, dict) else None
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict) and "content" in message:
                    record["text"] = message["content"]
            self._write_export(path, record)
        elif kind == "error":
            record["error"] = {key: event[key] for key in
                               ("error_type", "message", "retry_without_temperature") if key in event}
            self._write_export(path, record)
        elif kind == "request_end":
            record["status"] = event["status"]
            record["completed_at"] = event["timestamp"]
            record["api_call_attempted"] = event.get("api_call_attempted", False)
            self._write_export(path, record)
            # append exactly one terminal snapshot for this actual request.
            with self.history_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(self._redact(record), ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._finalized.add(request_id)

    # persist prompt events before a call and retain every raw output and error.
    def log_event(self, kind, **fields):
        event = self._redact({
            "kind": kind, "event_id": uuid.uuid4().hex,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id, **fields,
        })
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._export_event(event)
        return event

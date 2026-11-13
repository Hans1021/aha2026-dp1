"""client helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
import json
import os
import re
import uuid
from .common import DEFAULT_BASE_URL, DEFAULT_MODEL, normalize_prompt_text


# match the lab's Portkey client while preserving an audit for every SDK call.
class LLMClient:
    # defer SDK creation until a generation request.
    def __init__(self, api_key=None, model=None, provider="portkey", *, logger,
                 timeout_s=120, sdk_factory=None):
        # keep the provider explicit and network waits bounded.
        if provider != "portkey":
            raise ValueError(f"Unsupported provider: {provider}")
        if not 0 < timeout_s <= 300:
            raise ValueError("Use a timeout in (0, 300] seconds")
        self.provider = provider
        self.model = model or os.environ.get("PORTKEY_MODEL") or DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("PORTKEY_API_KEY")
        self.base_url = os.environ.get("PORTKEY_BASE_URL", DEFAULT_BASE_URL)
        self.logger = logger
        self.logger.register_secret(self.api_key)
        self.timeout_s = timeout_s
        self.sdk_factory = sdk_factory
        self.client = None
        self.use_temperature = True
        self.last_request_id = None

    # report local credential configuration without contacting the gateway.
    def is_available(self):
        return bool(self.api_key and self.api_key.strip())

    # construct one reusable SDK client with its hidden retries disabled.
    def _get_client(self):
        # retain the same connection pool across generation and repair attempts.
        if self.client is None:
            factory = self.sdk_factory
            # defer the optional dependency until live generation is requested.
            if factory is None:
                from portkey_ai import Portkey
                factory = Portkey
            self.client = factory(base_url=self.base_url, api_key=self.api_key,
                                  timeout=self.timeout_s, max_retries=0)
            # Portkey 2.3.4 resets retries on its nested chat client.
            self.client.openai_client.max_retries = 0
        return self.client

    # retry only an explicit unsupported-temperature client error.
    @staticmethod
    def _temperature_rejected(error):
        status = getattr(error, "status_code", None)
        # SDK HTTP errors normally expose both status_code and response.
        if status is None:
            status = getattr(getattr(error, "response", None), "status_code", None)
        message = str(error).lower()
        unsupported = re.search(r"unsupported|not supported|does not support|deprecated|not allowed|not permitted|only.{0,40}default", message)
        # auth, server, and transport failures must not trigger a parameter retry.
        return ("temperature" in message and
                ((status in (400, 422) and bool(unsupported)) or
                 (isinstance(error, TypeError) and "unexpected keyword" in message)))

    # generate text with the lab's interface and one logged parameter retry.
    def generate(self, prompt, system_prompt=None, temperature=0.0, max_tokens=16000, *, seed=None, stage="generation", reasoning_effort="low"):
        messages = []
        # include the optional system prompt exactly as supplied.
        if system_prompt:
            messages.append({"role": "system", "content": normalize_prompt_text(system_prompt)})
        messages.append({"role": "user", "content": normalize_prompt_text(prompt)})
        kwargs = {"model": self.model, "messages": messages,
                  "max_tokens": max_tokens, "stream": False, "timeout": self.timeout_s}
        # Claude Opus 5.5 reasons by default and its thinking cannot be disabled or
        # budgeted; at the default effort the reasoning tokens consumed most of
        # max_tokens and truncated the artifact ("Generation did not finish normally").
        # Low effort keeps the reasoning small so the full artifact fits the budget.
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        # remember model compatibility across later calls in the same notebook.
        # Opus 5.5 rejects an explicit temperature unless thinking is adaptive, so
        # once reasoning effort is constrained the temperature must be omitted.
        if self.use_temperature and not reasoning_effort:
            kwargs["temperature"] = temperature
        # limit fallback to one retry and log each actual SDK attempt separately.
        for attempt in range(2):
            request_id = uuid.uuid4().hex
            self.last_request_id = request_id
            parameters = {key: value for key, value in kwargs.items() if key != "messages"}
            self.logger.log_event("prompt", request_id=request_id, source="portkey_sdk", mode="live",
                                  stage=stage, messages=messages, parameters=parameters, base_url=self.base_url,
                                  timeout_s=self.timeout_s, simulation_seed=seed,
                                  sdk_max_retries=0, parameter_attempt=attempt + 1)
            response_received = False
            api_call_attempted = False
            # retain failed requests and responses before any fallback or parsing.
            try:
                # fail missing credentials before contacting the gateway.
                if not self.is_available():
                    raise ValueError("Set PORTKEY_API_KEY before requesting live generation")
                client = self._get_client()
                api_call_attempted = True
                response = client.chat.completions.create(**kwargs)
                response_received = True
                raw = response.model_dump_json()
                self.logger.log_event("output", request_id=request_id, source="portkey_sdk",
                                      live_api_call=True, raw_format="sdk_model_dump_json", raw=raw)
                choice = response.choices[0]
                # reject incomplete or refused model output after saving it.
                if getattr(choice, "finish_reason", None) not in (None, "stop", "end_turn"):
                    raise ValueError("Generation did not finish normally; see the logged response")
                output_text = choice.message.content
                if not isinstance(output_text, str) or not output_text.strip():
                    raise ValueError("Generation returned no text; see the logged response")
                self.logger.log_event("request_end", request_id=request_id, status="returned",
                                      api_call_attempted=api_call_attempted)
                return output_text
            except Exception as error:
                error_response = getattr(error, "response", None)
                raw_error = getattr(error_response, "text", None)
                # preserve structured SDK error bodies when no HTTP text exists.
                if raw_error is None and getattr(error, "body", None) is not None:
                    raw_error = json.dumps(error.body)
                if raw_error is not None:
                    self.logger.log_event("output", request_id=request_id, source="portkey_sdk",
                                          live_api_call=True, raw_format="http_error",
                                          status_code=getattr(error, "status_code", None), raw=raw_error)
                retry = attempt == 0 and "temperature" in kwargs and self._temperature_rejected(error)
                event = self.logger.log_event("error", request_id=request_id,
                                              error_type=type(error).__name__, message=str(error),
                                              retry_without_temperature=retry)
                status = ("response_error" if response_received else
                          "api_error" if api_call_attempted else "request_error")
                self.logger.log_event("request_end", request_id=request_id, status=status,
                                      api_call_attempted=api_call_attempted)
                # omit temperature once and keep it omitted for subsequent calls.
                if retry:
                    self.use_temperature = False
                    kwargs.pop("temperature")
                    continue
                raise RuntimeError(f"Portkey request failed: {event['message']}") from None

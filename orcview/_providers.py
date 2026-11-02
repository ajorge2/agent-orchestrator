"""Provider adapters for the observable agent loop. Credentials stay server-side."""
import json
from types import SimpleNamespace


class ResponseContent(list):
    """Keep complete Responses output (including reasoning) for the next turn."""
    def __init__(self, blocks, output):
        super().__init__(blocks)
        self.output = output


def create_client(model):
    if model.startswith("claude-"):
        import anthropic
        return anthropic.Anthropic()
    if model != "gpt-5.4-mini":
        raise ValueError("Unsupported model. Choose gpt-5.4-mini or a Claude model.")
    from openai import OpenAI
    return OpenAIAdapter(OpenAI(timeout=60, max_retries=2))


class OpenAIAdapter:
    def __init__(self, client):
        self.client = client
        self.messages = self

    def create(self, *, model, max_tokens, system, tools, messages):
        inputs = []
        for message in messages:
            content = message["content"]
            if isinstance(content, ResponseContent):
                inputs.extend(content.output)
            elif isinstance(content, list):
                inputs.extend({"type": "function_call_output", "call_id": result["tool_use_id"],
                               "output": result["content"]} for result in content)
            else:
                inputs.append(message)
        response = self.client.responses.create(
            model=model, instructions=system, input=inputs,
            tools=[{"type": "function", "name": t["name"],
                    "description": t["description"], "parameters": t["input_schema"],
                    "strict": False} for t in tools],
            max_output_tokens=max(8192, max_tokens), reasoning={"effort": "low"},
            store=False, include=["reasoning.encrypted_content"],
        )
        if response.status != "completed":
            raise RuntimeError("OpenAI response incomplete; retry or simplify the question.")
        blocks = []
        output = [item.model_dump(exclude_none=True) for item in response.output]
        for item in response.output:
            if item.type == "function_call":
                arguments = json.loads(item.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("OpenAI returned invalid tool arguments")
                blocks.append(SimpleNamespace(type="tool_use", id=item.call_id,
                                              name=item.name, input=arguments))
            elif item.type == "message":
                for part in item.content:
                    if part.type == "output_text":
                        blocks.append(SimpleNamespace(type="text", text=part.text))
                    elif part.type == "refusal":
                        blocks.append(SimpleNamespace(type="text", text=part.refusal))
        usage = response.usage
        cached = getattr(usage.input_tokens_details, "cached_tokens", 0) or 0
        # Standard GPT-5.4 mini rates per million tokens (2026-11-02).
        cost = ((usage.input_tokens - cached) * .75 + cached * .075 + usage.output_tokens * 4.5) / 1_000_000
        return SimpleNamespace(
            content=ResponseContent(blocks, output),
            stop_reason="tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn",
            usage=SimpleNamespace(input_tokens=usage.input_tokens,
                                  output_tokens=usage.output_tokens, cost_usd=cost),
        )

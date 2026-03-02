"""
OpenAI API wrapper with tool calling support.
This is the interface between agents and the LLM brain.
Agents define their tools, this module executes the LLM calls.
"""

import json
import time
import structlog
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from typing import Callable
from tenacity import retry, stop_after_attempt, wait_exponential

from core.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

# Initialize OpenAI client once
client = OpenAI(api_key=settings.openai_api_key)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def call_llm(
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 2000,
) -> dict:
    """
    Call OpenAI Chat Completions API with optional tool calling.
    
    Returns the full response object with:
    - response.choices[0].message.content (text response)
    - response.choices[0].message.tool_calls (if tools were used)
    """
    model = model or settings.openai_model_default
    start_time = time.time()

    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    response = client.chat.completions.create(**kwargs)

    elapsed_ms = int((time.time() - start_time) * 1000)
    tokens_used = response.usage.total_tokens if response.usage else 0

    logger.info(
        "llm_call",
        model=model,
        tokens=tokens_used,
        elapsed_ms=elapsed_ms,
        has_tool_calls=bool(response.choices[0].message.tool_calls),
    )

    return {
        "message": response.choices[0].message,
        "content": response.choices[0].message.content,
        "tool_calls": response.choices[0].message.tool_calls,
        "tokens_used": tokens_used,
        "elapsed_ms": elapsed_ms,
        "model": model,
    }


def run_agent_loop(
    system_prompt: str,
    user_message: str,
    tools_spec: list[dict],
    tool_handlers: dict[str, Callable],
    model: str | None = None,
    max_iterations: int = 10,
) -> dict:
    """
    Run a full agent loop: LLM decides tools → we execute → feed results back → repeat.
    
    This is the core agentic pattern:
    1. Send system prompt + user message + available tools to LLM
    2. LLM responds with either text (done) or tool_calls (needs to do something)
    3. If tool_calls: execute each tool, collect results, send back to LLM
    4. Repeat until LLM responds with text (no more tool calls) or max iterations
    
    Args:
        system_prompt: The agent's role, rules, and knowledge
        user_message: The current task or customer message
        tools_spec: List of OpenAI tool definitions (function schemas)
        tool_handlers: Dict mapping tool names to Python functions
        model: Which OpenAI model to use
        max_iterations: Safety limit to prevent infinite loops
    
    Returns:
        Dict with final response text, all tool calls made, total tokens used
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    all_tool_calls = []
    total_tokens = 0
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        result = call_llm(
            messages=messages,
            tools=tools_spec if tools_spec else None,
            model=model,
        )
        total_tokens += result["tokens_used"]
        message = result["message"]

        # If no tool calls, the agent is done — return the text response
        if not message.tool_calls:
            return {
                "response": message.content or "",
                "tool_calls": all_tool_calls,
                "total_tokens": total_tokens,
                "iterations": iteration,
            }

        # Agent wants to call tools — execute all in parallel then feed results back
        messages.append(message.model_dump())

        def _run_tool(tool_call):
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments)
            logger.info("tool_call", tool=fn_name, args=fn_args)
            handler = tool_handlers.get(fn_name)
            try:
                if handler:
                    tool_result = handler(**fn_args)
                    result_str = json.dumps(tool_result) if isinstance(tool_result, (dict, list)) else str(tool_result)
                else:
                    result_str = json.dumps({"error": f"Unknown tool: {fn_name}"})
            except Exception as e:
                logger.error("tool_error", tool=fn_name, error=str(e))
                result_str = json.dumps({"error": str(e)})
            return tool_call, fn_name, fn_args, result_str

        # Run all tool calls in parallel (OpenAI supports multiple tool_calls per response)
        with ThreadPoolExecutor(max_workers=len(message.tool_calls)) as executor:
            futures = [executor.submit(_run_tool, tc) for tc in message.tool_calls]
            parallel_results = [f.result() for f in as_completed(futures)]

        # Sort results back to original tool_call order (OpenAI requires matching order)
        tc_order = {tc.id: i for i, tc in enumerate(message.tool_calls)}
        parallel_results.sort(key=lambda r: tc_order.get(r[0].id, 0))

        for tool_call, fn_name, fn_args, result_str in parallel_results:
            all_tool_calls.append({"tool": fn_name, "args": fn_args, "result": result_str})
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_str,
            })

    # Safety: max iterations reached
    logger.warning("agent_loop_max_iterations", iterations=max_iterations)
    return {
        "response": "I've reached my processing limit. Escalating to human team for assistance.",
        "tool_calls": all_tool_calls,
        "total_tokens": total_tokens,
        "iterations": iteration,
    }


def call_llm_simple(
    system_prompt: str,
    user_message: str,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 2000,
) -> str:
    """
    Simple LLM call without tool calling — just get a text response.
    Used for: message generation, lead qualification (non-agentic), parsing.
    """
    result = call_llm(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return result["content"] or ""


def call_llm_json(
    system_prompt: str,
    user_message: str,
    model: str | None = None,
    temperature: float = 0.1,
) -> dict | list:
    """
    LLM call that expects JSON output. Parses and returns structured data.
    Used for: lead qualification, data extraction from scraped content.
    """
    result = call_llm_simple(
        system_prompt=system_prompt + "\n\nRespond with ONLY valid JSON. No markdown, no backticks, no explanation.",
        user_message=user_message,
        model=model,
        temperature=temperature,
    )

    # Clean potential markdown formatting
    cleaned = result.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]  # Remove first line
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    return json.loads(cleaned)

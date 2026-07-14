from __future__ import annotations

import sys
import json
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty
from rich.text import Text
from rich.syntax import Syntax
from rich.table import Table

console = Console()

SERVICE_COLORS = {
    "api-gateway": "cyan",
    "identity-service": "blue",
    "inventory-service": "green",
    "warehouse-service": "green",
    "product-service": "green",
    "customer-service": "green",
    "ai-service": "magenta",
    "wms-mcp-server": "yellow",
    "mcp-server": "yellow",
}


def get_color(service: str) -> str:
    return SERVICE_COLORS.get(service.lower(), "white")


def format_timestamp(ts: float | str | None) -> str:
    if not ts:
        return ""
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts)
        else:
            dt = datetime.fromisoformat(str(ts))
        return dt.strftime("%H:%M:%S.%f")[:-3]
    except Exception:
        return str(ts)


def process_log_line(line: str) -> None:
    try:
        log = json.loads(line.strip())
    except Exception:
        # If it's not JSON, print it as plain text if it looks interesting
        stripped = line.strip()
        if stripped:
            console.print(stripped, style="dim")
        return

    msg = log.get("msg") or log.get("message")
    service = log.get("service", "unknown")
    level = log.get("level", "info").upper()
    ts = format_timestamp(log.get("ts") or log.get("timestamp"))
    trace_id = log.get("trace_id")
    
    color = get_color(service)
    
    header = Text()
    header.append(f"[{ts}] ", style="bold black on white")
    header.append(f" {service.upper()} ", style=f"bold white on {color}")
    header.append(f" {level} ", style="bold red" if level == "ERROR" else "bold yellow" if level == "WARNING" else "bold green")
    
    if trace_id:
        header.append(f" [Trace: {trace_id[:8]}...]", style="dim cyan")

    # 1. gRPC Request/Response Payloads
    if msg == "grpc_debug_request_payload":
        method = log.get("method", "Unknown")
        payload = log.get("payload", {})
        console.print(header)
        console.print(Panel(
            Pretty(payload),
            title=f"📥 gRPC Request -> [bold]{method}[/bold]",
            title_align="left",
            border_style="cyan"
        ))
        
    elif msg == "grpc_debug_response_payload":
        method = log.get("method", "Unknown")
        payload = log.get("payload", {})
        console.print(header)
        console.print(Panel(
            Pretty(payload),
            title=f"📤 gRPC Response <- [bold]{method}[/bold]",
            title_align="left",
            border_style="green"
        ))

    # 2. HTTP Request Summary
    elif msg == "http_request":
        method = log.get("method", "GET")
        path = log.get("path", "/")
        status = log.get("status", 200)
        dur = log.get("duration_ms", 0)
        console.print(header)
        console.print(f"🌐 [bold]{method}[/bold] {path} - Status: [bold]{status}[/bold] ({dur:.2f}ms)", style="bold cyan")

    # 3. LangGraph Trace Entries
    elif msg == "langgraph_debug_chain_start":
        name = log.get("name", "Chain")
        inputs = log.get("inputs", {})
        console.print(header)
        console.print(Panel(
            Pretty(inputs),
            title=f"🧠 LangGraph Node START: [bold]{name}[/bold]",
            title_align="left",
            border_style="magenta"
        ))

    elif msg == "langgraph_debug_chain_end":
        outputs = log.get("outputs", {})
        diff = log.get("state_diff", {})
        console.print(header)
        
        # Render a nice table showing outputs and diffs
        table = Table(box=None, expand=True)
        table.add_column("Outputs", style="magenta")
        table.add_column("State Changes (Diff)", style="yellow")
        table.add_row(Pretty(outputs), Pretty(diff))
        
        console.print(Panel(
            table,
            title="🧠 LangGraph Node END / State Updated",
            title_align="left",
            border_style="bold magenta"
        ))

    elif msg == "langgraph_debug_llm_start":
        prompts = log.get("prompts", [])
        console.print(header)
        prompt_text = "\n---\n".join(prompts)
        console.print(Panel(
            Syntax(prompt_text, "markdown", theme="monokai", word_wrap=True),
            title="🤖 LLM Prompt Input",
            title_align="left",
            border_style="bold yellow"
        ))

    elif msg == "langgraph_debug_llm_end":
        generations = log.get("generations", [])
        tokens = log.get("token_usage", {})
        gen_text = "\n---\n".join(generations)
        console.print(header)
        console.print(Panel(
            Syntax(gen_text, "markdown", theme="monokai", word_wrap=True),
            title=f"🤖 LLM Response Completion (Tokens: {tokens.get('total_tokens', 'N/A')})",
            title_align="left",
            border_style="bold green"
        ))

    elif msg == "langgraph_debug_tool_start":
        name = log.get("name", "Tool")
        inp = log.get("input", "")
        console.print(header)
        console.print(Panel(
            Syntax(str(inp), "json", word_wrap=True),
            title=f"🔌 MCP Tool call: [bold]{name}[/bold]",
            title_align="left",
            border_style="bold yellow"
        ))

    elif msg == "langgraph_debug_tool_end":
        output = log.get("output", "")
        console.print(header)
        console.print(Panel(
            Syntax(str(output), "json", word_wrap=True),
            title="🔌 MCP Tool response",
            title_align="left",
            border_style="bold green"
        ))

    # 4. Fallback default structured log printing
    else:
        # Standard microservice requests or normal logs
        fields = {k: v for k, v in log.items() if k not in ["ts", "timestamp", "service", "level", "msg", "message", "trace_id", "span_id"]}
        console.print(header)
        console.print(f"💬 {msg}", style="bold white")
        if fields:
            console.print(Pretty(fields), style="dim")


def main() -> None:
    console.print(Panel(
        "[bold green]WMS Ecosystem Live Debugger Dashboard[/bold green]\n"
        "Tailing live traces and events from stdin... Press Ctrl+C to exit.",
        border_style="bold green",
        expand=False
    ))
    
    try:
        for line in sys.stdin:
            if not line:
                break
            process_log_line(line)
    except KeyboardInterrupt:
        console.print("\n[bold red]Exiting debugger dashboard.[/bold red]")
        sys.exit(0)


if __name__ == "__main__":
    main()

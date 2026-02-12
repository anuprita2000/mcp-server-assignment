# MCP Weather Server

A Model Context Protocol (MCP) server that provides weather forecast and alert data using the US National Weather Service (NWS) API. Built following the [official MCP Python quickstart tutorial](https://modelcontextprotocol.io/quickstart/server).

## Overview

This MCP server exposes two tools:

- **`get_alerts`** — Get active weather alerts for a US state (by two-letter state code)
- **`get_forecast`** — Get a weather forecast for a specific latitude/longitude location

The server communicates over **stdio** transport using the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) and the [`FastMCP`](https://github.com/modelcontextprotocol/python-sdk) high-level API.

## Prerequisites

- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager

### Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart your terminal after installation.

## Setup

### 1. Clone / navigate to the project

```bash
cd /path/to/mcp-server-assignment
```

### 2. Initialize the project with uv

```bash
uv init
uv venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows
```

### 3. Install dependencies

```bash
uv add "mcp[cli]" httpx
```

Or install from the requirements file:

```bash
uv pip install -r requirements.txt
```

## Running the Server

```bash
uv run weather.py
```

The server listens on **stdio** for JSON-RPC messages from an MCP client.

## Connecting to Claude for Desktop

1. Open your Claude for Desktop config file:

   ```bash
   # macOS
   code ~/Library/Application\ Support/Claude/claude_desktop_config.json
   ```

2. Add the weather server configuration (or copy `claude_desktop_config.json` from this repo and update the path):

   ```json
   {
     "mcpServers": {
       "weather": {
         "command": "uv",
         "args": [
           "--directory",
           "/ABSOLUTE/PATH/TO/mcp-server-assignment",
           "run",
           "weather.py"
         ]
       }
     }
   }
   ```

   > **Note:** Replace `/ABSOLUTE/PATH/TO/mcp-server-assignment` with the actual absolute path to this project. You may also need to use the full path to `uv` (find it with `which uv`).

3. Save the file and **restart Claude for Desktop**.

4. Look for the tools icon in Claude for Desktop to verify the server connected.

## Testing

Try these prompts in Claude for Desktop:

- "What's the weather in Sacramento?"
- "What are the active weather alerts in Texas?"

> **Note:** The NWS API only supports US locations.

## Project Structure

```
mcp-server-assignment/
├── weather.py                 # MCP server implementation
├── requirements.txt           # Python dependencies
├── claude_desktop_config.json # Example MCP client configuration
└── README.md                  # This file
```

## Troubleshooting

- **Server not showing up:** Check `claude_desktop_config.json` syntax and ensure the path is absolute.
- **Check logs:** `tail -n 20 -f ~/Library/Logs/Claude/mcp*.log`
- **Tool calls failing:** Verify the server runs without errors via `uv run weather.py`.

## Documentation

| Item | Details |
|------|---------|
| **GitHub Link** | [https://github.com/anuprita2000/mcp-server-assignment](https://github.com/anuprita2000/mcp-server-assignment) |
| **Domain Choice** | Weather |
| **Best Practice 1** | **Async I/O with proper error handling** — All NWS API calls use `httpx.AsyncClient` with timeouts and `raise_for_status()`, ensuring the server never blocks and gracefully handles network failures by returning user-friendly error messages instead of crashing. |
| **Best Practice 2** | **Separation of concerns via helper functions** — API request logic (`make_nws_request`), data formatting (`format_alert`), and tool definitions (`get_alerts`, `get_forecast`) are cleanly separated. This makes the code easier to test, debug, and extend with new tools. |

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    MCP Client                           │
│              (Claude for Desktop)                       │
└──────────────────────┬──────────────────────────────────┘
                       │  JSON-RPC over stdio
                       ▼
┌─────────────────────────────────────────────────────────┐
│                  MCP Server (weather.py)                │
│  ┌───────────────────────────────────────────────────┐  │
│  │              FastMCP Framework                    │  │
│  │         (protocol handling, routing)              │  │
│  └───────────────────┬───────────────────────────────┘  │
│                      │                                  │
│          ┌───────────┴───────────┐                      │
│          ▼                       ▼                      │
│  ┌───────────────┐     ┌─────────────────┐              │
│  │  get_alerts   │     │  get_forecast   │              │
│  │    (tool)     │     │     (tool)      │              │
│  │               │     │                 │              │
│  │ Input: state  │     │ Input: lat, lon │              │
│  │   (e.g. CA)   │     │  (e.g. 38, -121)│              │
│  └───────┬───────┘     └────────┬────────┘              │
│          │                      │                       │
│          └──────────┬───────────┘                       │
│                     ▼                                   │
│  ┌───────────────────────────────────────────────────┐  │
│  │           make_nws_request (helper)               │  │
│  │     async HTTP client with error handling         │  │
│  └───────────────────┬───────────────────────────────┘  │
│                      │                                  │
│  ┌───────────────────┴───────────────────────────────┐  │
│  │           format_alert (helper)                   │  │
│  │     formats raw JSON into readable strings        │  │
│  └───────────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────────┘
                       │  HTTPS requests
                       ▼
┌─────────────────────────────────────────────────────────┐
│          National Weather Service API                   │
│            (https://api.weather.gov)                    │
│                                                         │
│  /alerts/active/area/{state}  → active weather alerts   │
│  /points/{lat},{lon}          → grid point lookup       │
│  /gridpoints/.../forecast     → detailed forecast       │
└─────────────────────────────────────────────────────────┘
```

## Reflection

### Challenges Encountered

The primary challenge during development was environment compatibility. The system's default Python version (3.9) did not meet MCP's minimum requirement of Python 3.10+. This required installing Python 3.11 via `uv`, pinning the `.python-version` file, and updating the `requires-python` field in `pyproject.toml` before dependencies could be resolved. This experience highlighted the importance of clearly documenting Python version requirements upfront and using tools like `uv` that can manage multiple Python versions seamlessly. A secondary challenge was understanding the stdio transport model — since the server communicates via standard input/output, any accidental `print()` calls would corrupt the JSON-RPC protocol, requiring careful attention to logging practices.

### Abstraction Decisions

The server exposes two tools — `get_alerts` and `get_forecast` — rather than a single monolithic "get weather" tool or a more granular set of tools. This decision was driven by how the NWS API is structured: alerts and forecasts are fundamentally different data types served by different endpoints, with different input parameters (state code vs. latitude/longitude). Combining them into one tool would have required the LLM to always specify which type of data it wanted, adding unnecessary complexity to the tool's interface. Conversely, splitting further (e.g., separate tools for "get grid point" and "get forecast from grid point") would have leaked implementation details of the NWS API's two-step forecast lookup. The chosen level of abstraction hides that complexity inside `get_forecast` while keeping each tool's purpose clear and its inputs simple.

### Limitations and Future Improvements

The server is currently limited to US locations only, since it relies exclusively on the National Weather Service API, which does not cover international locations. Adding a fallback to a global weather API (such as OpenWeatherMap or WeatherAPI) would significantly broaden its usefulness. The server also lacks caching — every request makes fresh HTTP calls to the NWS API, which could lead to rate limiting under heavy use. Implementing a simple in-memory cache with a TTL (time-to-live) would reduce redundant requests. Additionally, the server only exposes tools, not MCP resources or prompts. Adding a resource for "recent queries" or a prompt template for "weather briefing" would make the server a more complete demonstration of MCP's capabilities. Finally, the error messages returned to the user are generic; more specific error handling (distinguishing between network timeouts, invalid coordinates, and API outages) would improve the user experience.

## Sources & Citations

- [MCP Quickstart — Build an MCP Server (Python)](https://modelcontextprotocol.io/quickstart/server)
- [MCP Python SDK (GitHub)](https://github.com/modelcontextprotocol/python-sdk)
- [Quickstart Resources (GitHub)](https://github.com/modelcontextprotocol/quickstart-resources/tree/main/weather-server-python)
- [National Weather Service API](https://www.weather.gov/documentation/services-web-api)

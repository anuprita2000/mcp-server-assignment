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

## Sources & Citations

- [MCP Quickstart — Build an MCP Server (Python)](https://modelcontextprotocol.io/quickstart/server)
- [MCP Python SDK (GitHub)](https://github.com/modelcontextprotocol/python-sdk)
- [Quickstart Resources (GitHub)](https://github.com/modelcontextprotocol/quickstart-resources/tree/main/weather-server-python)
- [National Weather Service API](https://www.weather.gov/documentation/services-web-api)

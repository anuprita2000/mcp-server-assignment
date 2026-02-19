# MCP Server Assignment

This project contains two MCP (Model Context Protocol) servers for use with Claude for Desktop:

1. **Weather Server** (`weather.py`) — Provides weather forecasts and alerts using the US National Weather Service API. Built following the [official MCP Python quickstart tutorial](https://modelcontextprotocol.io/quickstart/server).
2. **Calendar Server** (`macos-calendar-mcp/`) — Schedules meetings and manages calendar events locally on macOS via AppleScript. Uses [xybstone/macos-calendar-mcp](https://github.com/xybstone/macos-calendar-mcp).

## Overview

### Weather Server Tools

- **`get_alerts`** — Get active weather alerts for a US state (by two-letter state code)
- **`get_forecast`** — Get a weather forecast for a specific latitude/longitude location

### Calendar Server Tools

- **`create-event`** — Schedule a new meeting (title, start/end time, calendar, description, location)
- **`list-calendars`** — List all available calendars on your Mac
- **`list-today-events`** — View today's schedule
- **`search-events`** — Search for events by keyword

The weather server communicates over **stdio** using the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). The calendar server communicates over **stdio** using the [MCP TypeScript SDK](https://github.com/modelcontextprotocol/typescript-sdk) and AppleScript for local Calendar.app automation.

## Prerequisites

- **Python 3.10+**
- **Node.js 16+** (for the calendar server)
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager
- **macOS** (for the calendar server — uses AppleScript)

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

### 2. Set up the Weather Server

```bash
uv init
uv venv
source .venv/bin/activate
uv add "mcp[cli]" httpx python-dotenv
```

Or install from the requirements file:

```bash
uv pip install -r requirements.txt
```

### 3. Set up the Calendar Server

```bash
cd macos-calendar-mcp
npm install
cd ..
```

## Running the Servers

### Weather Server

```bash
uv run weather.py
```

### Calendar Server

```bash
node macos-calendar-mcp/macos-calendar-mcp.js
```

Both servers listen on **stdio** for JSON-RPC messages from an MCP client.

## Connecting to Claude for Desktop

1. Open your Claude for Desktop config file:

   ```bash
   # macOS
   code ~/Library/Application\ Support/Claude/claude_desktop_config.json
   ```

2. Add both server configurations (or copy `claude_desktop_config.json` from this repo and update paths):

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
         ],
         "env": {
           "NWS_API_BASE": "https://api.weather.gov",
           "NWS_USER_AGENT": "weather-app/1.0"
         }
       },
       "macos-calendar": {
         "command": "node",
         "args": [
           "/ABSOLUTE/PATH/TO/mcp-server-assignment/macos-calendar-mcp/macos-calendar-mcp.js"
         ]
       }
     }
   }
   ```

   > **Note:** Replace `/ABSOLUTE/PATH/TO/mcp-server-assignment` with the actual absolute path to this project. You may also need to use the full path to `uv` (find it with `which uv`) and `node` (find it with `which node`).

3. Save the file and **restart Claude for Desktop**.

4. Look for the tools icon in Claude for Desktop to verify the server connected.

## Testing

Try these prompts in Claude for Desktop:

**Weather:**
- "What's the weather in Sacramento?"
- "What are the active weather alerts in Texas?"

**Calendar:**
- "What calendars do I have?"
- "Schedule a meeting called 'Team Standup' tomorrow at 10am for 30 minutes"
- "What's on my calendar today?"

> **Note:** The NWS API only supports US locations. The calendar server only works on macOS.

### macOS Calendar Permissions

On first use, macOS will prompt you to grant Calendar access. Click **Allow**. If you miss the prompt, go to **System Settings > Privacy & Security > Calendars** and enable access for the terminal/Claude Desktop app.

## Project Structure

```
mcp-server-assignment/
├── weather.py                 # Weather MCP server (Python)
├── macos-calendar-mcp/        # Calendar MCP server (Node.js, AppleScript)
│   ├── macos-calendar-mcp.js  # Main calendar server entry point
│   ├── package.json           # Node.js dependencies
│   └── README.md              # Calendar server docs
├── requirements.txt           # Python dependencies
├── .env.example               # Environment variable template
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
| **Domain Choice** | Weather + Calendar (Meeting Scheduling) |
| **Best Practice 1** | **Async I/O with proper error handling** — All NWS API calls use `httpx.AsyncClient` with timeouts and `raise_for_status()`, ensuring the server never blocks and gracefully handles network failures by returning user-friendly error messages instead of crashing. |
| **Best Practice 2** | **Separation of concerns via helper functions** — API request logic (`make_nws_request`), data formatting (`format_alert`), and tool definitions (`get_alerts`, `get_forecast`) are cleanly separated. This makes the code easier to test, debug, and extend with new tools. |

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                         MCP Client                                   │
│                   (Claude for Desktop)                               │
└────────────┬─────────────────────────────────┬───────────────────────┘
             │ JSON-RPC over stdio             │ JSON-RPC over stdio
             ▼                                 ▼
┌────────────────────────────┐   ┌──────────────────────────────────┐
│  Weather Server            │   │  Calendar Server                 │
│  (weather.py — Python)     │   │  (macos-calendar-mcp — Node.js)  │
│                            │   │                                  │
│  ┌──────────────────────┐  │   │  ┌────────────────────────────┐  │
│  │  FastMCP Framework   │  │   │  │  MCP TypeScript SDK        │  │
│  └──────────┬───────────┘  │   │  └────────────┬───────────────┘  │
│       ┌─────┴─────┐       │   │    ┌───────────┼───────────┐     │
│       ▼           ▼       │   │    ▼           ▼           ▼     │
│  ┌─────────┐ ┌─────────┐  │   │ ┌──────┐ ┌─────────┐ ┌───────┐  │
│  │  get    │ │  get    │  │   │ │create│ │  list   │ │search │  │
│  │ alerts  │ │forecast │  │   │ │-event│ │calendars│ │-events│  │
│  └────┬────┘ └────┬────┘  │   │ └──┬───┘ └────┬────┘ └───┬───┘  │
│       └─────┬─────┘       │   │    └─────┬─────┘         │      │
│             ▼             │   │          ▼               ▼      │
│  ┌──────────────────────┐  │   │  ┌────────────────────────────┐  │
│  │  make_nws_request    │  │   │  │     AppleScript Engine     │  │
│  │  (async HTTP client) │  │   │  │   (local OS automation)    │  │
│  └──────────┬───────────┘  │   │  └────────────┬───────────────┘  │
└─────────────┼──────────────┘   └───────────────┼──────────────────┘
              │ HTTPS                            │ Local IPC
              ▼                                  ▼
┌────────────────────────────┐   ┌──────────────────────────────────┐
│  National Weather Service  │   │   macOS Calendar.app             │
│  API (api.weather.gov)     │   │   (iCloud/Google/Exchange/       │
│                            │   │    Outlook calendars)            │
│  /alerts/active/area/{st}  │   │                                  │
│  /points/{lat},{lon}       │   │   No internet required.          │
│  /gridpoints/.../forecast  │   │   No email access. Calendar only.│
└────────────────────────────┘   └──────────────────────────────────┘
```

### Security: Calendar Server Permissions

The calendar server **cannot** access email, contacts, or any other data. It only uses macOS AppleScript to talk to Calendar.app. Even if Claude "tried" to read email, the server has no code or capability to do so — it is physically limited to calendar operations only.

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
- [macOS Calendar MCP Server (GitHub)](https://github.com/xybstone/macos-calendar-mcp) — Calendar server used for meeting scheduling

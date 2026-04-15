# Freshworks → Claude Code Setup Guide
**For: Mario @ Wisewell**

This guide connects all three Freshworks products to Claude Code via MCP servers.

---

## What You'll Get

| Product | MCP Server | What Claude Can Do |
|---|---|---|
| **Freshdesk** | effytech/freshdesk_mcp | View/search tickets, contacts, agents, companies |
| **Freshsales** | heathweaver/freshsales-mcp-server | View CRM deals, contacts, pipeline |
| **Freshchat** | Custom (built for you) | Live chats, messages, contacts, agents, channels |

---

## Before You Start — Gather Your API Keys

### Freshdesk API Key
1. Log in to your Freshdesk account → **Profile Settings** (top right)
2. Scroll to "Your API Key" and copy it
3. Your domain is the part before `.freshdesk.com` (e.g. `wisewell`)

### Freshsales API Key
1. Log in to Freshsales → **Settings** → **API Settings**
2. Copy the API key
3. Your domain is `wisewell-team` (from your URL)

### Freshchat API Token
1. Log in to Freshchat (or Freshworks CRM → Messaging tab)
2. Go to **Settings** → **Integration Settings** → **API Tokens**
3. Generate a new API token and copy it
4. Your domain is `wisewell-team.freshchat.com`

---

## Step 1 — Install the Freshchat MCP Server

The Freshchat MCP server is the custom one built for you. Save the `freshchat-mcp` folder (included with this guide) somewhere permanent on your Mac, for example:

```
~/mcp-servers/freshchat-mcp/
```

Then install its dependencies **once**:

```bash
cd ~/mcp-servers/freshchat-mcp
npm install
```

---

## Step 2 — Configure Claude Code

Open your Claude Code config file. In your terminal:

```bash
open ~/.claude.json
```

If it doesn't exist yet, create it. Add the following (replace the placeholder values with your real keys/domains):

```json
{
  "mcpServers": {

    "freshdesk": {
      "command": "npx",
      "args": ["-y", "@effytech/freshdesk_mcp"],
      "env": {
        "FRESHDESK_DOMAIN": "wisewell",
        "FRESHDESK_API_KEY": "YOUR_FRESHDESK_API_KEY_HERE"
      }
    },

    "freshsales": {
      "command": "npx",
      "args": ["-y", "freshsales-mcp-server"],
      "env": {
        "FRESHSALES_DOMAIN": "wisewell-team",
        "FRESHSALES_API_KEY": "YOUR_FRESHSALES_API_KEY_HERE"
      }
    },

    "freshchat": {
      "command": "node",
      "args": ["/Users/YOUR_USERNAME/mcp-servers/freshchat-mcp/index.js"],
      "env": {
        "FRESHCHAT_API_TOKEN": "YOUR_FRESHCHAT_API_TOKEN_HERE",
        "FRESHCHAT_DOMAIN": "wisewell-team.freshchat.com"
      }
    }

  }
}
```

> **Important:** Replace `/Users/YOUR_USERNAME/` with your actual Mac username path.
> You can find it by running `echo $HOME` in terminal.

---

## Step 3 — Install the Freshsales MCP

In your terminal, run:

```bash
npm install -g freshsales-mcp-server
```

> Note: If the `freshsales-mcp-server` npm package isn't found, use the GitHub version:
> ```bash
> git clone https://github.com/heathweaver/freshsales-mcp-server.git ~/mcp-servers/freshsales-mcp
> cd ~/mcp-servers/freshsales-mcp && npm install
> ```
> Then update the `freshsales` entry in your config to `"command": "node"` and `"args": ["/Users/YOUR_USERNAME/mcp-servers/freshsales-mcp/index.js"]`

---

## Step 4 — Restart Claude Code & Test

1. Fully quit and relaunch Claude Code
2. Start a new conversation and type:

```
Show me my latest Freshchat conversations
```

or

```
What open Freshdesk tickets do we have today?
```

Claude should respond by calling the MCP tools and returning live data.

---

## Example Prompts Once Connected

- *"Show me all new Freshchat conversations assigned to no one"*
- *"Summarize the open Freshdesk tickets from this week"*
- *"Which sales agent has the most active chats right now?"*
- *"Search Freshdesk for tickets mentioning 'billing'"*
- *"List all Freshsales deals in the pipeline"*
- *"Show me messages from conversation ID [X]"*

---

## Troubleshooting

**MCP not connecting?**
- Make sure the `freshchat-mcp` folder path in your config matches exactly where you saved it
- Run `node ~/mcp-servers/freshchat-mcp/index.js` directly in terminal to check for errors

**API returning 401 Unauthorized?**
- Double-check your API key/token — copy it fresh from the Freshworks admin panel
- For Freshchat, make sure you're using an API **token** (not an app ID)

**Freshsales MCP not found?**
- Fall back to the GitHub clone method in Step 3

---

*Freshchat MCP server built by Claude for Wisewell — April 2026*

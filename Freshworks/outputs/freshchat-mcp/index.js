#!/usr/bin/env node
/**
 * Freshchat MCP Server
 * Connects Claude to Freshchat for live sales chat visibility
 */

const { Server } = require("@modelcontextprotocol/sdk/server/index.js");
const { StdioServerTransport } = require("@modelcontextprotocol/sdk/server/stdio.js");
const { CallToolRequestSchema, ListToolsRequestSchema } = require("@modelcontextprotocol/sdk/types.js");
const axios = require("axios");

const API_TOKEN = process.env.FRESHCHAT_API_TOKEN;
const DOMAIN = process.env.FRESHCHAT_DOMAIN; // e.g. "wisewell-team.freshchat.com"

if (!API_TOKEN || !DOMAIN) {
  console.error("Missing required env vars: FRESHCHAT_API_TOKEN, FRESHCHAT_DOMAIN");
  process.exit(1);
}

const BASE_URL = `https://${DOMAIN}/v2`;

const client = axios.create({
  baseURL: BASE_URL,
  headers: {
    Authorization: `Bearer ${API_TOKEN}`,
    "Content-Type": "application/json",
  },
});

// Helper: safe API call with error handling
async function apiCall(method, path, params = {}, data = null) {
  try {
    const config = { method, url: path, params };
    if (data) config.data = data;
    const res = await client(config);
    return { ok: true, data: res.data };
  } catch (err) {
    const msg = err.response?.data?.message || err.message;
    return { ok: false, error: msg };
  }
}

// Tool definitions
const TOOLS = [
  {
    name: "freshchat_list_conversations",
    description: "List Freshchat conversations (sales chats). Filter by status, assigned agent, or page. Returns conversation IDs, status, assigned agent, contact info, and last message time.",
    inputSchema: {
      type: "object",
      properties: {
        status: { type: "string", enum: ["new", "assigned", "resolved", "reopened"], description: "Filter by conversation status" },
        assigned_agent_id: { type: "string", description: "Filter by agent ID" },
        page: { type: "number", description: "Page number (default 1)" },
        items_per_page: { type: "number", description: "Results per page (default 20, max 50)" },
      },
    },
  },
  {
    name: "freshchat_get_conversation",
    description: "Get details of a specific Freshchat conversation by ID, including messages, contact, and assigned agent.",
    inputSchema: {
      type: "object",
      properties: {
        conversation_id: { type: "string", description: "The conversation ID" },
      },
      required: ["conversation_id"],
    },
  },
  {
    name: "freshchat_get_messages",
    description: "Get all messages in a specific Freshchat conversation.",
    inputSchema: {
      type: "object",
      properties: {
        conversation_id: { type: "string", description: "The conversation ID" },
        page: { type: "number", description: "Page number (default 1)" },
      },
      required: ["conversation_id"],
    },
  },
  {
    name: "freshchat_list_contacts",
    description: "List Freshchat contacts (customers/leads). Search by email or name.",
    inputSchema: {
      type: "object",
      properties: {
        email: { type: "string", description: "Filter by email address" },
        page: { type: "number", description: "Page number" },
        items_per_page: { type: "number", description: "Results per page (default 20)" },
      },
    },
  },
  {
    name: "freshchat_get_contact",
    description: "Get details of a specific contact by ID.",
    inputSchema: {
      type: "object",
      properties: {
        contact_id: { type: "string", description: "The contact ID" },
      },
      required: ["contact_id"],
    },
  },
  {
    name: "freshchat_list_agents",
    description: "List all Freshchat agents (sales reps) and their availability status.",
    inputSchema: {
      type: "object",
      properties: {
        page: { type: "number", description: "Page number" },
      },
    },
  },
  {
    name: "freshchat_list_channels",
    description: "List all Freshchat channels/inboxes (e.g. Website Chat, WhatsApp, etc.).",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "freshchat_search_conversations",
    description: "Search conversations by keyword in messages, or filter by channel, date range, or label.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query" },
        channel_id: { type: "string", description: "Filter by channel ID" },
        from_date: { type: "string", description: "Start date (ISO 8601, e.g. 2026-01-01)" },
        to_date: { type: "string", description: "End date (ISO 8601)" },
        label: { type: "string", description: "Filter by label/tag" },
      },
    },
  },
  {
    name: "freshchat_send_reply",
    description: "Send a reply message in a Freshchat conversation on behalf of an agent.",
    inputSchema: {
      type: "object",
      properties: {
        conversation_id: { type: "string", description: "The conversation ID to reply to" },
        message: { type: "string", description: "The reply message text" },
        agent_id: { type: "string", description: "Agent ID sending the reply" },
      },
      required: ["conversation_id", "message"],
    },
  },
];

// Tool handlers
async function handleTool(name, args) {
  switch (name) {
    case "freshchat_list_conversations": {
      const params = { page: args.page || 1, items_per_page: args.items_per_page || 20 };
      if (args.status) params.status = args.status;
      if (args.assigned_agent_id) params.assigned_agent_id = args.assigned_agent_id;
      const result = await apiCall("GET", "/conversations", params);
      if (!result.ok) return `Error: ${result.error}`;
      const convos = result.data.conversations || [];
      if (!convos.length) return "No conversations found.";
      return JSON.stringify(convos.map(c => ({
        id: c.conversation_id,
        status: c.status,
        created_at: c.created_time,
        updated_at: c.updated_time,
        assigned_agent: c.assigned_agent_id,
        contact: c.user_id,
        channel: c.channel_id,
        messages_count: c.messages_count,
      })), null, 2);
    }

    case "freshchat_get_conversation": {
      const result = await apiCall("GET", `/conversations/${args.conversation_id}`);
      if (!result.ok) return `Error: ${result.error}`;
      return JSON.stringify(result.data, null, 2);
    }

    case "freshchat_get_messages": {
      const params = { page: args.page || 1 };
      const result = await apiCall("GET", `/conversations/${args.conversation_id}/messages`, params);
      if (!result.ok) return `Error: ${result.error}`;
      const msgs = result.data.messages || [];
      if (!msgs.length) return "No messages found.";
      return JSON.stringify(msgs.map(m => ({
        id: m.id,
        created_at: m.created_time,
        actor_type: m.actor_type, // agent or user
        actor_id: m.actor_id,
        message_parts: m.message_parts,
      })), null, 2);
    }

    case "freshchat_list_contacts": {
      const params = { page: args.page || 1, items_per_page: args.items_per_page || 20 };
      if (args.email) params.email = args.email;
      const result = await apiCall("GET", "/contacts", params);
      if (!result.ok) return `Error: ${result.error}`;
      const contacts = result.data.contacts || [];
      if (!contacts.length) return "No contacts found.";
      return JSON.stringify(contacts.map(c => ({
        id: c.id,
        first_name: c.first_name,
        last_name: c.last_name,
        email: c.email,
        phone: c.phone,
        created_at: c.created_time,
        properties: c.properties,
      })), null, 2);
    }

    case "freshchat_get_contact": {
      const result = await apiCall("GET", `/contacts/${args.contact_id}`);
      if (!result.ok) return `Error: ${result.error}`;
      return JSON.stringify(result.data, null, 2);
    }

    case "freshchat_list_agents": {
      const params = { page: args.page || 1 };
      const result = await apiCall("GET", "/agents", params);
      if (!result.ok) return `Error: ${result.error}`;
      const agents = result.data.agents || [];
      if (!agents.length) return "No agents found.";
      return JSON.stringify(agents.map(a => ({
        id: a.id,
        first_name: a.first_name,
        last_name: a.last_name,
        email: a.email,
        availability_status: a.availability_status,
        role_id: a.role_id,
      })), null, 2);
    }

    case "freshchat_list_channels": {
      const result = await apiCall("GET", "/channels");
      if (!result.ok) return `Error: ${result.error}`;
      return JSON.stringify(result.data, null, 2);
    }

    case "freshchat_search_conversations": {
      const params = {};
      if (args.query) params.filter_type = "query", params.filter_value = args.query;
      if (args.channel_id) params.channel_id = args.channel_id;
      if (args.from_date) params.from_date = args.from_date;
      if (args.to_date) params.to_date = args.to_date;
      if (args.label) params.label = args.label;
      const result = await apiCall("GET", "/conversations", params);
      if (!result.ok) return `Error: ${result.error}`;
      return JSON.stringify(result.data, null, 2);
    }

    case "freshchat_send_reply": {
      const data = {
        message_parts: [{ text: { content: args.message } }],
        actor_type: "agent",
        actor_id: args.agent_id || undefined,
      };
      const result = await apiCall("POST", `/conversations/${args.conversation_id}/messages`, {}, data);
      if (!result.ok) return `Error: ${result.error}`;
      return `Reply sent successfully. Message ID: ${result.data.id}`;
    }

    default:
      return `Unknown tool: ${name}`;
  }
}

// MCP Server setup
const server = new Server(
  { name: "freshchat-mcp", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  const result = await handleTool(name, args || {});
  return {
    content: [{ type: "text", text: result }],
  };
});

// Start server
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("Freshchat MCP server running...");
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});

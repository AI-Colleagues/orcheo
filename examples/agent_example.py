# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.16.7
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Example of using Agent node

# %%
import asyncio
import os
from typing import Any
from dotenv import load_dotenv
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.base import TaskNode
from orcheo.nodes.connectors.telegram import MessageTelegram


load_dotenv()


model_settings = {
    "model": "gpt-4o-mini",
    "api_key": os.getenv("OPENAI_API_KEY"),
}


class GreetingTaskNode(TaskNode):
    """Simple in-file task node used by the sub-graph tool example."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Run the task node."""
        del config
        name = state["results"]["initial"]
        return {"messages": [{"role": "ai", "content": f"Hello, {name}."}]}


# %% [markdown]
# ## Structured output with vanilla JSON dict

# %%
so_schema = """
json_dict = {
    "type": "object",
    "title": "Person",
    "description": "A person",
    "properties": {
        "name": {"type": "string"},
    },
    "required": ["name"],
}
"""

so_config = {
    "schema_type": "json_dict",
    "schema_str": so_schema,
}

agent_node = AgentNode(
    name="agent",
    model_settings=model_settings,
    structured_output=so_config,
    system_prompt="Your name is John Doe.",
    checkpointer="memory",
)
config = {"configurable": {"thread_id": "123"}}
result = await agent_node(  # noqa: F704, PLE1142
    {"messages": [{"role": "user", "content": "What's your name?"}]}, config
)

result["structured_response"]

# %% [markdown]
# ## Structured output with OpenAI JSON dict

# %%
so_schema = """
oai_json_schema = {
    "name": "get_person",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
        "required": ["name"],
    },
}
"""

so_config = {
    "schema_type": "json_dict",
    "schema_str": so_schema,
}

agent_node = AgentNode(
    name="agent",
    model_settings=model_settings,
    structured_output=so_config,
    system_prompt="Your name is John Doe.",
    checkpointer="memory",
)
config = {"configurable": {"thread_id": "123"}}
result = await agent_node(  # noqa: F704, PLE1142
    {"messages": [{"role": "user", "content": "What's your name?"}]}, config
)

result["structured_response"]

# %% [markdown]
# ## Structured output with Pydantic Models

# %%
so_schema = """
class Person(BaseModel):
    \"\"\"A Person.\"\"\"

    name: str
"""

so_config = {
    "schema_type": "json_dict",
    "schema_str": so_schema,
}

agent_node = AgentNode(
    name="agent",
    model_settings=model_settings,
    structured_output=so_config,
    system_prompt="Your name is John Doe.",
    checkpointer="memory",
)
config = {"configurable": {"thread_id": "123"}}
result = await agent_node(  # noqa: F704, PLE1142
    {"messages": [{"role": "user", "content": "What's your name?"}]}, config
)

result["structured_response"]

# %% [markdown]
# ## Structured output with Typed dict

# %%
so_schema = """
class Person(TypedDict):
    \"\"\"A Person.\"\"\"

    name: str
"""

so_config = {
    "schema_type": "typed_dict",
    "schema_str": so_schema,
}

agent_node = AgentNode(
    name="agent",
    model_settings=model_settings,
    structured_output=so_config,
    system_prompt="Your name is John Doe.",
    checkpointer="memory",
)
config = {"configurable": {"thread_id": "123"}}
result = await agent_node(  # noqa: F704, PLE1142
    {"messages": [{"role": "user", "content": "What's your name?"}]}, config
)

result["structured_response"]

# %% [markdown]
# ## Use a node as a tool

# %% [markdown]
# Main differences of a graph node and a function tool: A graph node only
# receives `state: dict` as input, and returns a dict, while a function tool
# can have arbitrary inputs.

# %%
telegram_node = MessageTelegram(
    name="MessageTelegram",
    token=os.getenv("TELEGRAM_TOKEN"),
)

agent_node = AgentNode(
    name="agent",
    model_settings=model_settings,
    tools=[telegram_node],
    system_prompt="Your name is John Doe.",
    checkpointer="memory",
)

config = {"configurable": {"thread_id": "123"}}
result = await agent_node(  # noqa: F704, PLE1142
    {
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Say hello to {os.getenv('TELEGRAM_CHAT_ID')} using "
                    "message_telegram tool"
                ),
            }
        ]
    },
    config,
)

result["messages"][-2]

# %% [markdown]
# ## Use tools from MCP servers

# %% [markdown]
# ### Initialize the MCP client

# %%
mcp_servers = {
    "filesystem": {
        "command": "npx",
        "args": [
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "~/Desktop",
        ],
        "transport": "stdio",
    },
    "git": {
        "command": "uvx",
        "args": ["mcp-server-git"],
        "transport": "stdio",
    },
}

client = MultiServerMCPClient(mcp_servers)

tools = await client.get_tools()  # noqa: F704, PLE1142

# %% [markdown]
# ### Pass the tools to the agent

# %%
agent_node = AgentNode(
    name="agent",
    model_settings=model_settings,
    tools=tools,
    system_prompt="You have access to the filesystem and git.",
    checkpointer="memory",
)

config = {"configurable": {"thread_id": "123"}}
result = await agent_node(  # noqa: F704, PLE1142
    {
        "messages": [
            {
                "role": "user",
                "content": (
                    "Hello, tell me what's on my desktop and what's the git "
                    "status of it."
                ),
            }
        ]
    },
    config,
)

print(result["messages"][-1].content)

# %% [markdown]
# ## Use sub-graph as a tool

# %% [markdown]
# ### Define a sub-graph

# %%
python_code_node = GreetingTaskNode(name="greeting_task")

tool_graph = StateGraph(State)
tool_graph.add_node("python_code", python_code_node)
tool_graph.add_edge(START, "python_code")
tool_graph.add_edge("python_code", END)

python_code_graph = tool_graph.compile()


# %% [markdown]
# ### Wrap the sub-graph as a tool


# %%
@tool(parse_docstring=True)
def greet(name: str) -> dict:
    """Greet the user.

    Args:
        name: The name of the user to greet.
    """
    result = asyncio.run(
        python_code_graph.ainvoke(
            {
                "messages": [],
                "results": {"initial": name},
                "inputs": {},
            },
            config={},
        )
    )
    return result["results"]["greeting_task"]


# %% [markdown]
# ### Use the tool in an agent

# %%
agent_node = AgentNode(
    name="agent",
    model_settings=model_settings,
    tools=[greet],
    system_prompt="Your name is John Doe.",
    checkpointer="memory",
)

config = {"configurable": {"thread_id": "123"}}
result = await agent_node(  # noqa: F704, PLE1142
    {
        "messages": [
            {
                "role": "user",
                "content": ("Hello, my name is John Doe"),
            }
        ]
    },
    config,
)

result["messages"]

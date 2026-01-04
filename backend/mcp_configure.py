import sys
import os
from typing import Union, Dict
import json
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters, stdio_client
from ptt_utils import remove_descriptions
from mcp_manager import build_tool_from_schema

# ✅ Apply nest_asyncio at module level
try:
    import nest_asyncio
    nest_asyncio.apply()
    print("✅ Applied nest_asyncio patch")
except ImportError:
    print("⚠️ nest_asyncio not installed")

def load_config() -> Union[Dict, None]:
    """Load MCP configuration from mcp.json"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    possible_paths = [
        os.path.join(current_dir, "mcp.json"),
        os.path.join(current_dir, "..", "mcp.json"),
        os.path.join(current_dir, "..", "Shared_Services", "mcp.json"),
        os.path.join(current_dir, "..", "..", "Shared_Services", "mcp.json"),
    ]
    
    config_path = None
    for path in possible_paths:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            config_path = abs_path
            print(f"✅ Found mcp.json at: {config_path}")
            break
    
    if not config_path:
        print(f"❌ mcp.json not found. Tried these paths:")
        for path in possible_paths:
            print(f"   - {os.path.abspath(path)}")
        return None, None
    
    try:
        with open(config_path) as f:
            config = json.load(f)
            mcp_servers = config.get("mcpServers", {})
            
            if not mcp_servers:
                print("⚠️ Warning: No MCP servers found in config")
                return {}, os.path.dirname(config_path)
            
            print(f"✅ Loaded {len(mcp_servers)} MCP servers from config")
            return mcp_servers, os.path.dirname(config_path)
            
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in mcp.json: {e}")
        return None, None
    except Exception as e:
        print(f"❌ Unable to open Config at path {config_path}: {e}")
        return None, None


async def configure_mcp():
    """Configure and initialize all MCP servers"""
    mcp_servers, base_path = load_config()
    
    if mcp_servers is None:
        print("❌ Failed to load MCP configuration")
        print("⚠️ Running without MCP tools")
        return AsyncExitStack(), {}, [], {}
    
    if not mcp_servers:
        print("⚠️ No MCP servers configured")
        return AsyncExitStack(), {}, [], {}
    
    input_schemas = {}
    name_to_tool = {}
    tools_list = []
    stack = AsyncExitStack()
    
    try:
        await stack.__aenter__()
        
        for server_name, server_info in mcp_servers.items():
            try:
                print(f"🔌 Connecting to server: {server_name}...")
                
                cmd = server_info["command"]
                if cmd == "python" or cmd == "python3":
                    cmd = sys.executable 
                
                resolved_args = []
                for arg in server_info["args"]:
                    potential_path = os.path.join(base_path, arg)
                    if os.path.exists(potential_path):
                        resolved_args.append(potential_path)
                    else:
                        resolved_args.append(arg)
                
                current_env = os.environ.copy()
                if "env" in server_info and server_info["env"]:
                    current_env.update(server_info["env"])
                
                server_param = StdioServerParameters(
                    command=cmd,
                    args=resolved_args, 
                    env=current_env
                )
                
                read, write = await stack.enter_async_context(stdio_client(server_param))
                session = await stack.enter_async_context(
                    ClientSession(read_stream=read, write_stream=write)
                )
                
                await session.initialize()
                print(f"✅ Session initialized for {server_name}")
                
                server_tools = await session.list_tools()
                
                for tool in server_tools.tools:
                    clean_schema = remove_descriptions(tool.inputSchema, max_length=200)
                    input_schemas[tool.name] = clean_schema
                    create_tool = build_tool_from_schema(
                        tool.name, 
                        tool.description, 
                        clean_schema, 
                        session
                    )
                    name_to_tool[tool.name] = create_tool
                    tools_list.append(create_tool)
                
                print(f"✅ Loaded {len(server_tools.tools)} tools from {server_name}")
                    
            except Exception as e:
                print(f"⚠️ Failed to load tools from {server_name}: {e}")
                continue
        
        if not tools_list:
            print("⚠️ No MCP tools loaded - Agent will run without tools")
        else:
            print(f"✅ Successfully loaded {len(tools_list)} MCP tools total")
        
        return stack, input_schemas, tools_list, name_to_tool
        
    except Exception as e:
        print(f"❌ MCP configuration error: {e}")
        import traceback
        traceback.print_exc()
        await stack.aclose()
        raise e
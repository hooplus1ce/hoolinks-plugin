
import asyncio, json, sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    server_params = StdioServerParameters(
        command=r"D:\Developer\ScoopApps\shims\uv.exe",
        args=["run", "fastmcp", "run", "fastmcp.json", "--skip-env"],
        cwd=r"D:\Developer\Hoolinks\hoolinks-plugin\mcp\qa-automation",
        env={"WORK_DIR": r"D:\Developer\Hoolinks\hoolinks-plugin","PLUGIN_ROOT": r"D:\Developer\Hoolinks\hoolinks-plugin","PLUGIN_DATA": r"D:\Developer\Hoolinks\hoolinks-plugin","PYTHONUNBUFFERED": "1"},
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(json.dumps(names, ensure_ascii=False, indent=1))
asyncio.run(main())

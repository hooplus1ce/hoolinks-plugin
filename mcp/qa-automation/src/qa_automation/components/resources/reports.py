from fastmcp.resources import resource


@resource("config://app")
def get_app_config() -> str:
    """Return the application configuration as JSON."""
    return '{"name": "qa-mcp", "version": "0.1.0"}'

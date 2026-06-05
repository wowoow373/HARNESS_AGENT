"""WebSearchTool — 模拟网络搜索工具。"""

from typing import Any, Dict

from harness.interfaces.types import ToolDefinition, ToolResult
from harness.components.tool.base import BaseTool


class WebSearchTool(BaseTool):
    """模拟网络搜索工具。

    根据 query 关键词返回预设的模拟结果。
    """

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="web_search",
            description="Search the web for information",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                },
                "required": ["query"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        query = args.get("query", "")
        if not query:
            return ToolResult(
                success=False, content=None, error="Missing 'query' argument"
            )

        query_lower = query.lower()

        if "weather" in query_lower or "天气" in query:
            content = (
                "Search Results for weather:\n"
                "1. Weather.com - Today's forecast: sunny with a high of 28C.\n"
                "2. AccuWeather - Weekend outlook: clear skies, perfect for outdoor activities.\n"
                "3. BBC Weather - Global weather patterns shifting due to El Nino."
            )
        elif "news" in query_lower or "新闻" in query:
            content = (
                "Search Results for news:\n"
                "1. Reuters - Global markets rally as inflation data shows improvement.\n"
                "2. BBC News - Major diplomatic breakthrough in Middle East peace talks.\n"
                "3. TechCrunch - Startup funding rebounds in Q2 after slow start to the year."
            )
        elif "tech" in query_lower or "科技" in query:
            content = (
                "Search Results for tech:\n"
                "1. The Verge - AI adoption accelerates across enterprise software.\n"
                "2. Ars Technica - New semiconductor breakthrough promises 2nm chips by 2026.\n"
                "3. Wired - Quantum computing reaches new milestone with 1000-qubit processor."
            )
        else:
            content = (
                f"Search Results for '{query}':\n"
                "1. Wikipedia - Comprehensive overview and related topics.\n"
                "2. Google Scholar - Academic papers and research articles.\n"
                "3. Reddit - Community discussions and user opinions."
            )

        return ToolResult(success=True, content=content)

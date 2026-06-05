"""WeatherTool — 模拟天气查询工具。"""

from typing import Any, Dict

from harness.interfaces.types import ToolDefinition, ToolResult
from harness.components.tool.base import BaseTool


class WeatherTool(BaseTool):
    """模拟天气查询工具。

    根据 city 返回预设的模拟天气数据。
    """

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="weather",
            description="Get weather information for a city",
            parameters={
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "The city name to query weather for",
                    },
                    "date": {
                        "type": "string",
                        "description": "The date to query weather for (default: today)",
                        "default": "today",
                    },
                },
                "required": ["city"],
            },
        )

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        city = args.get("city", "")
        date = args.get("date", "today")

        if not city:
            return ToolResult(
                success=False, content=None, error="Missing 'city' argument"
            )

        city_lower = city.lower()

        weather_db: Dict[str, str] = {
            "beijing": "Sunny, 28C / 18C, humidity 45%, light breeze",
            "shanghai": "Cloudy, 26C / 21C, humidity 65%, occasional drizzle",
            "guangzhou": "Thunderstorms, 31C / 25C, humidity 80%, strong winds",
            "shenzhen": "Partly cloudy, 30C / 26C, humidity 75%, gentle breeze",
            "hangzhou": "Overcast, 25C / 19C, humidity 60%, calm",
            "chengdu": "Light rain, 22C / 17C, humidity 70%, foggy morning",
            "wuhan": "Clear, 29C / 20C, humidity 50%, sunny all day",
            "xian": "Dry, 27C / 16C, humidity 35%, dusty winds",
            "nanjing": "Misty, 24C / 18C, humidity 68%, low visibility",
            "chongqing": "Hot, 33C / 24C, humidity 72%, heat advisory",
            "tokyo": "Rainy, 23C / 19C, humidity 78%, umbrella recommended",
            "new york": "Cool, 18C / 12C, humidity 55%, windy",
            "london": "Drizzle, 15C / 10C, humidity 82%, typical British weather",
            "paris": "Pleasant, 20C / 14C, humidity 58%, clear skies",
            "sydney": "Warm, 22C / 16C, humidity 60%, sunny afternoon",
        }

        weather = weather_db.get(
            city_lower,
            "Partly cloudy, 24C / 18C, humidity 55%, mild conditions"
        )

        content = f"Weather for {city} ({date}): {weather}"
        return ToolResult(success=True, content=content)

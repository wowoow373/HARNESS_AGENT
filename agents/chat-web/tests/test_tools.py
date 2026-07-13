"""Unit tests for chat-web consumer tools.

Coverage:
    - WebSearchTool: definition, keyword-based mock results, empty query
    - WeatherTool: definition, city lookup, unknown city fallback, date parameter
    - BaseTool inheritance compliance
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure project root and chat-web dir are on path
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_CHAT_WEB = Path(__file__).resolve().parents[1]
if str(_CHAT_WEB) not in sys.path:
    sys.path.insert(0, str(_CHAT_WEB))

from tools import WebSearchTool, WeatherTool
from harness.components.tool.base import BaseTool
from harness.interfaces.types import ToolDefinition, ToolResult


# ---------------------------------------------------------------------------
# WebSearchTool tests
# ---------------------------------------------------------------------------


class TestWebSearchTool:
    """Tests for WebSearchTool."""

    @pytest.fixture
    def tool(self):
        return WebSearchTool()

    def test_is_base_tool(self, tool):
        """WebSearchTool should inherit from BaseTool."""
        assert isinstance(tool, BaseTool)

    def test_definition_name(self, tool):
        """Tool name should be 'web_search'."""
        defn = tool.get_definition()
        assert defn.name == "web_search"

    def test_definition_description(self, tool):
        """Tool should have a non-empty description."""
        defn = tool.get_definition()
        assert "search" in defn.description.lower()

    def test_definition_has_query_param(self, tool):
        """Schema should have a 'query' parameter."""
        defn = tool.get_definition()
        params = defn.parameters
        assert "properties" in params
        assert "query" in params["properties"]
        assert "required" in params
        assert "query" in params["required"]

    def test_execute_weather_keyword(self, tool):
        """Query with 'weather' should return weather-related results."""
        result = tool.execute({"query": "weather today"})
        assert result.success is True
        assert "weather" in str(result.content).lower()

    def test_execute_chinese_weather_keyword(self, tool):
        """Query with Chinese '天气' should return weather results."""
        result = tool.execute({"query": "今天天气怎么样"})
        assert result.success is True
        assert "Weather" in str(result.content) or "weather" in str(result.content).lower()

    def test_execute_news_keyword(self, tool):
        """Query with 'news' should return news-related results."""
        result = tool.execute({"query": "latest news"})
        assert result.success is True
        assert "news" in str(result.content).lower()

    def test_execute_chinese_news_keyword(self, tool):
        """Query with Chinese '新闻' should return news results."""
        result = tool.execute({"query": "财经新闻"})
        assert result.success is True

    def test_execute_tech_keyword(self, tool):
        """Query with 'tech' should return tech-related results."""
        result = tool.execute({"query": "tech trends"})
        assert result.success is True
        assert "tech" in str(result.content).lower() or "AI" in str(result.content)

    def test_execute_chinese_tech_keyword(self, tool):
        """Query with Chinese '科技' should return tech results."""
        result = tool.execute({"query": "人工智能科技"})
        assert result.success is True

    def test_execute_generic_query(self, tool):
        """Generic query should return default search results."""
        result = tool.execute({"query": "python programming"})
        assert result.success is True
        assert "Wikipedia" in str(result.content) or "Search Results" in str(result.content)

    def test_execute_empty_query_fails(self, tool):
        """Empty query should return failure."""
        result = tool.execute({"query": ""})
        assert result.success is False
        assert result.error is not None

    def test_execute_missing_query_fails(self, tool):
        """Missing query argument should return failure."""
        result = tool.execute({})
        assert result.success is False

    def test_result_is_tool_result(self, tool):
        """execute() should return a ToolResult instance."""
        result = tool.execute({"query": "test"})
        assert isinstance(result, ToolResult)


# ---------------------------------------------------------------------------
# WeatherTool tests
# ---------------------------------------------------------------------------


class TestWeatherTool:
    """Tests for WeatherTool."""

    @pytest.fixture
    def tool(self):
        return WeatherTool()

    def test_is_base_tool(self, tool):
        """WeatherTool should inherit from BaseTool."""
        assert isinstance(tool, BaseTool)

    def test_definition_name(self, tool):
        """Tool name should be 'weather'."""
        defn = tool.get_definition()
        assert defn.name == "weather"

    def test_definition_description(self, tool):
        """Tool should have a weather-related description."""
        defn = tool.get_definition()
        assert "weather" in defn.description.lower()

    def test_definition_has_city_param(self, tool):
        """Schema should have 'city' as required parameter."""
        defn = tool.get_definition()
        params = defn.parameters
        assert "city" in params["properties"]
        assert "city" in params["required"]

    def test_definition_has_date_param(self, tool):
        """Schema should have optional 'date' parameter."""
        defn = tool.get_definition()
        params = defn.parameters
        assert "date" in params["properties"]
        assert "date" not in params["required"]

    def test_execute_beijing(self, tool):
        """Beijing should return known weather data."""
        result = tool.execute({"city": "beijing"})
        assert result.success is True
        assert "Beijing" in str(result.content) or "beijing" in str(result.content).lower()
        assert "Sunny" in str(result.content) or "Cloudy" in str(result.content)

    def test_execute_shanghai(self, tool):
        """Shanghai should return known weather data."""
        result = tool.execute({"city": "Shanghai"})
        assert result.success is True
        assert "Shanghai" in str(result.content) or "shanghai" in str(result.content).lower()

    def test_execute_tokyo(self, tool):
        """Tokyo should return known weather data."""
        result = tool.execute({"city": "tokyo"})
        assert result.success is True
        assert "Tokyo" in str(result.content) or "tokyo" in str(result.content).lower()

    def test_execute_new_york(self, tool):
        """New York should return known weather data."""
        result = tool.execute({"city": "new york"})
        assert result.success is True
        assert "New York" in str(result.content) or "new york" in str(result.content).lower()

    def test_execute_unknown_city_fallback(self, tool):
        """Unknown city should return default weather."""
        result = tool.execute({"city": "Mars Colony"})
        assert result.success is True
        assert "mild conditions" in str(result.content) or "Partly cloudy" in str(result.content)

    def test_execute_with_date(self, tool):
        """Date parameter should be included in output."""
        result = tool.execute({"city": "beijing", "date": "tomorrow"})
        assert result.success is True
        assert "tomorrow" in str(result.content).lower()

    def test_execute_default_date(self, tool):
        """Without date, should default to 'today'."""
        result = tool.execute({"city": "beijing"})
        assert result.success is True
        assert "today" in str(result.content).lower()

    def test_execute_empty_city_fails(self, tool):
        """Empty city should return failure."""
        result = tool.execute({"city": ""})
        assert result.success is False
        assert result.error is not None

    def test_execute_missing_city_fails(self, tool):
        """Missing city argument should return failure."""
        result = tool.execute({})
        assert result.success is False

    def test_result_is_tool_result(self, tool):
        """execute() should return a ToolResult instance."""
        result = tool.execute({"city": "beijing"})
        assert isinstance(result, ToolResult)

    def test_multiple_cities_unique_results(self, tool):
        """Different cities should produce different weather results."""
        r1 = tool.execute({"city": "beijing"})
        r2 = tool.execute({"city": "london"})
        assert r1.content != r2.content


# ---------------------------------------------------------------------------
# Tool definition roundtrip
# ---------------------------------------------------------------------------


class TestToolDefinitionRoundtrip:
    """Verify tool definitions can be used by the framework."""

    def test_web_search_definition_serializable(self):
        """WebSearchTool definition should be JSON-friendly."""
        tool = WebSearchTool()
        defn = tool.get_definition()
        assert isinstance(defn, ToolDefinition)
        assert defn.name == "web_search"
        assert isinstance(defn.parameters, dict)

    def test_weather_definition_serializable(self):
        """WeatherTool definition should be JSON-friendly."""
        tool = WeatherTool()
        defn = tool.get_definition()
        assert isinstance(defn, ToolDefinition)
        assert defn.name == "weather"
        assert isinstance(defn.parameters, dict)

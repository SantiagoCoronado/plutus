"""Registry invariants (spec §13.2/§13.4): every tool is fully defined, the
hard-coded exclusion list can never be a tool, and schemas stay within the
lowest-common-denominator JSON Schema all providers accept."""

import re

import pytest

from app.llm.tooldefs import EXCLUDED_OPERATIONS, TOOLS, tools_for_tier

# keywords Gemini's OpenAI-compat endpoint accepts; anything else breaks a provider
LCD_KEYWORDS = {"type", "properties", "required", "description", "enum", "items"}

# name shapes that would mean a forbidden operation slipped into the registry
FORBIDDEN_PATTERNS = (
    r"^(update|delete|edit|remove)_(transaction|note)s?$",
    r"^delete_(mandate|watchlist)s?$",
    r"(api_key|llm_setting|alert_channel)",
    r"(trade|order)",
    r"bulk",
)


def _walk_schema(schema: dict, path: str = "$"):
    yield path, schema
    for key, value in schema.get("properties", {}).items():
        if isinstance(value, dict):
            yield from _walk_schema(value, f"{path}.{key}")
    items = schema.get("items")
    if isinstance(items, dict):
        yield from _walk_schema(items, f"{path}[]")


class TestRegistryInvariants:
    def test_every_tool_fully_defined(self):
        for tool in TOOLS.values():
            assert tool.name and tool.description, tool.name
            assert tool.tier in ("read", "write"), tool.name
            assert callable(tool.handler) and callable(tool.summarize), tool.name
            assert tool.schema.get("type") == "object", tool.name
            assert isinstance(tool.schema.get("required"), list), tool.name

    def test_excluded_operations_never_registered(self):
        assert not EXCLUDED_OPERATIONS & set(TOOLS)

    @pytest.mark.parametrize("pattern", FORBIDDEN_PATTERNS)
    def test_no_tool_matches_forbidden_name_shape(self, pattern):
        offenders = [name for name in TOOLS if re.search(pattern, name)]
        assert not offenders, f"{offenders} match forbidden pattern {pattern}"

    def test_tier_scoping(self):
        read_names = {tool.name for tool in tools_for_tier("read")}
        assert all(TOOLS[name].tier == "read" for name in read_names)
        assert {tool.name for tool in tools_for_tier("write")} == set(TOOLS)

    def test_spec_read_tools_present(self):
        expected = {
            "search_assets", "get_asset_overview", "get_ohlcv", "get_fundamentals",
            "get_news", "run_screen", "backtest_signal", "get_candidates",
            "get_mandates", "get_portfolio_positions", "get_portfolio_performance",
            "get_ingestion_status",
        }
        assert expected <= {t.name for t in tools_for_tier("read")}

    def test_spec_write_tools_present(self):
        expected = {
            "write_research_note", "manage_watchlist", "create_mandate",
            "update_mandate", "trigger_scan", "update_candidate",
            "run_strategy_backtest", "add_transaction",
        }
        write_only = {t.name for t in TOOLS.values() if t.tier == "write"}
        assert expected == write_only


class TestSchemasAreLowestCommonDenominator:
    def test_only_lcd_keywords(self):
        for tool in TOOLS.values():
            for path, node in _walk_schema(tool.schema):
                extra = set(node) - LCD_KEYWORDS
                assert not extra, f"{tool.name} {path} uses non-LCD keywords {extra}"

    def test_no_refs_or_unions_anywhere(self):
        import json

        for tool in TOOLS.values():
            encoded = json.dumps(tool.schema)
            for banned in ('"$ref"', '"anyOf"', '"oneOf"', '"allOf"', '"default"'):
                assert banned not in encoded, f"{tool.name} schema contains {banned}"

    def test_required_fields_exist_in_properties(self):
        for tool in TOOLS.values():
            properties = set(tool.schema.get("properties", {}))
            for name in tool.schema.get("required", []):
                assert name in properties, f"{tool.name} requires unknown field {name}"

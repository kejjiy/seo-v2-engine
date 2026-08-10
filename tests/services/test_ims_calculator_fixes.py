import pytest
from app.services.ims_calculator import calculate_ims, IMSResult

class TestIMSResultFixes:
    def test_multiple_h1_friction_point(self):
        """Multiple H1 tags should be detected as friction point."""
        html = "<html><body><h1>Title 1</h1><h1>Title 2</h1><p>Content</p></body></html>"
        result = calculate_ims(html)
        assert "Multiple H1 tags" in result.friction_points

    def test_script_tags_ignored_in_text_density(self):
        """Content inside script tags should not count towards text density."""
        # A lot of JS code, but little visible text
        js_code = "var x = " + "'code' + " * 100 + "'';"
        html = f"<html><body><script>{js_code}</script><p>Short text</p></body></html>"

        result = calculate_ims(html)
        # Should have low score due to low visible text, not high score due to JS "text"
        # If JS was counted, word count would be high
        assert "Low word count" in result.friction_points

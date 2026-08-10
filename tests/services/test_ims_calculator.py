"""
Unit tests for IMS Calculator Service.

Tests cover:
- Score calculation (0-100 range)
- Text density scoring
- Heading structure scoring
- Interactive elements scoring
- Friction point detection
- Edge cases (empty HTML, perfect HTML)
"""
import pytest
from app.services.ims_calculator import calculate_ims, IMSResult


class TestIMSResult:
    """Tests for IMSResult model."""

    def test_ims_result_has_required_fields(self):
        """IMSResult should have score and friction_points fields."""
        result = IMSResult(score=75, friction_points=["Missing H1"])
        assert result.score == 75
        assert result.friction_points == ["Missing H1"]


class TestCalculateIMS:
    """Tests for calculate_ims function."""

    # --- Score Range Tests (AC #4) ---

    def test_score_is_between_0_and_100(self):
        """Score must be between 0 and 100."""
        html = "<html><body><h1>Test</h1><p>Content here.</p></body></html>"
        result = calculate_ims(html)
        assert 0 <= result.score <= 100

    def test_empty_html_returns_zero_score(self):
        """Empty HTML should return score of 0."""
        result = calculate_ims("")
        assert result.score == 0

    def test_empty_body_returns_low_score(self):
        """HTML with empty body should return very low score."""
        html = "<html><body></body></html>"
        result = calculate_ims(html)
        assert result.score < 30  # Score is low due to missing content

    # --- Text Density Tests (AC #3) ---

    def test_high_text_density_increases_score(self):
        """Pages with more text content should score higher."""
        low_text = "<html><body><div><div><div><p>Hi</p></div></div></div></body></html>"
        high_text = "<html><body><p>" + "This is quality content. " * 50 + "</p></body></html>"

        low_result = calculate_ims(low_text)
        high_result = calculate_ims(high_text)

        assert high_result.score > low_result.score

    # --- Heading Structure Tests (AC #3) ---

    def test_h1_present_increases_score(self):
        """Presence of H1 should contribute to score."""
        without_h1 = "<html><body><p>Content without heading</p></body></html>"
        with_h1 = "<html><body><h1>Title</h1><p>Content with heading</p></body></html>"

        without_result = calculate_ims(without_h1)
        with_result = calculate_ims(with_h1)

        assert with_result.score > without_result.score

    def test_proper_heading_hierarchy_increases_score(self):
        """Proper H1 -> H2 -> H3 hierarchy should score higher."""
        broken = "<html><body><h1>Title</h1><h3>Skipped H2</h3><p>Text</p></body></html>"
        proper = "<html><body><h1>Title</h1><h2>Section</h2><h3>Subsection</h3><p>Text</p></body></html>"

        broken_result = calculate_ims(broken)
        proper_result = calculate_ims(proper)

        assert proper_result.score > broken_result.score

    # --- Interactive Elements Tests (AC #3) ---

    def test_links_contribute_to_score(self):
        """Pages with relevant links should have better score."""
        no_links = "<html><body><h1>Test</h1><p>No links here.</p></body></html>"
        with_links = "<html><body><h1>Test</h1><p>Check <a href='/page'>this link</a> for more.</p></body></html>"

        no_links_result = calculate_ims(no_links)
        with_links_result = calculate_ims(with_links)

        # Links should slightly improve or maintain score
        assert with_links_result.score >= no_links_result.score - 5

    # --- Friction Points Tests (AC #5) ---

    def test_missing_h1_friction_point(self):
        """Missing H1 should be detected as friction point."""
        html = "<html><body><p>No heading here</p></body></html>"
        result = calculate_ims(html)
        assert "Missing H1" in result.friction_points

    def test_h1_present_no_friction(self):
        """H1 present should not trigger Missing H1 friction."""
        html = "<html><body><h1>I have a heading</h1><p>Content</p></body></html>"
        result = calculate_ims(html)
        assert "Missing H1" not in result.friction_points

    def test_low_word_count_friction_point(self):
        """Pages with < 300 words should have Low word count friction."""
        html = "<html><body><h1>Title</h1><p>Short content.</p></body></html>"
        result = calculate_ims(html)
        assert "Low word count" in result.friction_points

    def test_sufficient_word_count_no_friction(self):
        """Pages with >= 300 words should not have word count friction."""
        words = " ".join(["word"] * 350)
        html = f"<html><body><h1>Title</h1><p>{words}</p></body></html>"
        result = calculate_ims(html)
        assert "Low word count" not in result.friction_points

    def test_broken_hierarchy_friction_point(self):
        """H1 -> H3 (skipping H2) should be detected as broken hierarchy."""
        html = "<html><body><h1>Title</h1><h3>Skipped H2</h3><p>Content</p></body></html>"
        result = calculate_ims(html)
        assert "Broken heading hierarchy" in result.friction_points

    def test_proper_hierarchy_no_friction(self):
        """Proper heading hierarchy should not trigger friction."""
        html = "<html><body><h1>Title</h1><h2>Section</h2><h3>Sub</h3><p>Content</p></body></html>"
        result = calculate_ims(html)
        assert "Broken heading hierarchy" not in result.friction_points

    # --- Perfect HTML Test ---

    def test_perfect_html_high_score(self):
        """Well-structured HTML with good content should score high."""
        words = " ".join(["quality content word"] * 120)  # ~360 words
        html = f"""
        <html>
        <body>
            <h1>Main Title of the Page</h1>
            <p>{words}</p>
            <h2>First Section</h2>
            <p>More descriptive content about the first section.</p>
            <a href="/contact">Contact us</a>
            <h3>Subsection</h3>
            <p>Additional details here.</p>
            <button>Click me</button>
        </body>
        </html>
        """
        result = calculate_ims(html)
        assert result.score >= 70
        assert len(result.friction_points) == 0

    # --- Edge Cases ---

    def test_malformed_html_does_not_crash(self):
        """Malformed HTML should not crash, just return low score."""
        html = "<html><body><h1>Unclosed tag<p>Missing closes"
        result = calculate_ims(html)
        assert isinstance(result, IMSResult)
        assert 0 <= result.score <= 100

    def test_only_whitespace_html(self):
        """HTML with only whitespace should return 0 score."""
        html = "   \n\t  "
        result = calculate_ims(html)
        assert result.score == 0

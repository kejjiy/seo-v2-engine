"""
IMS (Intent Match Score) Calculator Service.

Calculates a quality score (0-100) for web pages based on:
- Text density (ratio of text to HTML tags)
- Heading structure (H1-H6 presence and hierarchy)
- Interactive elements (links, buttons)

Also detects "Friction Points" that indicate content quality issues.
"""
import re
from dataclasses import dataclass, field
from bs4 import BeautifulSoup


@dataclass
class IMSResult:
    """
    Result of IMS calculation.

    Attributes:
        score: Quality score between 0 and 100.
        friction_points: List of detected issues (e.g., "Missing H1").
    """
    score: int
    friction_points: list[str] = field(default_factory=list)


# Scoring weights (total = 100)
WEIGHT_TEXT_DENSITY = 35
WEIGHT_HEADING_STRUCTURE = 35
WEIGHT_INTERACTIVE = 15
WEIGHT_WORD_COUNT = 15

# Thresholds
MIN_WORD_COUNT = 300
IDEAL_TEXT_RATIO = 0.4  # 40% text vs HTML is ideal
MIN_TEXT_RATIO = 0.1

# Heading levels for hierarchy check
HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]


def calculate_ims(html: str) -> IMSResult:
    """
    Calculate the Intent Match Score for a given HTML page.

    Args:
        html: Raw HTML content of the page.

    Returns:
        IMSResult: Score (0-100) and list of friction points.
    """
    # Handle empty/whitespace input
    if not html or not html.strip():
        return IMSResult(score=0, friction_points=["Empty content"])

    # Parse HTML with lxml for speed, fallback to html.parser
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    friction_points: list[str] = []

    # Extract text content
    body = soup.find("body")
    if not body:
        body = soup  # Fallback if no body tag

    # Remove script and style elements
    for script_or_style in soup(["script", "style"]):
        script_or_style.decompose()

    text_content = body.get_text(separator=" ", strip=True)
    words = text_content.split()
    word_count = len(words)

    # --- Text Density Score ---
    text_density_score = _calculate_text_density_score(html, text_content)

    # --- Heading Structure Score ---
    heading_score, heading_frictions = _calculate_heading_score(soup)
    friction_points.extend(heading_frictions)

    # --- Interactive Elements Score ---
    interactive_score = _calculate_interactive_score(soup)

    # --- Word Count Score ---
    word_count_score = _calculate_word_count_score(word_count)
    if word_count < MIN_WORD_COUNT:
        friction_points.append("Low word count")

    # --- Calculate Total Score ---
    total_score = (
        text_density_score * WEIGHT_TEXT_DENSITY / 100 +
        heading_score * WEIGHT_HEADING_STRUCTURE / 100 +
        interactive_score * WEIGHT_INTERACTIVE / 100 +
        word_count_score * WEIGHT_WORD_COUNT / 100
    )

    # Ensure score is within bounds
    final_score = max(0, min(100, int(round(total_score))))

    return IMSResult(score=final_score, friction_points=friction_points)


def _calculate_text_density_score(html: str, text_content: str) -> int:
    """
    Calculate score based on text-to-HTML ratio.

    High tag density with little text = low score.
    Good text content with minimal markup = high score.
    """
    html_length = len(html)
    text_length = len(text_content)

    if html_length == 0:
        return 0

    ratio = text_length / html_length

    if ratio >= IDEAL_TEXT_RATIO:
        return 100
    elif ratio >= MIN_TEXT_RATIO:
        # Linear scale between MIN and IDEAL
        return int(((ratio - MIN_TEXT_RATIO) / (IDEAL_TEXT_RATIO - MIN_TEXT_RATIO)) * 100)
    else:
        # Very low ratio
        return int((ratio / MIN_TEXT_RATIO) * 50)


def _calculate_heading_score(soup: BeautifulSoup) -> tuple[int, list[str]]:
    """
    Calculate score based on heading structure.

    Checks for:
    - Presence of H1 (required)
    - Proper hierarchy (H1 -> H2 -> H3, no skipping)
    - Multiple H1s (minor issue)
    """
    friction_points: list[str] = []
    score = 100

    # Find all headings
    headings = []
    for tag in HEADING_TAGS:
        found = soup.find_all(tag)
        headings.extend([(tag, elem) for elem in found])

    # Sort by document order (approximate via source position)
    h1_tags = soup.find_all("h1")

    # Check H1 presence
    if not h1_tags:
        friction_points.append("Missing H1")
        score -= 40
    elif len(h1_tags) > 1:
        friction_points.append("Multiple H1 tags")
        score -= 10

    # Check heading hierarchy
    if _has_broken_hierarchy(soup):
        friction_points.append("Broken heading hierarchy")
        score -= 30

    # Bonus for having multiple heading levels (good structure)
    heading_levels_used = set()
    for tag in HEADING_TAGS:
        if soup.find(tag):
            heading_levels_used.add(tag)

    if len(heading_levels_used) >= 3:
        score = min(100, score + 10)  # Small bonus

    return max(0, score), friction_points


def _has_broken_hierarchy(soup: BeautifulSoup) -> bool:
    """
    Check if heading hierarchy is broken (e.g., H1 -> H3 without H2).
    """
    # Get all headings in order
    all_headings = soup.find_all(HEADING_TAGS)

    if len(all_headings) < 2:
        return False

    prev_level = 0
    for heading in all_headings:
        current_level = int(heading.name[1])  # h1 -> 1, h2 -> 2, etc.

        # Check for skip (e.g., H1 to H3)
        if prev_level > 0 and current_level > prev_level + 1:
            return True

        prev_level = current_level

    return False


def _calculate_interactive_score(soup: BeautifulSoup) -> int:
    """
    Calculate score based on interactive elements (links, buttons).

    Good pages have relevant internal links and clear CTAs.
    """
    links = soup.find_all("a", href=True)
    buttons = soup.find_all("button")

    score = 50  # Base score

    # Links contribute positively (up to a point)
    link_count = len(links)
    if link_count >= 1:
        score += min(30, link_count * 5)

    # Buttons (CTAs) are good
    button_count = len(buttons)
    if button_count >= 1:
        score += min(20, button_count * 10)

    return min(100, score)


def _calculate_word_count_score(word_count: int) -> int:
    """
    Calculate score based on word count.

    Target: >= 300 words for good content.
    """
    if word_count >= MIN_WORD_COUNT:
        return 100
    elif word_count >= 100:
        # Partial score
        return int((word_count / MIN_WORD_COUNT) * 100)
    elif word_count >= 10:
        return int((word_count / 100) * 50)
    else:
        return 0

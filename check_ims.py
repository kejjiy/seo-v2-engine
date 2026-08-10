import sys
import os

# Add current directory to path
sys.path.append(os.getcwd())

try:
    from app.services.ims_calculator import calculate_ims
    print("Import successful")

    html = "<html><body><h1>Test</h1><p>Some content here.</p></body></html>"
    result = calculate_ims(html)
    print(f"Score: {result.score}")
    print(f"Friction: {result.friction_points}")

except Exception as e:
    print(f"Error: {e}")

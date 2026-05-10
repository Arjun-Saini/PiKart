import sys
from PIL import Image

BORDER = 6

if len(sys.argv) != 3:
    print("Usage: python3 png_to_map.py input.png output.txt")
    sys.exit(1)

input_path  = sys.argv[1]
output_path = sys.argv[2]

img = Image.open(input_path).convert('RGBA')
width, height = img.size
pixels = img.load()

border_row = ' ' * (width + BORDER * 2)
lines = []

# top border
for _ in range(BORDER):
    lines.append(border_row)

# image rows
for y in range(height):
    row = ' ' * BORDER
    for x in range(width):
        r, g, b, a = pixels[x, y]
        row += '#' if a > 128 else ' '
    row += ' ' * BORDER
    lines.append(row)

# bottom border
for _ in range(BORDER):
    lines.append(border_row)

with open(output_path, 'w') as f:
    f.write('\n'.join(lines) + '\n')

print(f"Written {len(lines)} rows x {len(lines[0])} cols → {output_path}")
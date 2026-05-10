
import sys

file_path = r"d:\Lina\Flask_app\templates\cockpit.html"
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

with open(file_path, 'w', encoding='utf-8') as f:
    for line in lines:
        if 'updateSlider(\'pan\',' in line and 'preset-btn' in line:
            # Extract the number
            import re
            match = re.search(r'updateSlider\(\'pan\', (\d+)\)', line)
            if match:
                angle = match.group(1)
                new_line = f'                    <button class="preset-btn" onclick="updateSlider(\'pan\', {angle})">{angle}°</button>\n'
                f.write(new_line)
            else:
                f.write(line)
        else:
            f.write(line)

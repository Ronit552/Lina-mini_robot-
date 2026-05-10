
import os

file_path = r"d:\Lina\Flask_app\templates\cockpit.html"
with open(file_path, 'rb') as f:
    content = f.read()

# Replace the specific bad sequence if found
# b'\x95\x90\xe2\x95\x90' -> b'\xe2\x95\x90' or just remove it
# Let's just try to decode with 'ignore' or 'replace' to see what it looks like, 
# then write it back as clean UTF-8.
# Or better, just target the known bad bytes.

# Looking at the context: b'}\r\n        }\r\n\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\r\n'
# It seems to be around line 1422.

# Let's just remove all non-utf8 bytes or replace them with space.
try:
    decoded = content.decode('utf-8')
    print("No changes needed, file is valid UTF-8")
except UnicodeDecodeError:
    print("Fixing encoding...")
    # Replace common bad sequences or just use 'ignore' for those specific bytes
    # But 'ignore' might remove too much.
    # Let's try to replace common decorative characters that might have been mangled.
    fixed_content = content.replace(b'\x95\x90', b'') 
    # Try again
    try:
        fixed_content.decode('utf-8')
        with open(file_path, 'wb') as f:
            f.write(fixed_content)
        print("Fixed by removing \\x95\\x90")
    except UnicodeDecodeError as e:
        print(f"Still failing at {e.start}. Trying a more aggressive fix.")
        # Replace all non-ASCII characters in comments with something safe
        # Or just use the 'replace' error handler for the whole file.
        final_content = content.decode('utf-8', errors='replace').encode('utf-8')
        with open(file_path, 'wb') as f:
            f.write(final_content)
        print("Fixed using 'replace' error handler.")

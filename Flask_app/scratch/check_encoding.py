
import sys

def find_encoding_issue(file_path):
    try:
        with open(file_path, 'rb') as f:
            content = f.read()
        
        try:
            content.decode('utf-8')
            print("File is valid UTF-8")
        except UnicodeDecodeError as e:
            print(f"UnicodeDecodeError: {e}")
            pos = e.start
            print(f"Error at position: {pos}")
            context = content[max(0, pos-20):min(len(content), pos+20)]
            print(f"Context (bytes): {context}")
            try:
                print(f"Context (decoded cp1252): {context.decode('cp1252')}")
            except:
                print("Could not decode context as cp1252")
            
            # Find the line number
            lines = content[:pos].split(b'\n')
            line_num = len(lines)
            print(f"Approximate line number: {line_num}")
    except Exception as e:
        print(f"An error occurred: {e}")

            
if __name__ == "__main__":
    find_encoding_issue(r"d:\Lina\Flask_app\templates\cockpit.html")

import os
import json

if __name__ == "__main__":
    matrix = [
        {"keyboard": keyboard}
        for keyboard in os.listdir("keyboards")
        if os.path.isdir(os.path.join("keyboards", keyboard))
    ]
    print(json.dumps(matrix))

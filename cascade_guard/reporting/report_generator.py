import json
import os


def generate(data: dict, file: str = "report.json") -> str:
    folder = os.path.dirname(file)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return file

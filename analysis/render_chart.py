import json

with open("chart_data.json", encoding="utf-8") as f:
    data = json.load(f)

with open("chart_template.html", encoding="utf-8") as f:
    template = f.read()

html = template.replace("__CHART_DATA__", json.dumps(data, ensure_ascii=False))

with open("quadrant_chart.html", "w", encoding="utf-8") as f:
    f.write(html)

print("wrote quadrant_chart.html")

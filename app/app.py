from flask import Flask, render_template, request
import pandas as pd
import joblib

app = Flask(__name__)

# Load models and data
model = joblib.load("danger_grid_model_time.pkl")
grid_df = pd.read_csv("grid_summary_time.csv")
intersection_lookup = pd.read_csv("intersection_lookup.csv")

# Preprocess intersection names for easier matching
intersection_lookup["Primary address"] = intersection_lookup["Primary address"].str.strip().str.lower()
intersection_lookup["Secondary address"] = intersection_lookup["Secondary address"].str.strip().str.lower()

@app.route("/", methods=["GET", "POST"])
def index():
    prediction = ""
    confidence = ""

    if request.method == "POST":
        lat = request.form.get("latitude")
        lon = request.form.get("longitude")
        primary_street = request.form.get("primary_street", "").strip().lower()
        secondary_street = request.form.get("secondary_street", "").strip().lower()
        hour = int(request.form.get("hour", 12))
        day = int(request.form.get("dayofweek", 2))

        lat_grid = None
        lon_grid = None

        if primary_street and secondary_street and not lat and not lon:
            # If streets are provided but lat/lon not, find grid from lookup
            match = intersection_lookup[
                (intersection_lookup["Primary address"] == primary_street) &
                (intersection_lookup["Secondary address"] == secondary_street)
            ]
            if not match.empty:
                lat_grid = match.iloc[0]["lat_grid"]
                lon_grid = match.iloc[0]["lon_grid"]
        elif lat and lon:
            # If lat/lon are provided
            lat_grid = round(float(lat), 3)
            lon_grid = round(float(lon), 3)

        if lat_grid is not None and lon_grid is not None:
            match = grid_df[(grid_df["lat_grid"] == lat_grid) & (grid_df["lon_grid"] == lon_grid)]
            if not match.empty:
                row = match.iloc[0]
                features = [
                    row["total_crashes"],
                    row["total_danger_score"],
                    row["avg_deaths"],
                    row["avg_injuries"],
                    hour,
                    day
                ]
                pred_prob = model.predict_proba([features])[0][1]
                is_high_risk = model.predict([features])[0]

                if is_high_risk == 1:
                    prediction = "⚠️ High Risk"
                else:
                    prediction = "✅ Not High Risk"
                confidence = f"{int(pred_prob * 100)}%"
            else:
                prediction = "❓ Unknown Location"
                confidence = "N/A"
        else:
            prediction = "⚠️ Please provide valid input."
            confidence = ""

    return render_template("index.html", prediction=prediction, confidence=confidence)

@app.route("/upload", methods=["GET", "POST"])
def upload():
    results = []
    if request.method == "POST":
        file = request.files["file"]
        if file and file.filename.endswith(".csv"):
            import io
            df = pd.read_csv(io.StringIO(file.stream.read().decode("utf-8")))

            for index, row in df.iterrows():
                lat = row.get("latitude", None)
                lon = row.get("longitude", None)
                primary_street = str(row.get("Primary address", "")).strip().lower()
                secondary_street = str(row.get("Secondary address", "")).strip().lower()
                hour = row.get("hour", 12)
                day = row.get("dayofweek", 2)

                lat_grid = None
                lon_grid = None

                # 1. Prefer Lat/Lon if available
                if pd.notna(lat) and pd.notna(lon):
                    lat_grid = round(float(lat), 3)
                    lon_grid = round(float(lon), 3)
                elif primary_street and secondary_street:
                    # 2. Otherwise, try to find from intersection lookup
                    match = intersection_lookup[
                        (intersection_lookup["Primary address"] == primary_street) &
                        (intersection_lookup["Secondary address"] == secondary_street)
                    ]
                    if not match.empty:
                        lat_grid = match.iloc[0]["lat_grid"]
                        lon_grid = match.iloc[0]["lon_grid"]

                if lat_grid is not None and lon_grid is not None:
                    match_grid = grid_df[(grid_df["lat_grid"] == lat_grid) & (grid_df["lon_grid"] == lon_grid)]
                    if not match_grid.empty:
                        g = match_grid.iloc[0]
                        features = [
                            g["total_crashes"],
                            g["total_danger_score"],
                            g["avg_deaths"],
                            g["avg_injuries"],
                            hour,
                            day
                        ]
                        prob = model.predict_proba([features])[0][1]
                        risk = "High Risk" if model.predict([features])[0] == 1 else "Not High Risk"
                        results.append({
                            "primary_street": primary_street,
                            "secondary_street": secondary_street,
                            "latitude": lat_grid,
                            "longitude": lon_grid,
                            "hour": hour,
                            "dayofweek": day,
                            "prediction": risk,
                            "confidence": f"{int(prob * 100)}%"
                        })
                    else:
                        results.append({
                            "primary_street": primary_street,
                            "secondary_street": secondary_street,
                            "latitude": lat_grid,
                            "longitude": lon_grid,
                            "hour": hour,
                            "dayofweek": day,
                            "prediction": "Unknown Location",
                            "confidence": "N/A"
                        })
                else:
                    results.append({
                        "primary_street": primary_street,
                        "secondary_street": secondary_street,
                        "latitude": lat,
                        "longitude": lon,
                        "hour": hour,
                        "dayofweek": day,
                        "prediction": "Invalid Input",
                        "confidence": "N/A"
                    })
    return render_template("upload.html", results=results)


if __name__ == "__main__":
    app.run(debug=True)

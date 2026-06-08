from flask import Flask, render_template, request, send_file, jsonify
import pandas as pd
import numpy as np
import joblib
import io
import json
import folium
from folium.plugins import HeatMap

app = Flask(__name__)

# ── Load model and data ─────────────────────────────────────────────────────
model      = joblib.load('../models/danger_model_v2.pkl')
meta       = joblib.load('../models/model_meta.pkl')
FEATURES   = meta['features']
SPEED_MED  = meta['median_speed_limit']

grid_df  = pd.read_csv('../data/grid_summary_v2.csv')
lookup   = pd.read_csv('../data/intersection_lookup_v2.csv')
lookup['primary_street']   = lookup['primary_street'].str.strip().str.lower()
lookup['secondary_street'] = lookup['secondary_street'].str.strip().str.lower()

# Pre-build a set for quick existence checks
lookup_index = set(zip(lookup['primary_street'], lookup['secondary_street']))


# ── Helpers ──────────────────────────────────────────────────────────────────
def resolve_location(lat, lon, primary, secondary):
    """Return (lat_grid, lon_grid) from coordinates OR street names."""
    lat_grid = lon_grid = None

    if lat and lon:
        try:
            lat_grid = round(float(lat), 3)
            lon_grid = round(float(lon), 3)
        except ValueError:
            pass
    elif primary and secondary:
        p = primary.strip().lower()
        s = secondary.strip().lower()
        match = lookup[
            (lookup['primary_street'] == p) & (lookup['secondary_street'] == s)
        ]
        if match.empty:
            # bidirectional: try reversed order
            match = lookup[
                (lookup['primary_street'] == s) & (lookup['secondary_street'] == p)
            ]
        if not match.empty:
            lat_grid = float(match.iloc[0]['lat_grid'])
            lon_grid = float(match.iloc[0]['lon_grid'])

    return lat_grid, lon_grid


def get_grid_row(lat_grid, lon_grid):
    """Fetch precomputed statistics for a grid cell."""
    match = grid_df[
        (grid_df['lat_grid'] == lat_grid) & (grid_df['lon_grid'] == lon_grid)
    ]
    return match.iloc[0] if not match.empty else None


def build_feature_vector(row, hour, day_of_week):
    """Assemble the 14-element feature vector the model expects."""
    return [
        row['total_crashes'],
        row['death_rate'],
        row['serious_injury_rate'],
        row['injury_rate'],
        row['avg_speed_limit'] if not pd.isna(row['avg_speed_limit']) else SPEED_MED,
        row['pedestrian_rate'],
        row['bicycle_rate'],
        row['motorcycle_rate'],
        row['night_crash_rate'],
        row['rush_hour_rate'],
        row['weekend_rate'],
        row['construction_zone_rate'],
        float(hour),
        float(day_of_week),
    ]


def predict(row, hour, day_of_week):
    """Return (is_high_risk, confidence_pct, explanation_dict)."""
    if row is None:
        return None, None, None
    x = build_feature_vector(row, hour, day_of_week)
    prob      = float(model.predict_proba([x])[0][1])
    pred      = int(model.predict([x])[0])
    explained = {
        feat: round(val, 4)
        for feat, val in zip(FEATURES, x)
    }
    return pred, prob, explained


def friendly_explanation(row, feats):
    """Generate a 2-3 sentence plain-English explanation of the prediction."""
    lines = []
    if row is not None:
        lines.append(
            f"This location has <strong>{int(row['total_crashes'])} historical crashes</strong> "
            f"in its 100-meter grid cell."
        )
        if not pd.isna(row['avg_speed_limit']) and row['avg_speed_limit'] > 0:
            lines.append(
                f"Crashes here typically occur at a posted speed limit of "
                f"<strong>{row['avg_speed_limit']:.0f} mph</strong>."
            )
        if row['pedestrian_rate'] > 0.05:
            lines.append(
                f"<strong>{row['pedestrian_rate']*100:.1f}%</strong> of crashes involved pedestrians — "
                "indicating a high-risk zone for people on foot."
            )
        if row['death_rate'] > 0.005:
            lines.append(
                f"The fatality rate is <strong>{row['death_rate']*100:.2f}%</strong> per crash."
            )
    return ' '.join(lines)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/', methods=['GET', 'POST'])
def index():
    result = None

    if request.method == 'POST':
        lat       = request.form.get('latitude',       '').strip()
        lon       = request.form.get('longitude',      '').strip()
        primary   = request.form.get('primary_street', '').strip()
        secondary = request.form.get('secondary_street','').strip()
        hour      = int(request.form.get('hour',      12))
        day       = int(request.form.get('dayofweek',  2))

        lat_grid, lon_grid = resolve_location(lat, lon, primary, secondary)

        if lat_grid is None:
            result = {'status': 'error',
                      'message': 'Could not resolve location. Check coordinates or street names.'}
        else:
            row  = get_grid_row(lat_grid, lon_grid)
            pred, prob, feats = predict(row, hour, day)

            if pred is None:
                result = {'status': 'nodata',
                          'message': f'No crash history for ({lat_grid}, {lon_grid}). Cannot predict.',
                          'lat': lat_grid, 'lon': lon_grid}
            else:
                spd = row['avg_speed_limit']
                result = {
                    'status'       : 'success',
                    'is_high_risk' : pred == 1,
                    'prediction'   : 'High Risk' if pred == 1 else 'Not High Risk',
                    'confidence'   : int(prob * 100),
                    'lat'          : lat_grid,
                    'lon'          : lon_grid,
                    'total_crashes': int(row['total_crashes']),
                    'death_rate'   : f"{row['death_rate']:.4f}",
                    'injury_rate'  : f"{row['injury_rate']:.2f}",
                    'avg_speed'    : f"{spd:.0f} mph" if not pd.isna(spd) else 'Unknown',
                    'ped_rate'     : f"{row['pedestrian_rate']*100:.1f}%",
                    'night_rate'   : f"{row['night_crash_rate']*100:.1f}%",
                    'explanation'  : friendly_explanation(row, feats),
                }

    return render_template('index.html', result=result)


@app.route('/upload', methods=['GET', 'POST'])
def upload():
    results = []

    if request.method == 'POST':
        file = request.files.get('file')
        if file and file.filename.endswith('.csv'):
            df = pd.read_csv(io.StringIO(file.stream.read().decode('utf-8')))

            for _, row_in in df.iterrows():
                lat       = str(row_in.get('latitude',        '')).strip() if pd.notna(row_in.get('latitude'))        else ''
                lon       = str(row_in.get('longitude',       '')).strip() if pd.notna(row_in.get('longitude'))       else ''
                primary   = str(row_in.get('primary_street',  '')).strip() if pd.notna(row_in.get('primary_street'))  else ''
                secondary = str(row_in.get('secondary_street',''))        .strip() if pd.notna(row_in.get('secondary_street')) else ''
                hour      = int(row_in.get('hour',       12))
                day       = int(row_in.get('dayofweek',   2))

                lat_grid, lon_grid = resolve_location(lat, lon, primary, secondary)

                if lat_grid is None:
                    results.append({
                        'location'   : f"{primary or lat} & {secondary or lon}",
                        'lat'        : '–', 'lon': '–',
                        'hour'       : hour, 'day': day,
                        'prediction' : 'Location Not Found',
                        'confidence' : 'N/A',
                        'is_high_risk': False,
                    })
                    continue

                grid_row              = get_grid_row(lat_grid, lon_grid)
                pred, prob, _         = predict(grid_row, hour, day)

                results.append({
                    'location'   : f"{primary or lat_grid} & {secondary or lon_grid}",
                    'lat'        : lat_grid, 'lon': lon_grid,
                    'hour'       : hour, 'day': day,
                    'prediction' : ('High Risk' if pred == 1 else 'Not High Risk') if pred is not None else 'No Data',
                    'confidence' : f"{int(prob*100)}%" if prob is not None else 'N/A',
                    'is_high_risk': bool(pred == 1) if pred is not None else False,
                })

    return render_template('upload.html', results=results)


@app.route('/download-results', methods=['POST'])
def download_results():
    data = request.get_json()
    buf  = io.StringIO()
    pd.DataFrame(data).to_csv(buf, index=False)
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.getvalue().encode()),
        mimetype='text/csv',
        as_attachment=True,
        download_name='risk_predictions.csv',
    )


@app.route('/api/map-data')
def map_data():
    """Return top 60 dangerous grid cells as JSON for the embedded map."""
    top = grid_df.nlargest(60, 'total_danger_score')[
        ['lat_grid', 'lon_grid', 'total_crashes', 'total_danger_score',
         'death_rate', 'injury_rate', 'avg_speed_limit']
    ].copy()
    top['avg_speed_limit'] = top['avg_speed_limit'].fillna(0)
    return jsonify(top.to_dict(orient='records'))


if __name__ == '__main__':
    app.run(debug=True)

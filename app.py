"""
Working Principle:
This Flask app is a real-time GPS navigation system. It fetches the user's location using browser geolocation,
then uses Google Maps API to compute and update route steps to the entered destination every 5 seconds.
The updated step data and map image are available both on the web UI and via an endpoint that can be
fetched by an ESP32 module.

Cloud Platform Used: Google Maps API
"""

from flask import Flask, request, jsonify, render_template_string, send_file
import requests
import io
from urllib.parse import quote_plus
import socket
import re
from geopy.distance import geodesic

app = Flask(__name__)

GOOGLE_MAPS_API_KEY = 'AIzaSyDZuZ1sMCSJSyC_u-rbzHC8BvbIyzAgL3M'
MAP_WIDTH = 320
MAP_HEIGHT = 240

current_route = {
    'origin': None,
    'destination': None,
    'steps': [],
    'step_index': 0,
    'polyline': ''
}

def clean_html(raw_html):
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html)

def update_route(origin, destination):
    directions_url = 'https://maps.googleapis.com/maps/api/directions/json'
    params = {
        'origin': origin,
        'destination': destination,
        'mode': 'driving',
        'key': GOOGLE_MAPS_API_KEY
    }
    response = requests.get(directions_url, params=params).json()
    if response['status'] != 'OK':
        return False

    steps = []
    for leg in response['routes'][0]['legs']:
        for step in leg['steps']:
            loc = step['start_location']
            instruction = clean_html(step['html_instructions'])
            steps.append({
                'lat': loc['lat'],
                'lng': loc['lng'],
                'instruction': instruction
            })

    current_route['steps'] = steps
    current_route['polyline'] = response['routes'][0]['overview_polyline']['points']
    current_route['step_index'] = 0
    return True

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        destination = request.form.get('destination')
        if not destination:
            return "Destination is required.", 400
        current_route['destination'] = destination

        if not current_route['origin']:
            return "Origin not set yet from GPS.", 400

        success = update_route(current_route['origin'], destination)
        if not success:
            return "Could not calculate route.", 500

    return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Live Navigation</title>
            <script>
            let interval;
            function startTracking() {
                if (!navigator.geolocation) {
                    alert("Geolocation not supported.");
                    return;
                }
                navigator.geolocation.watchPosition(pos => {
                    fetch('/update_location', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            lat: pos.coords.latitude,
                            lng: pos.coords.longitude
                        })
                    });
                });
                interval = setInterval(() => {
                    fetch('/current_step')
                        .then(res => res.json())
                        .then(data => {
                            if (data.error) return;
                            document.getElementById("origin").textContent = `Origin: (${data.lat.toFixed(5)}, ${data.lng.toFixed(5)})`;
                            document.getElementById("instruction").textContent = `Instruction: ${data.instruction}`;
                            document.getElementById("step").textContent = `Step: ${data.step_index + 1}/${data.total_steps}`;
                            document.getElementById("map").src = `/map/${data.step_index}?t=${new Date().getTime()}`;
                        });
                }, 5000);
            }
            function resetRoute() {
                clearInterval(interval);
                fetch('/reset').then(() => location.reload());
            }
            </script>
        </head>
        <body onload="startTracking()">
            <h2>Live Navigation</h2>
            <form method="POST">
                <p>Origin is set automatically from your device.</p>
                Destination: <input name="destination" required>
                <button type="submit">Start Navigation</button>
            </form>
            <p id="origin">Origin: waiting...</p>
            <p id="instruction">Instruction: waiting...</p>
            <p id="step">Step: 0</p>
            <img id="map" src="" alt="Map" width="320" height="240">
            <br><br>
            <button onclick="resetRoute()">Plan Another Route</button>
        </body>
        </html>
    ''')

@app.route('/update_location', methods=['POST'])
def update_location():
    data = request.get_json()
    lat = data.get('lat')
    lng = data.get('lng')
    if lat is None or lng is None:
        return jsonify({'error': 'Invalid data'}), 400

    new_origin = f"{lat},{lng}"
    origin_changed = (current_route['origin'] != new_origin)
    current_route['origin'] = new_origin

    if current_route['steps']:
        current_step = current_route['steps'][current_route['step_index']]
        step_coords = (current_step['lat'], current_step['lng'])
        user_coords = (lat, lng)

        if geodesic(step_coords, user_coords).meters < 15:
            if current_route['step_index'] < len(current_route['steps']) - 1:
                current_route['step_index'] += 1

    if current_route['destination'] and origin_changed:
        update_route(new_origin, current_route['destination'])

    return jsonify({'status': 'Location updated'})

@app.route('/map/<int:step>')
def step_map(step):
    if step < 0 or step >= len(current_route['steps']):
        return "No such step", 404

    location = current_route['steps'][step]
    lat = location['lat']
    lng = location['lng']
    polyline = current_route['polyline']

    base_url = "https://maps.googleapis.com/maps/api/staticmap"
    params = {
        'size': f'{MAP_WIDTH}x{MAP_HEIGHT}',
        'zoom': '18',
        'center': f'{lat},{lng}',
        'path': f'color:0xff0000ff|weight:5|enc:{polyline}',
        'format': 'jpg-baseline',
        'key': GOOGLE_MAPS_API_KEY
    }

    markers = [
        f'markers=color:blue|label:{step+1}|{lat},{lng}',
        f'markers=color:red|label:E|{current_route["destination"]}'
    ]

    query = '&'.join([f'{k}={quote_plus(str(v))}' for k, v in params.items()])
    marker_query = '&'.join(markers)
    full_url = f"{base_url}?{query}&{marker_query}"

    response = requests.get(full_url)
    return send_file(io.BytesIO(response.content), mimetype='image/jpeg')

@app.route('/current_step')
def current_step():
    i = current_route['step_index']
    if i < 0 or i >= len(current_route['steps']):
        return jsonify({'error': 'No current step'}), 404
    step_data = current_route['steps'][i]
    return jsonify({
        'step_index': i,
        'lat': step_data['lat'],
        'lng': step_data['lng'],
        'instruction': step_data['instruction'],
        'total_steps': len(current_route['steps'])
    })

@app.route('/reset')
def reset():
    current_route['origin'] = None
    current_route['destination'] = None
    current_route['steps'] = []
    current_route['step_index'] = 0
    current_route['polyline'] = ''
    return "Route reset."

if __name__ == '__main__':
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except:
        local_ip = '127.0.0.1'
    print(f"Flask server running at http://{local_ip}:5000")
    app.run(host='0.0.0.0', port=5000)

import os
import time
import csv
from datetime import datetime
from io import StringIO

from flask import Flask, render_template, request, Response
from flask_restx import Api, Resource, fields
from flask_httpauth import HTTPBasicAuth
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
auth = HTTPBasicAuth()

APP_VERSION = "0.2.0"

# ---------- Database ----------
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///iot.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


class Measurement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(50), nullable=False, index=True)
    sensor_type = db.Column(db.String(50), nullable=False)
    device_unix_time = db.Column(db.Integer, nullable=False)
    device_local_time = db.Column(db.String(50), nullable=False)
    server_unix_time = db.Column(db.Integer, nullable=False)
    server_local_time = db.Column(db.String(50), nullable=False)
    data = db.Column(db.JSON, nullable=False)


# ---------- Authentication ----------
USERNAME = os.getenv("BASIC_AUTH_USERNAME", "admin")
PASSWORD = os.getenv("BASIC_AUTH_PASSWORD", "password123")

users = {
    USERNAME: generate_password_hash(PASSWORD)
}

@auth.verify_password
def verify_password(username, password):
    if username in users and check_password_hash(users.get(username), password):
        return username
    return None


@app.before_request
def require_authentication():
    # Correct way to protect all routes
    if not auth.current_user():
        return auth.login_required(lambda: None)()


# ---------- Helpers ----------
def get_temperature(measurement):
    if measurement.data and isinstance(measurement.data, dict):
        return measurement.data.get("temperature")
    return None


# ---------- API ----------
api = Api(
    app,
    version=APP_VERSION,
    title="IoT Platform API",
    description="""
    **IoT Data Platform**
    
    This API allows IoT devices to send sensor measurements and provides tools to explore and export the data.
    
    ### Main Features:
    - Receive flexible sensor data (JSON)
    - Store both device-side and server-side timestamps
    - Real-time visualization
    - CSV export with date filtering
    """,
    doc="/swagger",
    prefix="/api"
)

measurement_input = api.model("MeasurementInput", {
    "device_id": fields.String(required=True, example="device_001"),
    "sensor_type": fields.String(required=True, example="temperature"),
    "device_unix_time": fields.Integer(required=True, example=1726000000),
    "device_local_time": fields.String(required=True, example="2026-09-11 14:00:00"),
    "data": fields.Raw(required=True, example={"temperature": 23.5})
})


@api.route("/measurements")
class Measurements(Resource):
    @api.expect(measurement_input)
    @api.doc(
        description="""
        **Receive a new measurement from an IoT device**

        This is the main endpoint that field devices should call to send sensor data.

        ### Required fields:
        - `device_id`: Unique identifier of the device
        - `sensor_type`: Type of sensor (e.g. temperature, weather, energy)
        - `device_unix_time`: Unix timestamp when the measurement was taken on the device
        - `device_local_time`: Local datetime string when the measurement was taken on the device
        - `data`: JSON object containing the actual measured values (flexible structure)

        The server will automatically add:
        - Server reception Unix timestamp
        - Server reception local timestamp
        """,
        responses={
            201: "Measurement successfully stored",
            400: "Missing required fields"
        }
    )
    def post(self):
        """Receive measurement from an IoT device"""
        payload = request.get_json()

        required = ["device_id", "sensor_type", "device_unix_time", "device_local_time", "data"]
        if not all(k in payload for k in required):
            api.abort(400, f"Missing required fields: {required}")

        now = datetime.now()

        measurement = Measurement(
            device_id=payload["device_id"],
            sensor_type=payload["sensor_type"],
            device_unix_time=payload["device_unix_time"],
            device_local_time=payload["device_local_time"],
            server_unix_time=int(time.time()),
            server_local_time=now.strftime("%Y-%m-%d %H:%M:%S"),
            data=payload["data"]
        )

        db.session.add(measurement)
        db.session.commit()

        return {"message": "Measurement stored successfully", "id": measurement.id}, 201


@api.route("/devices/<string:device_id>/plot-data")
class DevicePlotData(Resource):
    @api.doc(
        description="""
        **Get data for the real-time temperature plot**

        Returns the latest temperature readings for a specific device.
        This endpoint is used by the live dashboard page to draw the interactive graph.

        ### Response format:
        - `times`: List of server timestamps
        - `values`: List of temperature values
        - `hover_texts`: List of detailed hover information for each point
        """,
        params={
            "device_id": "The unique ID of the device (example: device_001)"
        }
    )
    def get(self, device_id):
        """Get latest temperature data for interactive plot"""
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        measurements = (Measurement.query
                        .filter(
                            Measurement.device_id == device_id,
                            Measurement.device_unix_time >= int(today_start.timestamp())
                        )
                        .order_by(Measurement.device_unix_time.desc())
                        .all())

        measurements = list(reversed(measurements))

        times = []
        values = []
        hover_texts = []

        for m in measurements:
            temp = get_temperature(m)
            if temp is not None:
                times.append(m.server_local_time)
                values.append(temp)
                hover_texts.append(
                    f"Device: {m.device_id}<br>"
                    f"Device Local Time: {m.device_local_time}<br>"
                    f"Temperature: {temp} °C<br>"
                    f"Server Time: {m.server_local_time}"
                )

        return {
            "times": times,
            "values": values,
            "hover_texts": hover_texts
        }


@api.route("/devices/<string:device_id>/csv")
class DeviceCSV(Resource):
    @api.doc(
        description="""
        **Download measurement data as CSV**

        Exports all measurements of a specific device within an optional time range.

        ### Query Parameters:
        - `start` (optional): Start datetime (format: YYYY-MM-DD HH:MM:SS)
        - `end` (optional): End datetime (format: YYYY-MM-DD HH:MM:SS)

        If `start` and `end` are not provided, all available data for the device will be downloaded.

        ### Example:
        `/api/devices/device_001/csv?start=2026-09-11 10:00:00&end=2026-09-11 18:00:00`
        """,
        params={
            "device_id": "The unique ID of the device",
            "start": "Start datetime (example: 2026-09-11 10:00:00)",
            "end": "End datetime (example: 2026-09-11 18:00:00)"
        }
    )
    def get(self, device_id):
        """Download CSV file for a device"""
        start = request.args.get("start")
        end = request.args.get("end")

        query = Measurement.query.filter_by(device_id=device_id)

        if start:
            query = query.filter(Measurement.server_local_time >= start)
        if end:
            query = query.filter(Measurement.server_local_time <= end)

        measurements = query.order_by(Measurement.server_unix_time.asc()).all()

        output = StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "id", "device_id", "sensor_type",
            "device_unix_time", "device_local_time",
            "server_unix_time", "server_local_time",
            "data"
        ])

        for m in measurements:
            writer.writerow([
                m.id,
                m.device_id,
                m.sensor_type,
                m.device_unix_time,
                m.device_local_time,
                m.server_unix_time,
                m.server_local_time,
                str(m.data)
            ])

        output.seek(0)

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={device_id}_data.csv"
            }
        )

@api.route("/devices")
class DeviceList(Resource):
    @api.doc(
        description="""
        **List all devices that have sent data**

        Returns a list of unique device IDs that exist in the database.
        This is useful for discovering which devices are currently active.
        """
    )
    def get(self):
        """Get list of all devices"""
        devices = db.session.query(Measurement.device_id).distinct().all()
        device_list = [d[0] for d in devices]

        return {
            "count": len(device_list),
            "devices": device_list
        }


@api.route("/devices/<string:device_id>/latest")
class DeviceLatest(Resource):
    @api.doc(
        description="""
        **Get the latest measurement of a specific device**

        Returns the most recent measurement received from the given device.
        Useful for dashboards, monitoring, or checking the current status of a device.
        """,
        params={
            "device_id": "The unique ID of the device (example: device_001)"
        },
        responses={
            200: "Latest measurement returned successfully",
            404: "No measurements found for this device"
        }
    )
    def get(self, device_id):
        """Get the most recent measurement of a device"""
        measurement = (Measurement.query
                       .filter_by(device_id=device_id)
                       .order_by(Measurement.server_unix_time.desc())
                       .first())

        if not measurement:
            api.abort(404, f"No measurements found for device '{device_id}'")

        return {
            "id": measurement.id,
            "device_id": measurement.device_id,
            "sensor_type": measurement.sensor_type,
            "device_unix_time": measurement.device_unix_time,
            "device_local_time": measurement.device_local_time,
            "server_unix_time": measurement.server_unix_time,
            "server_local_time": measurement.server_local_time,
            "data": measurement.data
        }


@api.route("/measurements/<int:measurement_id>")
class MeasurementDetail(Resource):
    @api.doc(
        description="""
        **Delete a specific measurement**

        Permanently deletes one measurement from the database using its ID.

        Use this endpoint with caution. Deleted data cannot be recovered.
        """,
        params={
            "measurement_id": "The ID of the measurement you want to delete"
        },
        responses={
            200: "Measurement deleted successfully",
            404: "Measurement not found"
        }
    )
    def delete(self, measurement_id):
        """Delete a single measurement by ID"""
        measurement = Measurement.query.get(measurement_id)

        if not measurement:
            api.abort(404, f"Measurement with ID {measurement_id} not found")

        db.session.delete(measurement)
        db.session.commit()

        return {
            "message": f"Measurement {measurement_id} deleted successfully"
        }, 200


@api.route("/devices/<string:device_id>/data")
class DeviceData(Resource):
    @api.doc(
        description="""
        **Delete all measurements of a device**

        Permanently deletes **all** measurements that belong to the given device.

        This action is irreversible. Use it carefully (for example when resetting a device or cleaning test data).
        """,
        params={
            "device_id": "The unique ID of the device whose data should be deleted"
        },
        responses={
            200: "All measurements of the device deleted",
            404: "Device has no measurements"
        }
    )
    def delete(self, device_id):
        """Delete all measurements of a specific device"""
        deleted_count = Measurement.query.filter_by(device_id=device_id).delete()
        db.session.commit()

        if deleted_count == 0:
            api.abort(404, f"No measurements found for device '{device_id}'")

        return {
            "message": f"Successfully deleted {deleted_count} measurements for device '{device_id}'"
        }, 200
# ---------- Web Pages ----------
@app.route("/")
def index():
    return render_template("index.html", version=APP_VERSION)


@app.route("/devices")
def list_devices():
    devices = db.session.query(Measurement.device_id).distinct().all()
    device_list = [d[0] for d in devices]
    return render_template("devices.html", devices=device_list, version=APP_VERSION)


@app.route("/devices/<device_id>")
def device_detail(device_id):
    return render_template("device_detail.html", device_id=device_id, version=APP_VERSION)


# ---------- Create tables ----------
with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=True)
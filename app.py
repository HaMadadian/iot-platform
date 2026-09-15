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

APP_VERSION = "0.3.0"

# ---------- Database Configuration ----------
database_url = os.getenv("DATABASE_URL")

# Render sometimes gives postgres:// — SQLAlchemy needs postgresql://
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url or "sqlite:///iot.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# ---------- Models ----------
class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255))
    location = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "device_id": self.device_id,
            "name": self.name,
            "description": self.description,
            "location": self.location,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


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
    if not auth.current_user():
        return auth.login_required(lambda: None)()


# ---------- Helpers ----------
def get_temperature(measurement):
    if measurement.data and isinstance(measurement.data, dict):
        return measurement.data.get("temperature")
    return None


# ---------- API Setup ----------
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
    - Device registry
    - Real-time visualization
    - CSV export with date filtering
    """,
    doc="/swagger",
    prefix="/api"
)

# ---------- Input Models ----------
measurement_input = api.model("MeasurementInput", {
    "device_id": fields.String(required=True, example="device_001"),
    "sensor_type": fields.String(required=True, example="temperature"),
    "device_unix_time": fields.Integer(required=True, example=1726000000),
    "device_local_time": fields.String(required=True, example="2026-09-15 00:00:00"),
    "data": fields.Raw(required=True, example={"temperature": 23.5})
})

device_input = api.model("DeviceInput", {
    "device_id": fields.String(required=True, example="device_001"),
    "name": fields.String(required=True, example="Warehouse Temperature Sensor"),
    "description": fields.String(example="Sensor located in the main warehouse"),
    "location": fields.String(example="Building A - Zone 3")
})


# ---------- Measurements Endpoints ----------
@api.route("/measurements")
class Measurements(Resource):
    @api.expect(measurement_input)
    @api.doc(description="Receive a new measurement from an IoT device")
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


@api.route("/measurements/<int:measurement_id>")
class MeasurementDetail(Resource):
    @api.doc(description="Delete a specific measurement by ID")
    def delete(self, measurement_id):
        """Delete a single measurement"""
        measurement = Measurement.query.get(measurement_id)
        if not measurement:
            api.abort(404, f"Measurement with ID {measurement_id} not found")

        db.session.delete(measurement)
        db.session.commit()
        return {"message": f"Measurement {measurement_id} deleted successfully"}, 200


# ---------- Devices Endpoints ----------
@api.route("/devices")
class DeviceList(Resource):
    @api.doc(description="List all registered devices")
    def get(self):
        """Get all devices"""
        devices = Device.query.order_by(Device.created_at.desc()).all()
        return {
            "count": len(devices),
            "devices": [d.to_dict() for d in devices]
        }

    @api.expect(device_input)
    @api.doc(description="Register a new device")
    def post(self):
        """Register a new device"""
        data = request.get_json()

        if not data.get("device_id") or not data.get("name"):
            api.abort(400, "device_id and name are required")

        existing = Device.query.filter_by(device_id=data["device_id"]).first()
        if existing:
            api.abort(400, f"Device '{data['device_id']}' already exists")

        device = Device(
            device_id=data["device_id"],
            name=data["name"],
            description=data.get("description"),
            location=data.get("location"),
            is_active=True
        )

        db.session.add(device)
        db.session.commit()
        return device.to_dict(), 201


@api.route("/devices/<string:device_id>")
class DeviceDetail(Resource):
    @api.doc(description="Get details of a specific device")
    def get(self, device_id):
        """Get one device"""
        device = Device.query.filter_by(device_id=device_id).first_or_404(
            description=f"Device '{device_id}' not found"
        )
        return device.to_dict()

    @api.doc(description="Deactivate a device")
    def delete(self, device_id):
        """Deactivate a device"""
        device = Device.query.filter_by(device_id=device_id).first_or_404(
            description=f"Device '{device_id}' not found"
        )
        device.is_active = False
        db.session.commit()
        return {"message": f"Device '{device_id}' has been deactivated"}


@api.route("/devices/<string:device_id>/latest")
class DeviceLatest(Resource):
    @api.doc(description="Get the latest measurement of a specific device")
    def get(self, device_id):
        """Get the most recent measurement of a device"""
        measurement = (Measurement.query
                       .filter_by(device_id=device_id)
                       .order_by(Measurement.device_unix_time.desc())
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


@api.route("/devices/<string:device_id>/plot-data")
class DevicePlotData(Resource):
    @api.doc(description="Get data for the real-time temperature plot")
    def get(self, device_id):
        """Data for the interactive real-time plot"""
        day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        measurements = (Measurement.query
                        .filter_by(device_id=device_id)
                        .filter(Measurement.device_local_time >= day_start)
                        .order_by(Measurement.device_unix_time.desc())
                        .all())

        measurements = list(reversed(measurements))

        times = []
        values = []
        hover_texts = []

        for m in measurements:
            temp = get_temperature(m)
            if temp is not None:
                times.append(m.device_local_time)
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
        description="Download measurement data as CSV",
        params={
            "start": "Start datetime (example: 2026-09-11 10:00:00)",
            "end": "End datetime (example: 2026-09-11 18:00:00)"
        }
    )
    def get(self, device_id):
        """Download CSV for a device"""
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
                m.id, m.device_id, m.sensor_type,
                m.device_unix_time, m.device_local_time,
                m.server_unix_time, m.server_local_time,
                str(m.data)
            ])

        output.seek(0)

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={device_id}_data.csv"}
        )


@api.route("/devices/<string:device_id>/data")
class DeviceData(Resource):
    @api.doc(description="Delete all measurements of a specific device")
    def delete(self, device_id):
        """Delete all measurements of a device"""
        deleted_count = Measurement.query.filter_by(device_id=device_id).delete()
        db.session.commit()

        if deleted_count == 0:
            api.abort(404, f"No measurements found for device '{device_id}'")

        return {"message": f"Successfully deleted {deleted_count} measurements for device '{device_id}'"}, 200


# ---------- Web Pages ----------
@app.route("/")
def index():
    return render_template("index.html", version=APP_VERSION)


@app.route("/devices")
def list_devices():
    devices = Device.query.order_by(Device.created_at.desc()).all()
    return render_template("devices.html", devices=devices, version=APP_VERSION)


@app.route("/devices/<device_id>")
def device_dashboard(device_id):
    return render_template("device_detail.html", device_id=device_id, version=APP_VERSION)


# ---------- Create tables ----------
with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=True)
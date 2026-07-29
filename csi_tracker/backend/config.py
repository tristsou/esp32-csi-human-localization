import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DeviceConfig:
    id: str
    label: str
    x_m: float
    y_m: float
    source: str = "serial"
    port: str = ""
    baud: int = 921600
    host: str = ""
    tcp_port: int = 0

    def to_dict(self):
        return {
            "id": self.id,
            "label": self.label,
            "x_m": self.x_m,
            "y_m": self.y_m,
            "source": self.source,
            "port": self.port,
            "baud": self.baud,
            "host": self.host,
            "tcp_port": self.tcp_port,
        }


@dataclass
class RoomConfig:
    width_m: float = 6.0
    height_m: float = 4.0


@dataclass
class AppConfig:
    room: RoomConfig = field(default_factory=RoomConfig)
    calibration_seconds: int = 10
    max_people: int = 3
    devices: list = field(default_factory=list)

    @staticmethod
    def from_dict(data: dict) -> "AppConfig":
        room = RoomConfig(**data.get("room", {}))
        devices = [DeviceConfig(**d) for d in data.get("devices", [])]
        return AppConfig(
            room=room,
            calibration_seconds=data.get("calibration_seconds", 10),
            max_people=data.get("max_people", 3),
            devices=devices,
        )

    @staticmethod
    def load(path: str) -> "AppConfig":
        return AppConfig.from_dict(json.loads(Path(path).read_text()))

    def to_dict(self):
        return {
            "room": {"width_m": self.room.width_m, "height_m": self.room.height_m},
            "calibration_seconds": self.calibration_seconds,
            "max_people": self.max_people,
            "devices": [d.to_dict() for d in self.devices],
        }

    def save(self, path: str):
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    def validate(self):
        n = len(self.devices)
        if n < 2 or n > 6:
            raise ValueError(f"Number of devices must be between 2 and 6, got {n}")
        ids = [d.id for d in self.devices]
        if len(set(ids)) != len(ids):
            raise ValueError("Device ids must be unique")
        if not (1 <= self.max_people <= 3):
            raise ValueError("max_people must be between 1 and 3")

from enum import StrEnum
from typing import Annotated, List, Optional, Union

from pydantic import Field
from pydantic.dataclasses import dataclass

MULTI_ENDPOINT_TYPES = [67]


class DeviceClassification(StrEnum):
    LIGHT = "light"
    SWITCH = "switch"
    THERMOSTAT = "thermostat"
    BRIDGE = "bridge"
    SENSOR = "sensor"
    UNKNOWN = "unknown"


@dataclass
class LightCapabilities:
    power: bool = True
    dimmable: bool = True
    tunable_white: bool = False
    dynamic: bool = False
    color: bool = False
    colour: Annotated[bool, Field(alias="color")] = False
    # Confirmed via a real capture: standalone motion sensors (type 96) report their
    # trigger state through the recently_seen field of the normal fa db 13 status
    # struct, and some light/switch types with a built-in occupancy sensor (e.g. 37,
    # 49, 56 - "...with Motion and Ambient Light") report it via a distinct fa 54
    # ctrl_bytes packet, as an ADDITIONAL entity alongside their primary light/switch.
    # One flag covers both: on a SENSOR-classified device it drives the device's only
    # entity; on a LIGHT/SWITCH device it adds a secondary occupancy binary_sensor.
    motion_sensor: bool = False
    # binary_sensor device_class for the entity motion_sensor drives. "motion" for
    # genuine motion/occupancy-sensing hardware; "occupancy" for devices that reuse
    # the same recently_seen-as-trigger-flag mechanism for a different kind of
    # decaying "recently active" signal (e.g. type 112's battery scene remote -
    # confirmed via a real capture: recently_seen goes 1->0 ~19s after a button
    # press, same shape as a motion trigger, but it's not a PIR/motion sensor).
    sensor_device_class: str = "motion"


@dataclass
class SwitchCapabilities(LightCapabilities):
    dimmable: bool = False
    fan: bool = False
    plug: bool = False


@dataclass
class DeviceProtocol:
    BTLE: bool = True
    TCP: bool = False
    MATTER: bool = False


@dataclass
class OpcodeFamily:
    """Identifies which binary opcode family a device uses for control commands.

    Older GE/C-by-GE XLink Wi-Fi-direct devices (e.g. C by GE Sol, type 80) use a
    different opcode set for brightness (0xD2) and CCT (0xE2) than the newer Cync mesh
    devices which use 0xF0 for both.  All other fields on DeviceTypeInfo are transport or
    aesthetic; this one drives the binary packet structure.
    """

    sol_lamp: bool = False


@dataclass
class LightCharacteristics:
    min_kelvin: Optional[Annotated[int, Field(ge=2000, le=7000)]] = None
    max_kelvin: Optional[Annotated[int, Field(ge=2000, le=7000)]] = None
    lumens: Optional[Annotated[int, Field(ge=10)]] = None
    cri: Optional[Annotated[int, Field(ge=5, le=100)]] = None


@dataclass
class DeviceTypeInfo:
    type: DeviceClassification = Field(default=DeviceClassification.UNKNOWN)
    model_name: Optional[str] = "Unknown Device, See repo issue tracker"
    model_id: Optional[str] = None
    protocol: DeviceProtocol = Field(default_factory=DeviceProtocol)
    opcodes: OpcodeFamily = Field(default_factory=OpcodeFamily)
    capabilities: Union[LightCapabilities, SwitchCapabilities, None] = None
    characteristics: Optional[LightCharacteristics] = None
    supported: bool = Field(
        default=True, description="Whether this device type is supported"
    )
    notes: Optional[List[str]] = None

    @property
    def model_string(self) -> str:
        """Return a string representation of the model name, ID and characteristics."""
        base_str = self.model_name
        add_str = ""
        if self.model_id:
            add_str = self.model_id
        if self.type == DeviceClassification.LIGHT:
            if self.characteristics:
                if self.characteristics.lumens:
                    if add_str:
                        add_str += " "
                    add_str += f"{self.characteristics.lumens} lum"
                if self.characteristics.min_kelvin:
                    if (
                        self.characteristics.min_kelvin
                        and self.characteristics.max_kelvin
                    ):
                        if (
                            self.characteristics.min_kelvin
                            != self.characteristics.max_kelvin
                        ):
                            kelvin_data = f"{self.characteristics.min_kelvin}-{self.characteristics.max_kelvin}K"
                        else:
                            kelvin_data = f"{self.characteristics.min_kelvin}K"
                    else:
                        kelvin_data = f"{self.characteristics.min_kelvin}K"
                    if add_str:
                        add_str += " "
                    add_str += f"{kelvin_data}"
                if self.characteristics.cri:
                    if add_str:
                        add_str += " "
                        add_str += f"{self.characteristics.cri} CRI"
        if add_str:
            add_str = f" [{add_str}]"
        return base_str + add_str


"""Maps a device type ID to its corresponding DeviceTypeInfo."""
device_type_map = {
    1: DeviceTypeInfo(
        type=DeviceClassification.UNKNOWN,
        model_name="CLife A19 Standalone Bulb (Gen1)",
        protocol=DeviceProtocol(TCP=True),
        supported=False,
        notes=[
            "Confirmed to exist (DeviceType.java CLifeA19Gen1Standalone) but no "
            "capability data recoverable from the decompiled app for the original "
            "'CLife'-branded (pre-Cync) product line specifically - not guessed.",
        ],
    ),
    5: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White A19 Bulb",
        model_id=None,
        characteristics=LightCharacteristics(lumens=800),
        capabilities=LightCapabilities(tunable_white=True),
    ),
    6: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        model_id=None,
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    7: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        model_id=None,
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    8: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        model_id=None,
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    9: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Soft White A19 Bulb",
        model_id=None,
        capabilities=LightCapabilities(),
        characteristics=LightCharacteristics(lumens=800, min_kelvin=2700),
    ),
    10: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        model_id=None,
        capabilities=LightCapabilities(tunable_white=True),
    ),
    11: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        model_id=None,
        capabilities=LightCapabilities(tunable_white=True),
    ),
    13: DeviceTypeInfo(
        type=DeviceClassification.UNKNOWN,
        model_name="CLife A19 Standalone Bulb (TCO Gen2)",
        protocol=DeviceProtocol(TCP=True),
        supported=False,
        notes=[
            "Confirmed to exist (DeviceType.java CLifeA19TCOGen2Standalone) but no "
            "capability data recoverable from the decompiled app for the original "
            "'CLife'-branded (pre-Cync) product line specifically - not guessed.",
        ],
    ),
    14: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        model_id=None,
        capabilities=LightCapabilities(tunable_white=True),
    ),
    15: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        model_id=None,
        capabilities=LightCapabilities(tunable_white=True),
    ),
    17: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="White Dimmable A19 Bulb (BTLE only)",
        model_id="CLED199L2",
        capabilities=LightCapabilities(),
        characteristics=LightCharacteristics(min_kelvin=2700, lumens=760),
    ),
    18: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="White Dimmable A19 Bulb (BTLE only)",
        model_id="CLED199L2",
        capabilities=LightCapabilities(),
        characteristics=LightCharacteristics(min_kelvin=2700, lumens=760),
    ),
    19: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        protocol=DeviceProtocol(TCP=True),
        model_name="Tunable White A19 Bulb",
        capabilities=LightCapabilities(tunable_white=True),
    ),
    20: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        model_id=None,
        capabilities=LightCapabilities(tunable_white=True),
    ),
    21: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="C by GE Full Color A19 Bulb (BTLE only)",
        model_id="CLEDA1911C2",
        characteristics=LightCharacteristics(lumens=760),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    22: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="C by GE Full Color BR30 Bulb (BTLE only)",
        model_id="CLEDR3010C2",
        characteristics=LightCharacteristics(lumens=700),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    23: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    24: DeviceTypeInfo(
        type=DeviceClassification.UNKNOWN,
        model_name="CLife A19 Bulb (Gen2, Made for Google)",
        protocol=DeviceProtocol(TCP=True),
        supported=False,
        notes=[
            "Confirmed to exist (DeviceType.java "
            "CLifeA19Gen2MadeForGoogleCECTier2) but no capability data recoverable "
            "from the decompiled app for the original 'CLife'-branded (pre-Cync) "
            "product line specifically - not guessed.",
        ],
    ),
    25: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        model_id=None,
        capabilities=LightCapabilities(tunable_white=True),
    ),
    26: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="C by GE Tunable White BR30 Bulb (BTLE only)",
        model_id="CLEDR309S2",
        characteristics=LightCharacteristics(
            lumens=800, min_kelvin=2000, max_kelvin=7000
        ),
        capabilities=LightCapabilities(tunable_white=True),
    ),
    27: DeviceTypeInfo(
        type=DeviceClassification.UNKNOWN,
        model_name="CLife A19 Bulb (TCO Gen2, Made for Google)",
        protocol=DeviceProtocol(TCP=True),
        supported=False,
        notes=[
            "Confirmed to exist (DeviceType.java CLifeA19TCOGen2MadeForGoogle) but "
            "no capability data recoverable from the decompiled app for the "
            "original 'CLife'-branded (pre-Cync) product line specifically - not "
            "guessed.",
        ],
    ),
    28: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        model_id=None,
        capabilities=LightCapabilities(tunable_white=True),
    ),
    29: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        model_id=None,
        capabilities=LightCapabilities(tunable_white=True),
    ),
    30: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="C by GE Full Color A19 Bulb (BTLE only)",
        model_id="CLEDA1911C2",
        characteristics=LightCharacteristics(lumens=760),
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    31: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="C by GE Full Color A19 Bulb (BTLE only)",
        model_id="CLEDA1911C2",
        characteristics=LightCharacteristics(lumens=800),
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    32: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    33: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    34: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    35: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    36: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Light Switch",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(dimmable=True),
        notes=[
            "Confirmed dimmable by an owner; RGB/tunable-white support and exact "
            "protocol (BT only vs BT & WiFi vs Matter) unconfirmed, see issue #12",
        ],
    ),
    37: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Dimmer Switch with Motion and Ambient Light",
        model_id="CSWDMOCBWF1",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(dimmable=True, motion_sensor=True),
        notes=[
            "Built-in occupancy sensor confirmed on this switch family via a real "
            "capture (owner confirmed physical model has one); reports via a distinct "
            "fa 54 ctrl_bytes packet (dev_id at offset 9, trigger flag at offset 15), "
            "exposed as a secondary occupancy binary_sensor alongside the light. "
            "color/tunable_white removed: cross-referenced against the real Cync "
            "Android app's device-type capability table, this switch family has no "
            "CctColor/RgbColor capability at all - dimmable and motion_sensor confirmed "
            "correct, color/tunable_white were not.",
        ],
    ),
    38: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Circle Switch (Gen2)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=[
            "FourWireSwitchCircleGen2 in the real app - same Circle-style family "
            "as type 53, no Dimming/CctColor/RgbColor capability.",
        ],
    ),
    39: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Paddle Switch",
        model_id=" CSWONBLPWF1",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=[
            "dimmable/color/tunable_white all removed: cross-referenced against the "
            "real Cync Android app, this switch's load type is non-dimming (no "
            "Dimming or CctColor/RgbColor capability) - binary on/off only.",
        ],
    ),
    40: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Paddle Switch",
        model_id="CSWONBLTWF1",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=[
            "dimmable/color/tunable_white all removed: cross-referenced against the "
            "real Cync Android app, this switch's load type is non-dimming (no "
            "Dimming or CctColor/RgbColor capability) - binary on/off only.",
        ],
    ),
    41: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Reveal HD+ Full Color Under Cabinet Light - 12 Inch",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
        characteristics=LightCharacteristics(lumens=750),
    ),
    42: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Reveal HD+ Full Color Under Cabinet Light - 18 Inch",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
        characteristics=LightCharacteristics(lumens=1150),
    ),
    43: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Reveal HD+ Full Color Under Cabinet Light - 24 Inch",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
        characteristics=LightCharacteristics(lumens=1500),
    ),
    44: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Under Cabinet Puck Fixture",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    46: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name='Full Color 6" Recessed Can Retrofit Downlight',
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    47: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name='Reveal Full Color 6" Recessed Downlight',
        model_id="CFIXRSCR6CRVD",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    48: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="C by GE (C Start Smart) Dimmer Switch",
        model_id="CSWDMBLBWF1",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(dimmable=True),
        notes=[
            "Was labeled 'Paddle Switch'; the real Cync Android app's internal "
            "naming (FourWireSwitchDimmerGen1, no 'paddle' anywhere in that family's "
            "assets) points to this being the plain 4-wire dimmer, with the paddle "
            "variant actually being type 125 (FourWireSwitchPaddleDimmerGen1) - "
            "labels swapped accordingly. color/tunable_white also removed: this "
            "switch family has no CctColor/RgbColor capability in the real app.",
        ],
    ),
    49: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="C by GE (C Start Smart) Dimmer Switch with Motion and Ambient Light",
        model_id="CSWDMOCBWF1",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(dimmable=True, motion_sensor=True),
        notes=[
            "Built-in occupancy sensor confirmed via a real capture (owner-confirmed "
            "physical device, 'Master Closet Lights'): reports via a distinct fa 54 "
            "ctrl_bytes packet (dev_id at offset 9, trigger flag at offset 15), "
            "exposed as a secondary occupancy binary_sensor alongside the light. "
            "Toggle interval matched real foot traffic (irregular gaps, not a fixed "
            "heartbeat) over a multi-hour capture. color/tunable_white removed: "
            "cross-referenced against the real Cync Android app, this switch family "
            "has no CctColor/RgbColor capability - dimmable and motion_sensor "
            "confirmed correct.",
        ],
    ),
    51: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Switch",
        model_id=None,
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=[
            "dimmable/color/tunable_white all removed: cross-referenced against the "
            "real Cync Android app, this switch's load type is non-dimming (no "
            "Dimming or CctColor/RgbColor capability) - binary on/off only.",
        ],
    ),
    52: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Toggle Switch",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=[
            "Was unlabeled generic 'Switch'; the real Cync Android app's internal "
            "class name for this type is FourWireSwitchToggleGen1 - named accordingly. "
            "(Type 53, previously labeled 'Toggle Switch', is actually the Circle-style "
            "switch per the app - see its own entry.) dimmable/color/tunable_white all "
            "removed: this switch's load type is non-dimming in the real app.",
        ],
    ),
    53: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Circle Switch",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=[
            "Binary on/off only, confirmed via owner's debug logs (bri always 0 "
            "regardless of state) and cloud export showing the same deviceType used "
            "for both fan-wired and light-wired toggle switches",
            "Renamed from 'Toggle Switch': the real Cync Android app's internal class "
            "name for this type is FourWireSwitchCircleGen1 (type 52 is the actual "
            "Toggle-style switch) - the behavioral notes above describe the wire "
            "protocol for this type ID and are unaffected by the name correction.",
        ],
    ),
    55: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Dimmer Switch - No Neutral",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(dimmable=True),
        notes=[
            "color/tunable_white removed: cross-referenced against the real Cync "
            "Android app, this switch family has no CctColor/RgbColor capability - "
            "dimmable confirmed correct.",
        ],
    ),
    56: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Dimmable Motion Light Switch",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(dimmable=True, motion_sensor=True),
        notes=[
            "Same motion-switch family as 37/49 (owner-confirmed); hasn't yet been "
            "seen sending fa 54 in a capture (low foot traffic in that room during "
            "the capture window, not a negative result) but marked accordingly.",
            "color/tunable_white removed: cross-referenced against the real Cync "
            "Android app, this switch family has no CctColor/RgbColor capability - "
            "dimmable and motion_sensor confirmed correct.",
        ],
    ),
    57: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Paddle Switch - No Neutral",
        model_id="CSWONBLPWF1NN",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=[
            "dimmable/color/tunable_white all removed: cross-referenced against the "
            "real Cync Android app, this switch's load type is non-dimming (no "
            "Dimming or CctColor/RgbColor capability) - binary on/off only.",
        ],
    ),
    58: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Switch - No Neutral",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=[
            "dimmable/color/tunable_white all removed: cross-referenced against the "
            "real Cync Android app, this switch's load type is non-dimming (no "
            "Dimming or CctColor/RgbColor capability) - binary on/off only.",
        ],
    ),
    59: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Switch",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=[
            "dimmable/color/tunable_white all removed: cross-referenced against the "
            "real Cync Android app, this switch's load type is non-dimming (no "
            "Dimming or CctColor/RgbColor capability) - binary on/off only.",
        ],
    ),
    61: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Paddle Switch (TCO Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=["FourWireSwitchPaddleTCOGen1 - non-dimming, no color capability."],
    ),
    62: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Toggle Switch (TCO Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=["FourWireSwitchToggleTCOGen1 - non-dimming, no color capability."],
    ),
    63: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Circle Switch (TCO Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=["FourWireSwitchCircleTCOGen1 - non-dimming, no color capability."],
    ),
    64: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Indoor Plug",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(plug=True),
    ),
    65: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Indoor Plug (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(plug=True),
    ),
    66: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Indoor Plug (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(plug=True),
    ),
    67: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Outdoor Plug - Dual Outlet",
        model_id="CPLGOD2BLG1",
        protocol=DeviceProtocol(TCP=True, MATTER=True),
        capabilities=SwitchCapabilities(plug=True),
        notes=[
            "when reading the 0x83 internal state, bri byte is the bitmask, use -> & (1 << (sub_id - 1))",
            "temp can sometimes be 255, unknown what it means",
            "power byte is 1 if either outlet on, 0 if either off",
            "sending a command is easy, has a dedicated byte for sub-id directly after the device-id byte",
        ],
    ),
    68: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Indoor Plug",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(plug=True),
    ),
    69: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Outdoor Plug (Gen2)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(plug=True),
    ),
    71: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Dynamic Effects Premium Thin Light Strip",
        model_id="CSTR16CDID",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    72: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Dynamic Effects Premium Light Strip",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
        characteristics=LightCharacteristics(
            lumens=1600, min_kelvin=2000, max_kelvin=7000, cri=80
        ),
    ),
    73: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Outdoor Neon Light Strip - 16ft",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    74: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Outdoor Neon Light Strip - 32ft",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    75: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Outdoor Cafe Lights - 24ft (Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True, dynamic=True),
        notes=["Sibling of type 76's Cafe Lights - same dynamic-effects capability."],
    ),
    76: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Dynamic Effects Cafe` Lights",
        model_id="CCF48CDOD",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True, dynamic=True),
        characteristics=LightCharacteristics(
            lumens=130, min_kelvin=2000, max_kelvin=7000
        ),
    ),
    80: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="C by GE Sol / XLink Tunable White",
        protocol=DeviceProtocol(TCP=True),
        opcodes=OpcodeFamily(sol_lamp=True),
        capabilities=LightCapabilities(tunable_white=True),
    ),
    81: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Fan Controller",
        model_id="CSWFSBLBWF1/ST-1P",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(fan=True),
    ),
    82: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        capabilities=LightCapabilities(tunable_white=True),
    ),
    83: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        capabilities=LightCapabilities(tunable_white=True),
    ),
    85: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        capabilities=LightCapabilities(tunable_white=True),
    ),
    96: DeviceTypeInfo(
        type=DeviceClassification.SENSOR,
        model_name="Motion Sensor",
        capabilities=LightCapabilities(motion_sensor=True),
        notes=[
            "Standalone BTLE motion sensor accessory. Confirmed via a real capture "
            "across 3 physical units: reports fa db 13 (the normal internal-status "
            "struct) with recently_seen as the trigger flag (1=motion, 0=clear) - "
            "power/brightness/rgb stay fixed at 0, temp fixed at 255 (unused-field "
            "sentinel, same as noted on type 67).",
        ],
    ),
    97: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Edison ST19 Bulb (Gen2)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    98: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Edison G25 Bulb (Gen2)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    99: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Soft White Edison ST19 Bulb (Gen2)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(),
    ),
    100: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Soft White Edison G25 Bulb (Gen2)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(),
    ),
    101: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Bulb - BC (Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    102: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Bulb - BM (Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    103: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Reveal Soft White A19 Bulb (Gen2)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(),
        notes=[
            "SingleChipRevealSoftWhiteBulbA19Gen2 - 'Reveal' is a rendering-quality "
            "tier, not a color capability; no CctColor/RgbColor, same as the "
            "already-confirmed Soft White pattern (e.g. type 134).",
        ],
    ),
    104: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Outdoor PAR38 Bulb (Gen2)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    105: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color A21 Bulb (Gen2)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    107: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Reveal HD+ Bulb",
        model_id="CLEDA199CDRV",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    108: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Reveal Full Color BR30 Bulb (Gen2)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    109: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Reveal Full Color A21 Bulb (Gen2)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    110: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Indoor Value Light Strip - 16ft (Gen3)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    111: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Outdoor Plug (Gen3)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(plug=True),
    ),
    112: DeviceTypeInfo(
        type=DeviceClassification.SENSOR,
        model_name="Wire-Free Dimmer Switch (BTLE only, battery-powered)",
        capabilities=LightCapabilities(
            motion_sensor=True, sensor_device_class="occupancy"
        ),
        notes=[
            "Owner-confirmed model: a wire-free (battery) dimmer switch, not a "
            "generic scene remote. It has no output of its own - its "
            "'lightRing*' cloud-export fields (brightness/color/mode) describe "
            "a status LED ring, not a controllable light - and it dims its "
            "paired light directly over the BTLE mesh, invisible to the "
            "WiFi-connected bridge our TCP listener sees. "
            "occupancyEnable/occupancySensitivity present but disabled on these "
            "units. No wifiMac (BTLE-only, same as the type-96 motion sensors - "
            "see the wifiMac-optional cloud_api.py export fix). Initially marked "
            "unsupported after a toggle test produced nothing in "
            "unsupported_devices.log - that was a false negative: this type "
            "already has a metadata entry, so it's never routed through the "
            "*unsupported*-device capture path regardless of whether real "
            "packets arrive. A later real capture confirmed it: dev_id 34 "
            "(Garage Outside) sent a normal fa db 13 status struct with "
            "recently_seen going 1->0 about 19s after a physical button press - "
            "same shape as the type-96 motion sensor's trigger flag - while "
            "bri/red/green stayed 0 and temp stayed fixed at 255 (same unused-"
            "field sentinel as type 96). Treated as an occupancy-style 'recently "
            "active' binary_sensor rather than device_class=motion since this is "
            "a button press, not a PIR motion sensor.",
        ],
    ),
    113: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Wire-Free Dimmer with White Temperature Switch (BTLE only)",
        capabilities=SwitchCapabilities(dimmable=True, color=True, tunable_white=True),
        supported=False,
    ),
    114: DeviceTypeInfo(
        type=DeviceClassification.UNKNOWN,
        model_name="Wire-Free Smart Remote Dimmer",
        supported=False,
        notes=[
            "WireFreeSmartRemoteDimmer in the real app, classified ProductType."
            "WireFreeRemote - a generic scene remote. This is functionally "
            "different from type 112 (a wire-free dimmer that directly dims its "
            "own paired light over BTLE), whose own notes explicitly say it is "
            "NOT a generic scene remote - so 112's behavior/entity model should "
            "not be assumed here. No capture data available; left unsupported "
            "rather than guessed.",
        ],
    ),
    115: DeviceTypeInfo(
        type=DeviceClassification.UNKNOWN,
        model_name="Wire-Free Smart Remote Dimmer Plus Color Controller",
        supported=False,
        notes=[
            "WireFreeSmartRemoteDimmerPlusColorController - sibling of type 114 "
            "with added color-control buttons, same ProductType.WireFreeRemote "
            "classification and same caveats. No capture data available.",
        ],
    ),
    116: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Dimmer Switch (Gen3)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(dimmable=True),
        notes=["FourWireSwitchDimmerGen3 - no CctColor/RgbColor capability."],
    ),
    117: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Dimmer Switch with Motion Sensing (Gen3)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(dimmable=True, motion_sensor=True),
        notes=[
            "FourWireSwitchMotionSensingDimmerGen3 - same motion-switch family as "
            "37/49/56, no CctColor/RgbColor capability.",
        ],
    ),
    118: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Circle Switch (Gen3)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=["FourWireSwitchCircleGen3 - non-dimming, no color capability."],
    ),
    119: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Paddle Switch (Gen3)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=["FourWireSwitchPaddleGen3 - non-dimming, no color capability."],
    ),
    120: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Toggle Switch (Gen3)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(),
        notes=["FourWireSwitchToggleGen3 - non-dimming, no color capability."],
    ),
    121: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Fan Controller (Gen2)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(fan=True),
        notes=["FanSpeedSwitchGen2, sibling of the existing type 81 entry."],
    ),
    122: DeviceTypeInfo(
        type=DeviceClassification.THERMOSTAT,
        model_name="Thermostat (Gen2)",
        protocol=DeviceProtocol(TCP=True),
        notes=[
            "ThermostatGen2. cync-lan's own devices.py explicitly skips HVAC "
            "devices during config export parsing today (logged as 'currently "
            "unsupported, work is in progress') regardless of this entry.",
        ],
    ),
    123: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Indoor Value Light Strip - 32ft (Gen3)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    124: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Keypad Dimmer Switch (Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(dimmable=True),
        notes=[
            "FourWireSwitchKeypadDimmerGen1 - has a CustomizableButtons capability "
            "in the real app not represented anywhere in this data model yet; no "
            "CctColor/RgbColor capability.",
        ],
    ),
    125: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Paddle Dimmer Switch",
        model_id="CSWDMBLBWF1",
        protocol=DeviceProtocol(TCP=True, MATTER=True),
        capabilities=SwitchCapabilities(dimmable=True),
        notes=[
            "Was classified LIGHT with LightCapabilities(color, tunable_white); the "
            "real Cync Android app classifies every 4-wire switch type (including "
            "this one, FourWireSwitchPaddleDimmerGen1) as a Switch product with no "
            "CctColor/RgbColor capability - reclassified and dimmable-only "
            "SwitchCapabilities substituted accordingly. Also renamed from 'Dimmer "
            "Switch' to 'Paddle Dimmer Switch': the app's own naming/asset data "
            "points to the 'Paddle' descriptor belonging here rather than on type 48 "
            "(see that entry's notes).",
        ],
    ),
    128: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Dimmable A19 Bulb",
        model_id="CLEDA199LD1",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(),
        characteristics=LightCharacteristics(lumens=800, cri=90, min_kelvin=2700),
    ),
    129: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True),
    ),
    130: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True),
    ),
    131: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color A19 Bulb",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    132: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    133: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color LED Light Strip Controller",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    134: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Soft White A19 Bulb",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(),
        notes=[
            "SingleChipSoftWhiteA19 - directly confirmed against the real app's "
            "capability data (no CctColor/RgbColor), used as one of the sanity-"
            "check anchors for the naming-convention-based capability inference "
            "used throughout this block of newly-added entries.",
        ],
    ),
    135: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True),
    ),
    136: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True),
    ),
    137: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color A19 Bulb",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    138: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color BR30 Floodlight",
        characteristics=LightCharacteristics(
            lumens=750, min_kelvin=2000, max_kelvin=7000, cri=90
        ),
        model_id="CLEDR309CD1",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    139: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    140: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Outdoor PAR38 Floodlight",
        characteristics=LightCharacteristics(lumens=1300),
        model_id="CLEDP3815CD1",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    141: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    142: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    143: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    144: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True),
    ),
    145: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Tunable White Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True),
    ),
    146: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Edison ST19 Bulb",
        characteristics=LightCharacteristics(lumens=500),
        model_id="CLEDST196CDGS",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    147: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Edison G25 Bulb",
        characteristics=LightCharacteristics(lumens=500),
        model_id="CLEDG256CDGS",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    148: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="White Edison ST19 Bulb",
        protocol=DeviceProtocol(TCP=True),
        characteristics=LightCharacteristics(min_kelvin=2700),
        capabilities=LightCapabilities(),
    ),
    149: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Soft White Edison G25 Bulb (Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(),
    ),
    150: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Soft White Bulb - BC (Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(),
    ),
    151: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Soft White Decorative Candle Light",
        model_id="CLEDBM6LDGF",
        protocol=DeviceProtocol(TCP=True),
        characteristics=LightCharacteristics(lumens=500, min_kelvin=2700, cri=90),
        capabilities=LightCapabilities(),
    ),
    152: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Reveal HD+ White A19 Bulb",
        protocol=DeviceProtocol(TCP=True),
        characteristics=LightCharacteristics(min_kelvin=2700),
        capabilities=LightCapabilities(),
    ),
    153: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    154: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        protocol=DeviceProtocol(TCP=True),
        model_name="Full Color Light (Unknown)",
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    155: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Dynamic Effects E26 Bulb",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(dimmable=True, color=True, tunable_white=True),
        characteristics=LightCharacteristics(dynamic=True),
    ),
    156: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        protocol=DeviceProtocol(TCP=True),
        model_name="Full Color Light (Unknown)",
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    157: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Dynamic Effects BR30 Bulb",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True, dynamic=True),
    ),
    158: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    159: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    160: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    161: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    162: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    163: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    164: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        protocol=DeviceProtocol(TCP=True),
        model_name="Full Color Light (Unknown)",
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    165: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Light (Unknown)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    166: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Dynamic Effects Neon Light Strip - 10ft (Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True, dynamic=True),
    ),
    167: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Dynamic Effects Neon Light Strip - 16ft (Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True, dynamic=True),
    ),
    168: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color Dynamic Effects Hexagon Light Tile (Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True, dynamic=True),
    ),
    169: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        characteristics=LightCharacteristics(lumens=760),
        model_id="CFIXCNLR4CRVD",
        model_name="Reveal HD+ Full Color 4 Inch Wafer Downlight",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(tunable_white=True, color=True),
    ),
    170: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name='4" Wafer Light',
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(dimmable=True, color=True, tunable_white=True),
    ),
    171: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color A19 Bulb (Gen3)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    172: DeviceTypeInfo(
        type=DeviceClassification.SWITCH,
        model_name="Plug (TCO Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=SwitchCapabilities(plug=True),
    ),
    173: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color BR30 Bulb (Gen3)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    174: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name='Reveal Full Color 4 Inch Wafer Downlight (Gen2)',
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    175: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name='Reveal Full Color 6 Inch Wafer Downlight (Gen2)',
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    177: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name='Reveal Full Color 2 Inch Wafer Downlight (Gen2)',
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    180: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name='Reveal Full Color High Lumen 4 Inch Wafer Downlight (Gen2)',
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    181: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name='Reveal Full Color High Lumen 6 Inch Wafer Downlight (Gen2)',
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    182: DeviceTypeInfo(
        type=DeviceClassification.LIGHT,
        model_name="Full Color A19 Clear Spiral Bulb (Gen1)",
        protocol=DeviceProtocol(TCP=True),
        capabilities=LightCapabilities(color=True, tunable_white=True),
    ),
    224: DeviceTypeInfo(
        type=DeviceClassification.THERMOSTAT,
        model_name="Thermostat",
        protocol=DeviceProtocol(TCP=True),
    ),
    240: DeviceTypeInfo(
        type=DeviceClassification.UNKNOWN,
        model_name="Indoor Camera (Gen1)",
        supported=False,
        notes=[
            "CameraIndoorGen1. Cameras are a real, distinct Cync product family "
            "(ProductType.Camera) that almost certainly uses a different (cloud "
            "video streaming) protocol entirely, not the lighting/switch BLE-mesh "
            "protocol cync-lan implements - out of scope, not modeled beyond this "
            "placeholder so the deviceType is at least recognized instead of "
            "logged as fully unknown if one ever appears in an export.",
        ],
    ),
    241: DeviceTypeInfo(
        type=DeviceClassification.UNKNOWN,
        model_name="Outdoor Wired Camera (Gen1)",
        supported=False,
        notes=["CameraOutdoorWiredGen1 - see type 240's notes."],
    ),
    242: DeviceTypeInfo(
        type=DeviceClassification.UNKNOWN,
        model_name="Outdoor Battery Camera (Gen1)",
        supported=False,
        notes=["CameraOutdoorBatteryGen1 - see type 240's notes."],
    ),
}

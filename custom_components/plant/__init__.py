"""Support for monitoring plants."""
from __future__ import annotations

from collections import deque
import copy
from datetime import datetime, timedelta
from email.policy import default
import logging
from modulefinder import LOAD_CONST
import random
from tkinter.messagebox import NO
from unittest.mock import NonCallableMagicMock

from pydantic import NoneBytes
import voluptuous as vol

from homeassistant.components.recorder import history
from homeassistant.components.sensor import ENTITY_ID_FORMAT, PLATFORM_SCHEMA
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_PICTURE,
    ATTR_NAME,
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    CONDUCTIVITY,
    CONF_NAME,
    CONF_SENSORS,
    PERCENTAGE,
    STATE_OK,
    STATE_PROBLEM,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    TEMP_CELSIUS,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util, slugify

from .const import (
    CONF_CHECK_DAYS,
    CONF_IMAGE,
    CONF_MAX_BRIGHTNESS,
    CONF_MAX_CONDUCTIVITY,
    CONF_MAX_HUMIDITY,
    CONF_MAX_MOISTURE,
    CONF_MAX_TEMPERATURE,
    CONF_MIN_BATTERY_LEVEL,
    CONF_MIN_BRIGHTNESS,
    CONF_MIN_CONDUCTIVITY,
    CONF_MIN_HUMIDITY,
    CONF_MIN_MOISTURE,
    CONF_MIN_TEMPERATURE,
    CONF_PLANTBOOK,
    CONF_PLANTBOOK_MAPPING,
    CONF_SPECIES,
    DOMAIN,
    FLOW_PLANT_IMAGE,
    FLOW_PLANT_INFO,
    FLOW_PLANT_LIMITS,
    FLOW_PLANT_NAME,
    FLOW_PLANT_SPECIES,
    FLOW_SENSOR_BRIGHTNESS,
    FLOW_SENSOR_CONDUCTIVITY,
    FLOW_SENSOR_HUMIDITY,
    FLOW_SENSOR_MOISTURE,
    FLOW_SENSOR_TEMPERATURE,
    OPB_DISPLAY_PID,
    OPB_PID,
    READING_BATTERY,
    READING_BRIGHTNESS,
    READING_CONDUCTIVITY,
    READING_MOISTURE,
    READING_TEMPERATURE,
)

# STATE_OK = "OK"
# STATE_PROBLEM = "Problem"


_LOGGER = logging.getLogger(__name__)

DATA_UPDATED = "plant_data_updated"


DEFAULT_NAME = "plant"

ATTR_PROBLEM = "problem"
ATTR_SENSORS = "sensors"
PROBLEM_NONE = "none"
ATTR_MAX_BRIGHTNESS_HISTORY = "max_brightness"
ATTR_SPECIES = "species"
ATTR_LIMITS = FLOW_PLANT_LIMITS
ATTR_IMAGE = "image"
ATTR_EXTERNAL_SENSOR = "external_sensor"

SERVICE_REPLACE_SENSOR = "replace_sensor"

# we're not returning only one value, we're returning a dict here. So we need
# to have a separate literal for it to avoid confusion.
ATTR_DICT_OF_UNITS_OF_MEASUREMENT = "unit_of_measurement_dict"


CONF_SENSOR_BATTERY_LEVEL = READING_BATTERY
CONF_SENSOR_MOISTURE = READING_MOISTURE
CONF_SENSOR_CONDUCTIVITY = READING_CONDUCTIVITY
CONF_SENSOR_TEMPERATURE = READING_TEMPERATURE
CONF_SENSOR_BRIGHTNESS = READING_BRIGHTNESS

CONF_WARN_BRIGHTNESS = "warn_low_brightness"

DEFAULT_MIN_BATTERY_LEVEL = 20
DEFAULT_MIN_TEMPERATURE = 10
DEFAULT_MAX_TEMPERATURE = 40
DEFAULT_MIN_MOISTURE = 20
DEFAULT_MAX_MOISTURE = 60
DEFAULT_MIN_CONDUCTIVITY = 500
DEFAULT_MAX_CONDUCTIVITY = 3000
DEFAULT_MIN_BRIGHTNESS = 0
DEFAULT_MAX_BRIGHTNESS = 100000
DEFAULT_CHECK_DAYS = 3

STATE_LOW = "Low"
STATE_HIGH = "High"

"""
SCHEMA_SENSORS = vol.Schema(
    {
        vol.Optional(CONF_SENSOR_BATTERY_LEVEL): cv.entity_id,
        vol.Optional(CONF_SENSOR_MOISTURE): cv.entity_id,
        vol.Optional(CONF_SENSOR_CONDUCTIVITY): cv.entity_id,
        vol.Optional(CONF_SENSOR_TEMPERATURE): cv.entity_id,
        vol.Optional(CONF_SENSOR_BRIGHTNESS): cv.entity_id,
    }
)

PLANT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SENSORS): vol.Schema(SCHEMA_SENSORS),
        vol.Optional(
            CONF_MIN_BATTERY_LEVEL, default=DEFAULT_MIN_BATTERY_LEVEL
        ): cv.positive_int,
        vol.Optional(CONF_MIN_TEMPERATURE, default=DEFAULT_MIN_TEMPERATURE): vol.Coerce(
            float
        ),
        vol.Optional(CONF_MAX_TEMPERATURE, default=DEFAULT_MAX_TEMPERATURE): vol.Coerce(
            float
        ),
        vol.Optional(CONF_MIN_MOISTURE, default=DEFAULT_MIN_MOISTURE): cv.positive_int,
        vol.Optional(CONF_MAX_MOISTURE, default=DEFAULT_MAX_MOISTURE): cv.positive_int,
        vol.Optional(
            CONF_MIN_CONDUCTIVITY, default=DEFAULT_MIN_CONDUCTIVITY
        ): cv.positive_int,
        vol.Optional(
            CONF_MAX_CONDUCTIVITY, default=DEFAULT_MAX_CONDUCTIVITY
        ): cv.positive_int,
        vol.Optional(
            CONF_MIN_BRIGHTNESS, default=DEFAULT_MIN_BRIGHTNESS
        ): cv.positive_int,
        vol.Optional(
            CONF_MAX_BRIGHTNESS, default=DEFAULT_MAX_BRIGHTNESS
        ): cv.positive_int,
        vol.Optional(CONF_CHECK_DAYS, default=DEFAULT_CHECK_DAYS): cv.positive_int,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_SPECIES): cv.string,
        vol.Optional(CONF_IMAGE): cv.string,
        vol.Optional(CONF_WARN_BRIGHTNESS, default=True): cv.boolean,
    }
)
PLANTBOOK_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PLANTBOOK_CLIENT): cv.string,
        vol.Required(CONF_PLANTBOOK_SECRET): cv.string,
    }
)


CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: {cv.string: (vol.Any(PLANT_SCHEMA, PLANTBOOK_SCHEMA))}},
    extra=vol.ALLOW_EXTRA,
)
"""

# Flag for enabling/disabling the loading of the history from the database.
# This feature is turned off right now as its tests are not 100% stable.
ENABLE_LOAD_HISTORY = False

PLANTBOOK_TOKEN = None


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the OpenPlantBook component."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up OpenPlantBook from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # If you need to create some dummy sensors to play with
    # await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    plant = PlantDevice(hass, entry)
    pspieces = PlantSpecies(hass, entry, plant)
    pmaxm = PlantMaxMoisture(hass, entry, plant)
    pminm = PlantMinMoisture(hass, entry, plant)
    pmaxt = PlantMaxTemperature(hass, entry, plant)
    pmint = PlantMinTemperature(hass, entry, plant)
    pmaxb = PlantMaxBrightness(hass, entry, plant)
    pminb = PlantMinBrightness(hass, entry, plant)
    pmaxc = PlantMaxConductivity(hass, entry, plant)
    pminc = PlantMinConductivity(hass, entry, plant)
    pmaxh = PlantMaxHumidity(hass, entry, plant)
    pminh = PlantMinHumidity(hass, entry, plant)

    pcurb = PlantCurrentBrightness(hass, entry, plant)
    pcurc = PlantCurrentConductivity(hass, entry, plant)
    pcurm = PlantCurrentMoisture(hass, entry, plant)
    pcurt = PlantCurrentTempertature(hass, entry, plant)
    pcurh = PlantCurrentHumidity(hass, entry, plant)

    hass.data[DOMAIN][entry.entry_id] = plant

    component = EntityComponent(_LOGGER, DOMAIN, hass)

    plant_entities = [
        plant,
        pspieces,
        pmaxm,
        pminm,
        pmaxt,
        pmint,
        pmaxb,
        pminb,
        pmaxc,
        pminc,
        pmaxh,
        pminh,
        pcurb,
        pcurc,
        pcurm,
        pcurt,
        pcurh,
    ]
    await component.async_add_entities(plant_entities)
    _LOGGER.info("Baz")

    # hass.data[DOMAIN]["12345"] = PlantSpecies(hass, entry)
    device_id = plant.device_id
    await _plant_add_to_device_registry(hass, plant_entities, device_id)

    plant.add_thresholds(
        max_moisture=pmaxm,
        min_moisture=pminm,
        max_temperature=pmaxt,
        min_temperature=pmint,
        max_brightness=pmaxb,
        min_brightness=pminb,
        max_conductivity=pmaxc,
        min_conductivity=pminc,
        max_humidity=pmaxh,
        min_humidity=pminh,
    )
    plant.add_sensors(
        temperature=pcurt,
        moisture=pcurm,
        conductivity=pcurc,
        brightness=pcurb,
    )
    plant.add_species(species=pspieces)

    # _LOGGER.info("GOC id: %s", goc)
    # _LOGGER.info("Entry id: %s", entry.entry_id)
    # all_entities = er.async_entries_for_config_entry(erreg, entry.entry_id)
    # _LOGGER.info("New entities: %s", all_entities)

    async def replace_sensor(call: ServiceCall) -> None:
        meter_entity = call.data.get("meter_entity")
        new_sensor = call.data.get("new_sensor")
        if not meter_entity.startswith(DOMAIN + "."):
            _LOGGER.warning(
                "Refuse to update non-%s entities: %s", DOMAIN, meter_entity
            )
            return False
        if not new_sensor.startswith("sensor.") and new_sensor != "":
            _LOGGER.warning("%s is not a sensor", new_sensor)
            return False

        try:
            meter = hass.states.get(meter_entity)
        except AttributeError:
            _LOGGER.error("Meter entity %s not found", meter_entity)
            return False
        if meter is None:
            _LOGGER.error("Meter entity %s not found", meter_entity)
            return False

        if new_sensor != "":
            try:
                test = hass.states.get(new_sensor)
            except AttributeError:
                _LOGGER.error("New sensor entity %s not found", meter_entity)
                return False
            if test is None:
                _LOGGER.error("New sensor entity %s not found", meter_entity)
                return False
        else:
            _LOGGER.info("New sensor is blank, removing current value")
            new_sensor = None

        _LOGGER.info(
            "Going to replace the external sensor for %s with %s",
            meter_entity,
            new_sensor,
        )

        attr = {}
        for key in meter.attributes:
            attr[key] = meter.attributes[key]
        attr[ATTR_EXTERNAL_SENSOR] = new_sensor
        _LOGGER.info(meter.attributes)
        _LOGGER.info(attr)
        hass.states.async_set(
            entity_id=meter_entity, new_state=meter.state, attributes=attr
        )

    if not DOMAIN in hass.services.async_services():
        hass.services.async_register(DOMAIN, SERVICE_REPLACE_SENSOR, replace_sensor)
    return True


async def _plant_add_to_device_registry(
    hass: HomeAssistant, plant_entities: list[Entity], device_id: str
) -> None:
    """Add all related entities to the correct device_id"""

    # There must be a better way to do this, but I just can't find a way to set the
    # device_id when adding the entities.
    for entity in plant_entities:
        erreg = er.async_get(hass)
        erreg.async_update_entity(entity.registry_entry.entity_id, device_id=device_id)


class PlantDevice(Entity):
    """Base device for plants"""

    def __init__(self, hass: HomeAssistant, config: ConfigEntry) -> None:
        """Initialize the Plant component."""
        self._config = config
        # _LOGGER.info("Init plantdevice %s", config.data)
        self._attr_name = config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]
        self._config_entries = []
        self._attr_entity_picture = config.data[FLOW_PLANT_INFO][FLOW_PLANT_LIMITS].get(
            ATTR_ENTITY_PICTURE
        )
        self._attr_unique_id = self._config.entry_id

        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self.name.replace(" ", "_")))
        self.species = None

        self.max_moisture = None
        self.min_moisture = None
        self.max_temperature = None
        self.min_temperature = None
        self.max_conductivity = None
        self.min_conductivity = None
        self.max_brightness = None
        self.min_brightness = None
        self.max_humidity = None
        self.min_humidity = None

        self.sensor_moisture = None
        self.sensor_temperature = None
        self.sensor_conductivity = None
        self.sensor_brightness = None
        self.sensor_humidity = None

        self.conductivity_status = None
        self.brightness_status = None
        self.moisture_status = None
        self.temperature_status = None
        self.humidity_status = None

        # Is there a better way to add an entity to the device registry?
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=config.entry_id,
            identifiers={(DOMAIN, self.entity_id)},
            name=self.name,
            model=config.data[FLOW_PLANT_INFO][FLOW_PLANT_LIMITS][OPB_DISPLAY_PID],
        )
        _LOGGER.info("Getting device for %s entity id %s", DOMAIN, self.unique_id)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, self.entity_id)}
        )
        self._device_id = device.id

    @property
    def entity_category(self):
        return None

    @property
    def device_id(self):
        """The device ID used for all the entities"""
        return self._device_id

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": self.name,
            "config_entries": self._config_entries,
        }

    @property
    def extra_state_attributes(self) -> dict:
        """Return the device specific state attributes."""
        if not self.species:
            return {}
        attributes = {
            "species": self.species.entity_id,
            "moisture_status": self.moisture_status,
            "temperature_status": self.temperature_status,
            "conductivity_status": self.conductivity_status,
            "brightness_status": self.brightness_status,
            "humidity_status": self.humidity_status,
            "meters": {
                "moisture": None,
                "temperature": None,
                "humidity": None,
                "conductivity": None,
                "brightness": None,
            },
            "thresholds": {
                "temperature": {
                    "max": self.max_temperature.entity_id,
                    "min": self.min_temperature.entity_id,
                },
                "brightness": {
                    "max": self.max_brightness.entity_id,
                    "min": self.min_brightness.entity_id,
                },
                "moisture": {
                    "max": self.max_moisture.entity_id,
                    "min": self.min_moisture.entity_id,
                },
                "conductivity": {
                    "max": self.max_conductivity.entity_id,
                    "min": self.min_conductivity.entity_id,
                },
                "humidity": {
                    "max": self.max_humidity.entity_id,
                    "min": self.min_humidity.entity_id,
                },
            },
        }
        if self.sensor_moisture is not None:
            attributes["meters"]["moisture"] = self.sensor_moisture.entity_id
        if self.sensor_conductivity is not None:
            attributes["meters"]["conductivity"] = self.sensor_conductivity.entity_id
        if self.sensor_brightness is not None:
            attributes["meters"]["brightness"] = self.sensor_brightness.entity_id
        if self.sensor_temperature is not None:
            attributes["meters"]["temperature"] = self.sensor_temperature.entity_id
        if self.sensor_humidity is not None:
            attributes["meters"]["humidity"] = self.sensor_humidity.entity_id

        return attributes

    def add_image(self, image_url: str | None) -> None:
        """Set new entity_picture"""
        self._attr_entity_picture = image_url

    def add_species(self, species: str | None) -> None:
        """Set new species"""
        self.species = species

    def add_thresholds(
        self,
        max_moisture: Entity | None,
        min_moisture: Entity | None,
        max_temperature: Entity | None,
        min_temperature: Entity | None,
        max_conductivity: Entity | None,
        min_conductivity: Entity | None,
        max_brightness: Entity | None,
        min_brightness: Entity | None,
        max_humidity: Entity | None,
        min_humidity: Entity | None,
    ) -> None:
        """Add the threshold entities"""
        self.max_moisture = max_moisture
        self.min_moisture = min_moisture
        self.max_temperature = max_temperature
        self.min_temperature = min_temperature
        self.max_conductivity = max_conductivity
        self.min_conductivity = min_conductivity
        self.max_brightness = max_brightness
        self.min_brightness = min_brightness
        self.max_humidity = max_humidity
        self.min_humidity = min_humidity

    def add_sensors(
        self,
        moisture: Entity | None,
        temperature: Entity | None,
        conductivity: Entity | None,
        brightness: Entity | None,
    ) -> None:
        """Add the sensor entities"""
        self.sensor_moisture = moisture
        self.sensor_temperature = temperature
        self.sensor_conductivity = conductivity
        self.sensor_brightness = brightness

    def update(self) -> None:
        """Run on every update of the entities"""

        state = STATE_OK

        if (
            self.sensor_moisture is not None
            and self.sensor_moisture.state != STATE_UNKNOWN
        ):
            if int(self.sensor_moisture.state) < int(self.min_moisture.state):
                self.moisture_status = STATE_LOW
                state = STATE_PROBLEM
            elif int(self.sensor_moisture.state) > int(self.max_moisture.state):
                self.moisture_status = STATE_HIGH
                state = STATE_PROBLEM
            else:
                self.moisture_status = STATE_OK

        if (
            self.sensor_conductivity is not None
            and self.sensor_conductivity.state != STATE_UNKNOWN
        ):
            if int(self.sensor_conductivity.state) < int(self.min_conductivity.state):
                self.conductivity_status = STATE_LOW
                state = STATE_PROBLEM
            elif int(self.sensor_conductivity.state) > int(self.max_conductivity.state):
                self.conductivity_status = STATE_HIGH
                state = STATE_PROBLEM
            else:
                self.conductivity_status = STATE_OK

        if (
            self.sensor_temperature is not None
            and self.sensor_temperature.state != STATE_UNKNOWN
        ):
            if int(self.sensor_temperature.state) < int(self.min_temperature.state):
                self.temperature_status = STATE_LOW
                state = STATE_PROBLEM
            elif int(self.sensor_temperature.state) > int(self.max_temperature.state):
                self.temperature_status = STATE_HIGH
                state = STATE_PROBLEM
            else:
                self.temperature_status = STATE_OK

        if (
            self.sensor_humidity is not None
            and self.sensor_humidity.state != STATE_UNKNOWN
        ):
            if int(self.sensor_humidity.state) < int(self.min_humidity.state):
                self.humidity_status = STATE_LOW
                state = STATE_PROBLEM
            elif int(self.sensor_humidity.state) > int(self.humidity.state):
                self.humidity_status = STATE_HIGH
                state = STATE_PROBLEM
            else:
                self.humidity_status = STATE_OK

        # TODO
        # How to handle brightness?

        self._attr_state = state


class PlantSpecies(RestoreEntity):
    """The species entity"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        """Initialize the Plant component."""
        self._config = config
        self._attr_name = f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Species"
        self._attr_state = config.data[FLOW_PLANT_INFO][FLOW_PLANT_SPECIES]
        self._plant = plantdevice
        self._attr_unique_id = f"{self._config.entry_id}-species"
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self.name.replace(" ", "_")))

    @property
    def entity_category(self):
        """The category of the entity"""
        return EntityCategory.CONFIG

    @property
    def device_info(self):
        """Device info for the entity"""
        return {
            "identifiers": {(DOMAIN, self._plant.unique_id)},
            "name": self.name,
        }

    async def async_update(self):
        """Run on every update"""

        # Here we ensure that you can change the species from the GUI, and we update
        # all parameters to match the new species
        new_species = self.hass.states.get(self.entity_id).state
        if new_species != self._attr_state and self._attr_state != STATE_UNKNOWN:
            opb_plant = None
            _LOGGER.info(
                "Species changed from '%s' to '%s'", self._attr_state, new_species
            )
            display_species = new_species

            if "openplantbook" in self.hass.services.async_services():
                _LOGGER.info("We have OpenPlantbook configured")
                await self.hass.services.async_call(
                    domain="openplantbook",
                    service="get",
                    service_data={"species": new_species},
                    blocking=True,
                    limit=30,
                )
                try:
                    opb_plant = self.hass.states.get(
                        "openplantbook."
                        + new_species.replace("'", "").replace(" ", "_")
                    )

                    _LOGGER.info("Result: %s", opb_plant)
                    _LOGGER.info("Result A: %s", opb_plant.attributes)
                except AttributeError:
                    _LOGGER.warning("Did not find '%s' in OpenPlantbook", new_species)
                    await self.hass.services.async_call(
                        domain="persistent_notification",
                        service="create",
                        service_data={
                            "title": "Species not found",
                            "message": f"Could not find '{new_species}' in OpenPlantbook",
                        },
                    )
                    return True
            if opb_plant:
                _LOGGER.info(
                    "Setting entity_image to %s", opb_plant.attributes[FLOW_PLANT_IMAGE]
                )
                self._plant.add_image(opb_plant.attributes[FLOW_PLANT_IMAGE])

                for (ha_attribute, opb_attribute) in CONF_PLANTBOOK_MAPPING.items():

                    set_entity = getattr(self._plant, ha_attribute)

                    set_entity_id = set_entity.entity_id
                    self.hass.states.get(set_entity_id).state = opb_plant.attributes[
                        opb_attribute
                    ]
                self._attr_state = opb_plant.attributes[OPB_PID]
                self.async_write_ha_state()
                display_species = opb_plant.attributes[OPB_DISPLAY_PID]

            else:
                # We just accept whatever species the user sets.
                # They can always change it later
                _LOGGER.info("OpenPlantbook is not configured")

            device_registry = dr.async_get(self.hass)
            device_registry.async_update_device(
                device_id=self._plant.device_id,
                model=display_species,
            )

    async def async_added_to_hass(self) -> None:
        """Restore state of species on startup."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if not state:
            return
        self._attr_state = state.state

        async_dispatcher_connect(
            self.hass, DATA_UPDATED, self._schedule_immediate_update
        )

    @callback
    def _schedule_immediate_update(self):
        self.async_schedule_update_ha_state(True)


class PlantMinMax(RestoreEntity):
    """Parent class for the min/max classes below"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        """Initialize the Plant component."""
        self._config = config
        self._plant = plantdevice
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self.name.replace(" ", "_")))
        if not self._attr_state or self._attr_state == STATE_UNKNOWN:
            self._attr_state = self._default_state

    @property
    def entity_category(self):
        return EntityCategory.CONFIG

    def update(self) -> None:
        """Allow the state to be changed from the UI and saved in restore_state."""
        self._attr_state = self.hass.states.get(self.entity_id).state
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore state of thresholds on startup."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if not state:
            return
        self._attr_state = state.state

        async_dispatcher_connect(
            self.hass, DATA_UPDATED, self._schedule_immediate_update
        )

    @callback
    def _schedule_immediate_update(self):
        self.async_schedule_update_ha_state(True)


class PlantMaxMoisture(PlantMinMax):
    """Entity class for max moisture threshold"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        """Initialize the Plant component."""
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Max Moisture"
        )
        self._default_state = config.data[FLOW_PLANT_INFO][FLOW_PLANT_LIMITS].get(
            CONF_MAX_MOISTURE, STATE_UNKNOWN
        )
        self._attr_unique_id = f"{config.entry_id}-max-moisture"

        super().__init__(hass, config, plantdevice)

    @property
    def device_class(self):
        return "humidity"


class PlantMinMoisture(PlantMinMax):
    """Entity class for min moisture threshold"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        """Initialize the Plant component."""
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Min Moisture"
        )
        self._default_state = config.data[FLOW_PLANT_INFO][FLOW_PLANT_LIMITS].get(
            CONF_MIN_MOISTURE, STATE_UNKNOWN
        )
        self._attr_unique_id = f"{config.entry_id}-min-moisture"

        super().__init__(hass, config, plantdevice)

    @property
    def device_class(self):
        return "humidity"


class PlantMaxTemperature(PlantMinMax):
    """Entity class for max temperature threshold"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        """Initialize the Plant component."""
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Max Temperature"
        )
        self._default_state = config.data[FLOW_PLANT_INFO][FLOW_PLANT_LIMITS].get(
            CONF_MAX_TEMPERATURE, STATE_UNKNOWN
        )
        self._attr_unique_id = f"{config.entry_id}-max-temperature"
        super().__init__(hass, config, plantdevice)

    @property
    def device_class(self):
        return "temperature"


class PlantMinTemperature(PlantMinMax):
    """Entity class for min temperature threshold"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        """Initialize the Plant component."""
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Min Temperature"
        )
        self._default_state = config.data[FLOW_PLANT_INFO][FLOW_PLANT_LIMITS].get(
            CONF_MIN_TEMPERATURE, STATE_UNKNOWN
        )
        self._attr_unique_id = f"{config.entry_id}-min-temperature"
        super().__init__(hass, config, plantdevice)

    @property
    def device_class(self):
        return "temperature"


class PlantMaxBrightness(PlantMinMax):
    """Entity class for max brightness threshold"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        """Initialize the Plant component."""
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Max Brightness"
        )
        self._default_state = config.data[FLOW_PLANT_INFO][FLOW_PLANT_LIMITS].get(
            CONF_MAX_BRIGHTNESS, STATE_UNKNOWN
        )
        self._attr_unique_id = f"{config.entry_id}-max-brightness"
        super().__init__(hass, config, plantdevice)

    @property
    def device_class(self):
        return "illuminance"


class PlantMinBrightness(PlantMinMax):
    """Entity class for min brightness threshold"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        """Initialize the Plant component."""
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Min Brghtness"
        )
        self._default_state = config.data[FLOW_PLANT_INFO][FLOW_PLANT_LIMITS].get(
            CONF_MIN_BRIGHTNESS, STATE_UNKNOWN
        )
        self._attr_unique_id = f"{config.entry_id}-min-brightness"
        super().__init__(hass, config, plantdevice)

    @property
    def device_class(self):
        return "illuminance"


class PlantMaxConductivity(PlantMinMax):
    """Entity class for max conductivity threshold"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        """Initialize the Plant component."""
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Max Condictivity"
        )
        self._default_state = config.data[FLOW_PLANT_INFO][FLOW_PLANT_LIMITS].get(
            CONF_MAX_CONDUCTIVITY, STATE_UNKNOWN
        )
        self._attr_unique_id = f"{config.entry_id}-max-conductivity"
        super().__init__(hass, config, plantdevice)


class PlantMinConductivity(PlantMinMax):
    """Entity class for min conductivity threshold"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        """Initialize the Plant component."""
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Min Condictivity"
        )
        self._default_state = config.data[FLOW_PLANT_INFO][FLOW_PLANT_LIMITS].get(
            CONF_MIN_CONDUCTIVITY, STATE_UNKNOWN
        )
        self._attr_unique_id = f"{config.entry_id}-min-conductivity"
        super().__init__(hass, config, plantdevice)


class PlantMaxHumidity(PlantMinMax):
    """Entity class for max humidity threshold"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        """Initialize the Plant component."""
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Max Humidity"
        )
        self._default_state = config.data[FLOW_PLANT_INFO][FLOW_PLANT_LIMITS].get(
            CONF_MAX_HUMIDITY, STATE_UNKNOWN
        )
        self._attr_unique_id = f"{config.entry_id}-max-humidity"
        super().__init__(hass, config, plantdevice)


class PlantMinHumidity(PlantMinMax):
    """Entity class for min conductivity threshold"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        """Initialize the Plant component."""
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Min Humidity"
        )
        self._default_state = config.data[FLOW_PLANT_INFO][FLOW_PLANT_LIMITS].get(
            CONF_MIN_HUMIDITY, STATE_UNKNOWN
        )
        self._attr_unique_id = f"{config.entry_id}-min-humidity"
        super().__init__(hass, config, plantdevice)


class PlantCurrentStatus(RestoreEntity):
    """Parent class for the meter classes below"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        """Initialize the Plant component."""
        self._hass = hass
        self._config = config
        self._default_state = 0
        self._plant = plantdevice
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self.name.replace(" ", "_")))
        # self.entity_slug = self.name
        # self.entity_id = ENTITY_ID_FORMAT.format(
        #     slugify(self.entity_slug.replace(" ", "_"))
        # )
        if not self._attr_state or self._attr_state == STATE_UNKNOWN:
            self._attr_state = self._default_state

    @property
    def extra_state_attributes(self) -> dict:
        if self._external_sensor:
            attributes = {"external_sensor": self._external_sensor}
            return attributes

    def replace_external_sensor(self, new_sensor: str | None) -> None:
        """Modify the external sensor"""
        _LOGGER.info("Setting %s external sensor to %s", self.entity_id, new_sensor)
        self._external_sensor = new_sensor

    async def async_update(self):
        """Run on every update to allow for changes from the GUI and service call"""
        current_attrs = self.hass.states.get(self.entity_id).attributes
        if current_attrs.get("external_sensor") != self._external_sensor:
            self.replace_external_sensor(current_attrs.get("external_sensor"))
        if self._external_sensor:
            external_sensor = self.hass.states.get(self._external_sensor)
            if external_sensor:
                self._attr_state = external_sensor.state
            else:
                self._attr_state = STATE_UNKNOWN
        else:
            self._attr_state = STATE_UNKNOWN

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if not state:
            return
        self._attr_state = state.state
        if "external_sensor" in state.attributes:
            _LOGGER.info(
                "External sensor for %s in state-attributes: %s",
                self.entity_id,
                state.attributes["external_sensor"],
            )
            self.replace_external_sensor(state.attributes["external_sensor"])
        async_dispatcher_connect(
            self._hass, DATA_UPDATED, self._schedule_immediate_update
        )

    @callback
    def _schedule_immediate_update(self):
        self.async_schedule_update_ha_state(True)


class PlantCurrentBrightness(PlantCurrentStatus):
    """Entity class for the current brightness meter"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Current Brightness"
        )
        self._attr_unique_id = f"{config.entry_id}-current-brightness"
        self._attr_icon = "mdi:brightness-6"
        self._external_sensor = config.data[FLOW_PLANT_INFO].get(FLOW_SENSOR_BRIGHTNESS)
        _LOGGER.info(
            "Added external sensor for %s %s", self.entity_id, self._external_sensor
        )
        super().__init__(hass, config, plantdevice)


class PlantCurrentConductivity(PlantCurrentStatus):
    """Entity class for the current condictivity meter"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Current Conductivity"
        )
        self._attr_unique_id = f"{config.entry_id}-current-conductivity"
        self._attr_icon = "mdi:spa-outline"
        self._external_sensor = config.data[FLOW_PLANT_INFO].get(
            FLOW_SENSOR_CONDUCTIVITY
        )

        super().__init__(hass, config, plantdevice)


class PlantCurrentMoisture(PlantCurrentStatus):
    """Entity class for the current moisture meter"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Current Moisture Level"
        )
        self._attr_unique_id = f"{config.entry_id}-current-moisture"
        self._external_sensor = config.data[FLOW_PLANT_INFO].get(FLOW_SENSOR_MOISTURE)
        self._attr_icon = "mdi:water"

        super().__init__(hass, config, plantdevice)


class PlantCurrentTempertature(PlantCurrentStatus):
    """Entity class for the current temperature meter"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Current Temperature"
        )
        self._attr_unique_id = f"{config.entry_id}-current-temperature"
        self._external_sensor = config.data[FLOW_PLANT_INFO].get(
            FLOW_SENSOR_TEMPERATURE
        )
        self._attr_icon = "mdi:thermometer"
        super().__init__(hass, config, plantdevice)

    @property
    def device_class(self):
        return "temperature"


class PlantCurrentHumidity(PlantCurrentStatus):
    """Entity class for the current humidity meter"""

    def __init__(
        self, hass: HomeAssistant, config: ConfigEntry, plantdevice: Entity
    ) -> None:
        self._attr_name = (
            f"{config.data[FLOW_PLANT_INFO][FLOW_PLANT_NAME]} Current Humidity"
        )
        self._attr_unique_id = f"{config.entry_id}-current-humidity"
        self._external_sensor = config.data[FLOW_PLANT_INFO].get(FLOW_SENSOR_HUMIDITY)
        self._attr_icon = "mdi:water-percent"
        super().__init__(hass, config, plantdevice)

    @property
    def device_class(self):
        return "humidity"
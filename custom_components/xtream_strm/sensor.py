import os
import json
import requests
import re
import logging
from datetime import datetime, time
import homeassistant.util.dt as dt_util
from homeassistant.helpers.event import async_track_time_change
from homeassistant.components.sensor import SensorEntity

_LOGGER = logging.getLogger(__name__)

def sanitize_filename(name):
    """Remove caracteres inválidos de um nome de arquivo."""
    return re.sub(r'[^a-zA-Z0-9_\-\s]', '', name).strip()

def fetch_data(api_url, username, password, action):
    """Obtém dados da API externa."""
    url = f"{api_url}?username={username}&password={password}&action={action}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        _LOGGER.error(f"Erro ao acessar {url}: {e}")
        return []

class StreamManagerSensor(SensorEntity):
    """Sensor para monitorar novos streams conforme um horário programado."""
    
    def __init__(self, hass, sensor_type, api_url, username, password, strm_folder, update_schedule):
        """Inicializa o sensor."""
        self.hass = hass
        self._attr_name = f"Stream Manager {sensor_type.capitalize()}"
        self._attr_unique_id = f"stream_manager_{sensor_type}_{username}"
        self._sensor_type = sensor_type
        self._api_url = api_url
        self._username = username
        self._password = password
        self._strm_folder = strm_folder
        self._history_file = os.path.join(strm_folder, "history.json")
        self._state = None
        self._update_schedule = update_schedule
        self._history = self.load_history()

        # Criar pastas se não existirem
        os.makedirs(os.path.join(self._strm_folder, "series"), exist_ok=True)
        os.makedirs(os.path.join(self._strm_folder, "movies"), exist_ok=True)
        os.makedirs(os.path.join(self._strm_folder, "live"), exist_ok=True)

        # Agendar atualização nos horários definidos pelo usuário
        self.schedule_updates()

    def load_history(self):
        """Carrega o histórico salvo."""
        if os.path.exists(self._history_file):
            try:
                with open(self._history_file, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {"series": {}, "movies": [], "live": {}}
        return {"series": {}, "movies": [], "live": {}}

    def save_history(self):
        """Salva o histórico atualizado."""
        with open(self._history_file, "w") as f:
            json.dump(self._history, f, indent=4)

    def update(self):
        """Atualiza os dados do sensor."""
        _LOGGER.info(f"Atualizando Stream Manager: {self._sensor_type}")

        if self._sensor_type == "series":
            series_list = fetch_data(self._api_url, self._username, self._password, "get_series")
            new_series = []
            for series in series_list:
                series_id = str(series.get("series_id"))
                if series_id not in self._history["series"]:
                    new_series.append(series["name"])
                    self._history["series"][series_id] = {}
            self._state = len(new_series)

        elif self._sensor_type == "movies":
            movies_list = fetch_data(self._api_url, self._username, self._password, "get_vod_streams")
            new_movies = []
            for movie in movies_list:
                stream_id = str(movie.get("stream_id"))
                if stream_id not in self._history["movies"]:
                    new_movies.append(movie["name"])
                    self._history["movies"].append(stream_id)
            self._state = len(new_movies)

        elif self._sensor_type == "live":
            live_list = fetch_data(self._api_url, self._username, self._password, "get_live_streams")
            new_channels = []
            for channel in live_list:
                stream_id = str(channel.get("stream_id"))
                if stream_id not in self._history["live"]:
                    new_channels.append(channel["name"])
                    self._history["live"][stream_id] = channel["name"]
            self._state = len(new_channels)

        self.save_history()

    def schedule_updates(self):
        """Agenda atualizações com base na configuração do usuário."""
        if not self._update_schedule:
            return

        for schedule in self._update_schedule:
            try:
                day_of_week, time_str = schedule.split(" ")
                update_time = time.fromisoformat(time_str)
                
                async_track_time_change(
                    self.hass, self.update, hour=update_time.hour, minute=update_time.minute, second=0
                )

                _LOGGER.info(f"Agendado para rodar toda {day_of_week.capitalize()} às {update_time}.")
            
            except ValueError:
                _LOGGER.error(f"Formato inválido de agendamento: {schedule}. Use 'terca 02:00'.")

    @property
    def state(self):
        """Retorna o estado atual do sensor."""
        return self._state

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Configura os sensores no Home Assistant."""
    api_url = config.get("api_url")
    username = config.get("username")
    password = config.get("password")
    strm_folder = config.get("strm_folder")
    update_schedule = config.get("update_schedule", [])

    if not api_url or not username or not password or not strm_folder:
        _LOGGER.error("É necessário fornecer api_url, username, password e strm_folder na configuração do sensor!")
        return

    async_add_entities([
        StreamManagerSensor(hass, "series", api_url, username, password, strm_folder, update_schedule),
        StreamManagerSensor(hass, "movies", api_url, username, password, strm_folder, update_schedule),
        StreamManagerSensor(hass, "live", api_url, username, password, strm_folder, update_schedule),
    ])

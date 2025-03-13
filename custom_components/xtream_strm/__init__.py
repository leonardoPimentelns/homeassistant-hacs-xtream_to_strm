"""The xtream_to_strm integration."""
import os
import requests
import re
import json
from concurrent.futures import ThreadPoolExecutor
import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import track_time_change
import homeassistant.util.dt as dt_util
from datetime import datetime

DOMAIN = "xtream_to_strm"

CONF_API_URL = "api_url"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_STRM_FOLDER = "strm_folder"
CONF_UPDATE_TIME = "update_time"

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_API_URL): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_STRM_FOLDER): cv.string,
        vol.Required(CONF_UPDATE_TIME): cv.time,
    })
}, extra=vol.ALLOW_EXTRA)


def setup(hass, config):
    """Set up the xtream_to_strm component."""
    conf = config[DOMAIN]
    api_url = conf[CONF_API_URL]
    username = conf[CONF_USERNAME]
    password = conf[CONF_PASSWORD]
    strm_folder = conf[CONF_STRM_FOLDER]
    update_time = conf[CONF_UPDATE_TIME]

    history_file = os.path.join(strm_folder, "history.json")

    # Create folders if they don't exist
    os.makedirs(os.path.join(strm_folder, "series"), exist_ok=True)
    os.makedirs(os.path.join(strm_folder, "movies"), exist_ok=True)
    os.makedirs(os.path.join(strm_folder, "live"), exist_ok=True)

    def sanitize_filename(name):
        return re.sub(r'[^a-zA-Z0-9_\-\s]', '', name).strip()

    def process_streams(action):
        response = requests.get(f"{api_url}?username={username}&password={password}&action={action}")
        if response.status_code != 200:
            print(f"Erro ao acessar {action}")
            return []
        return response.json()

    if os.path.exists(history_file):
        try:
            with open(history_file, "r") as f:
                history = json.load(f)
        except json.JSONDecodeError:
            print("Erro ao carregar o JSON. Criando um novo histórico.")
            history = {"series": {}, "movies": [], "live": {}}
    else:
        history = {"series": {}, "movies": [], "live": {}}

    def process_series(series):
        name = sanitize_filename(series.get("name", "Desconhecido"))
        series_id = str(series.get("series_id"))
        
        if not series_id:
            return
        
        series_folder = os.path.join(strm_folder, "series", name)
        os.makedirs(series_folder, exist_ok=True)
        
        series_info = process_streams(f"get_series_info&series_id={series_id}")
        if "episodes" in series_info and isinstance(series_info["episodes"], dict):
            if series_id not in history["series"]:
                history["series"][series_id] = {}
            
            for season, episodes in series_info["episodes"].items():
                season_name = sanitize_filename(f"Temporada {season}")
                season_folder = os.path.join(series_folder, season_name)
                os.makedirs(season_folder, exist_ok=True)
                
                if season not in history["series"][series_id]:
                    history["series"][series_id][season] = set()
                
                for episode in episodes:
                    episode_title = sanitize_filename(episode.get("title", f"Episódio {episode.get('episode_num', 'Desconhecido')}"))
                    episode_id = str(episode.get("id"))
                    
                    if not episode_id or episode_id in history["series"][series_id][season]:
                        continue
                    
                    episode_url = f"{api_url}/series/{username}/{password}/{episode_id}"
                    episode_file = os.path.join(season_folder, f"{episode_title}.strm")
                    
                    with open(episode_file, "w") as f:
                        f.write(episode_url)
                    
                    history["series"][series_id][season].add(episode_id)

    def process_movies(movie):
        name = sanitize_filename(movie.get("name", "Desconhecido"))
        stream_id = str(movie.get("stream_id"))
        
        if not stream_id or stream_id in history["movies"]:
            return
        
        movie_url = f"{api_url}/movie/{username}/{password}/{stream_id}.mp4"
        movie_file = os.path.join(strm_folder, "movies", f"{name}.strm")
        
        with open(movie_file, "w") as f:
            f.write(movie_url)
        
        history["movies"].append(stream_id)

    def process_live(channel, categories):
        name = sanitize_filename(channel.get("name", "Desconhecido"))
        stream_id = str(channel.get("stream_id"))
        category_id = str(channel.get("category_id"))
        category_name = sanitize_filename(categories.get(category_id, "Outros"))
        
        if not stream_id or stream_id in history["live"].get(category_name, set()):
            return
        
        live_url = f"{api_url}/live/{username}/{password}/{stream_id}.m3u8"
        category_folder = os.path.join(strm_folder, "live", category_name)
        os.makedirs(category_folder, exist_ok=True)
        live_file = os.path.join(category_folder, f"{name}.strm")
        
        with open(live_file, "w") as f:
            f.write(live_url)
        
        if category_name not in history["live"]:
            history["live"][category_name] = set()
        history["live"][category_name].add(stream_id)

    live_categories = {str(cat["category_id"]): cat["category_name"] for cat in process_streams("get_live_categories")}

    def update_content(event_time):
        with ThreadPoolExecutor() as executor:
            series_list = process_streams("get_series")
            executor.map(process_series, series_list)
            
            movies_list = process_streams("get_vod_streams")
            executor.map(process_movies, movies_list)
            
            live_list = process_streams("get_live_streams")
            executor.map(lambda channel: process_live(channel, live_categories), live_list)

        history["movies"] = list(history["movies"])
        for category in history["live"]:
            history["live"][category] = list(history["live"][category])
        for series_id in history["series"]:
            for season in history["series"][series_id]:
                history["series"][series_id][season] = list(history["series"][series_id][season])

        with open(history_file, "w") as f:
            json.dump(history, f, indent=4)

        # Update the sensor state
        hass.states.set(f"{DOMAIN}.last_update", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        print("Processo concluído! Apenas novos episódios, filmes e canais foram adicionados.")

    # Schedule the update function
    track_time_change(hass, update_content, hour=update_time.hour, minute=update_time.minute, second=0)
    return True

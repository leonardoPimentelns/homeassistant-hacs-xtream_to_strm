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
CONF_TMDB_API_KEY = "tmdb_api_key"

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_API_URL): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_STRM_FOLDER): cv.string,
        vol.Required(CONF_UPDATE_TIME): cv.time,
        vol.Required(CONF_TMDB_API_KEY): cv.string,
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
    tmdb_api_key = conf[CONF_TMDB_API_KEY]

    history_file = os.path.join(strm_folder, "history.json")

    os.makedirs(os.path.join(strm_folder, "TV Shows"), exist_ok=True)
    os.makedirs(os.path.join(strm_folder, "Movies"), exist_ok=True)
    os.makedirs(os.path.join(strm_folder, "Live"), exist_ok=True)

    def sanitize_filename(name):
        # Remove caracteres especiais do nome
        return re.sub(r'[^a-zA-Z0-9_\-\s\(\)]', '', name).strip()

    def fetch_year_from_tmdb(title, media_type="movie"):
        """Fetch the correct year using TMDb API."""
        url = f"https://api.themoviedb.org/3/search/{media_type}?api_key={tmdb_api_key}&query={title}"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            if data['results']:
                if media_type == "movie":
                    return data['results'][0].get('release_date', '').split('-')[0]
                elif media_type == "tv":
                    return data['results'][0].get('first_air_date', '').split('-')[0]
        return "Unknown"

    def process_streams(action):
        response = requests.get(f"{api_url}/player_api.php?username={username}&password={password}&action={action}")
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
            history = {"tv_shows": {}, "movies": [], "live": {}}
    else:
        history = {"tv_shows": {}, "movies": [], "live": {}}

    def process_tv_shows(series):
        name = sanitize_filename(series.get("name", "Desconhecido"))
        series_id = str(series.get("series_id"))
        year = str(series.get("year", ""))

        if year in ["0", "none", ""]:
            year = fetch_year_from_tmdb(name, media_type="tv")

        if year and f"({year})" not in name:
            name = f"{name} ({year})"

        if " L" in name:
            name = name.replace(" L", "")
            name += " - Legendado"
        else:
            name += " - Dublado"

        if not series_id:
            return

        series_folder = os.path.join(strm_folder, "TV Shows", name)
        os.makedirs(series_folder, exist_ok=True)

        series_info = process_streams(f"get_series_info&series_id={series_id}")
        if "episodes" in series_info and isinstance(series_info["episodes"], dict):
            if series_id not in history["tv_shows"]:
                history["tv_shows"][series_id] = {}

            for season, episodes in series_info["episodes"].items():
                season_name = f"Season {str(season).zfill(2)}"  # Season 01, Season 02, etc.
                season_folder = os.path.join(series_folder, season_name)
                os.makedirs(season_folder, exist_ok=True)

                if season not in history["tv_shows"][series_id]:
                    history["tv_shows"][series_id][season] = set()

                for episode in episodes:
                    episode_num = episode.get("episode_num", "Desconhecido")
                    episode_title = sanitize_filename(episode.get("title", f"Episode {episode_num}"))
                    episode_id = str(episode.get("id"))

                    if not episode_id or episode_id in history["tv_shows"][series_id][season]:
                        continue

                    # Remove `player_api.php` do `api_url`
                    episode_url = f"{api_url}/series/{username}/{password}/{episode_id}.mp4"
                    episode_file = os.path.join(season_folder, f"{name} - s{str(season).zfill(2)}e{str(episode_num).zfill(2)}.strm")

                    with open(episode_file, "w") as f:
                        f.write(episode_url)

                    history["tv_shows"][series_id][season].add(episode_id)

    def process_movies(movie):
        name = sanitize_filename(movie.get("name", "Desconhecido"))
        stream_id = str(movie.get("stream_id"))
        year = str(movie.get("year", ""))

        if year in ["0", "none", ""]:
            year = fetch_year_from_tmdb(name, media_type="movie")

        if year and f"({year})" not in name:
            name = f"{name} ({year})"

        if " L" in name:
            name = name.replace(" L", "")
            name += " - Legendado"
        else:
            name += " - Dublado"

        if not stream_id or stream_id in history["movies"]:
            return

        movie_url = f"{api_url}/movie/{username}/{password}/{stream_id}.mp4"
        movie_folder = os.path.join(strm_folder, "Movies", name)
        os.makedirs(movie_folder, exist_ok=True)
        movie_file = os.path.join(movie_folder, f"{name}.strm")

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
        category_folder = os.path.join(strm_folder, "Live", category_name)
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
            executor.map(process_tv_shows, series_list)

            movies_list = process_streams("get_vod_streams")
            executor.map(process_movies, movies_list)

            live_list = process_streams("get_live_streams")
            executor.map(lambda channel: process_live(channel, live_categories), live_list)

        history["movies"] = list(history["movies"])
        for category in history["live"]:
            history["live"][category] = list(history["live"][category])
        for series_id in history["tv_shows"]:
            for season in history["tv_shows"][series_id]:
                history["tv_shows"][series_id][season] = list(history["tv_shows"][series_id][season])

        with open(history_file, "w") as f:
            json.dump(history, f, indent=4)

        hass.states.set(f"{DOMAIN}.last_update", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        print("Processo concluído! Apenas novos episódios, filmes e canais foram adicionados.")

    track_time_change(hass, update_content, hour=update_time.hour, minute=update_time.minute, second=0)
    return True

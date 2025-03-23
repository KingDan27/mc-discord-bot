#!/usr/bin/env python3

import os
import re
import json
import asyncio
import aiofiles
import discord
import logging
import time
import sys
from discord.ext import commands
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ✅ Load configuration
CONFIG_PATH = "config.json"
with open(CONFIG_PATH, "r") as config_file:
    config = json.load(config_file)

TOKEN = config["token"]
CHANNEL_ID = int(config["channel_id"])
SERVER_DIR = config["server_dir"]
LOG_LEVEL = logging.DEBUG

# ✅ Set up logging
LOG_DIR = "log"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bot_activity.log")

logging.basicConfig(
    filename=LOG_FILE,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=LOG_LEVEL,
)
console_handler = logging.StreamHandler()
console_handler.setLevel(LOG_LEVEL)
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(console_handler)

# ✅ Bot setup
intents = discord.Intents.default()
intents.typing = False
intents.presences = False
bot = commands.Bot(command_prefix="!", intents=intents)

# ✅ Embed message tracking
embed_tracker = {}
player_tracker = {}
channel_cache = None

# ✅ Embed IDs file
EMBED_IDS_FILE = "embed_ids.json"

def load_embed_ids():
    """Load saved embed message IDs from file."""
    global embed_tracker
    if os.path.exists(EMBED_IDS_FILE):
        with open(EMBED_IDS_FILE, "r") as f:
            embed_tracker = json.load(f)
            logging.info("✅ Embed IDs loaded successfully.")

def save_embed_ids():
    """Save current embed message IDs to file."""
    with open(EMBED_IDS_FILE, "w") as f:
        json.dump(embed_tracker, f)
        logging.info("✅ Embed IDs saved successfully.")

@bot.event
async def on_ready():
    """Wait until bot is fully connected before caching the channel."""
    global channel_cache
    logging.info(f"🚀 Bot connected as {bot.user}")

    # ✅ Cache the channel after the bot is fully ready
    channel_cache = bot.get_channel(CHANNEL_ID)

    if channel_cache:
        logging.info(f"✅ Channel {CHANNEL_ID} cached successfully.")
    else:
        logging.warning(f"⚠️ Channel {CHANNEL_ID} not found after bot is ready!")

    # ✅ Initialize players once bot is ready
    await initialize_players()

# ✅ Initialize servers concurrently
async def initialize_players():
    """Initialize player lists and start monitoring concurrently."""
    servers = [d for d in os.listdir(SERVER_DIR) if os.path.isdir(os.path.join(SERVER_DIR, d))]

    tasks = []
    for server in servers:
        screen_name = f"MC{server}"

        if os.system(f"screen -list | grep -q {screen_name}") == 0:
            os.system(f"screen -S {screen_name} -p 0 -X stuff 'list\n'")
            await asyncio.sleep(2)

            log_file = os.path.join(SERVER_DIR, server, "logs", "latest.log")

            if not os.path.exists(log_file):
                logging.warning(f"⚠️ No log file found for {server}. Skipping.")
                continue

            async with aiofiles.open(log_file, "r") as f:
                await f.seek(0, os.SEEK_END)
                position = await f.tell()

            player_tracker[server] = {
                "players": set(),
                "log_file": log_file,
                "position": position,
            }

            await update_embed(server)
            logging.info(f"✅ Initialized {server} with file position {position}")

            tasks.append(asyncio.create_task(monitor_player_activity(server)))

    await asyncio.gather(*tasks)

# ✅ Create or update embed messages
async def update_embed(server):
    """Update or create the embed message for the server."""
    global channel_cache

    # ✅ Ensure the channel is cached
    if not channel_cache:
        logging.warning(f"⚠️ Channel not cached yet. Skipping embed update for {server}.")
        return

    players = player_tracker[server]["players"]
    status = "Online" if players else "Offline"
    embed = discord.Embed(
        title=f"{server} - {status}",
        color=discord.Color.green() if players else discord.Color.red(),
        description=f"**Players Online:** {len(players)}"
    )

    if players:
        embed.add_field(name="Players", value=", ".join(players), inline=False)

    if server in embed_tracker:
        message_id = embed_tracker[server]
        try:
            message = await channel_cache.fetch_message(message_id)
            await message.edit(embed=embed)
            logging.info(f"✅ Updated embed for {server}")
        except discord.NotFound:
            logging.warning(f"⚠️ Embed not found for {server}. Creating new one.")
            message = await channel_cache.send(embed=embed)
            embed_tracker[server] = message.id
            save_embed_ids()
    else:
        message = await channel_cache.send(embed=embed)
        embed_tracker[server] = message.id
        save_embed_ids()

# ✅ Monitor server activity concurrently
async def monitor_player_activity(server):
    """Monitor player activity from the saved file position."""
    base_log_dir = os.path.dirname(player_tracker[server]["log_file"])
    current_log_file = os.path.join(base_log_dir, "latest.log")

    logging.info(f"👀 Starting log monitor for {server}")

    async with aiofiles.open(current_log_file, "r") as f:
        await f.seek(player_tracker[server]["position"])

        while True:
            line = await f.readline()

            if not line:
                await asyncio.sleep(0.5)
                continue

            logging.debug(f"📄 New log entry for {server}: {line.strip()}")

            # ✅ Improved player name extraction
            if "joined the game" in line:
                match = re.search(r': (.+) joined the game', line)
                if match:
                    player = match.group(1).strip()
                    player_tracker[server]["players"].add(player)
                    await update_embed(server)
                    logging.info(f"✅ {player} joined {server}")

            elif "left the game" in line or "lost connection" in line:
                match = re.search(r': (.+) (left the game|lost connection)', line)
                if match:
                    player = match.group(1).strip()
                    player_tracker[server]["players"].discard(player)
                    await update_embed(server)
                    logging.info(f"❌ {player} left {server}")

# ✅ Script reload handler
class ScriptChangeHandler(FileSystemEventHandler):
    """Reloads the script when modified."""
    last_reload = 0

    def on_modified(self, event):
        if event.src_path.endswith("mc_discord_bot.py"):
            current_time = time.time()
            if current_time - self.last_reload > 2:
                logging.info("🔄 Script changed, restarting...")
                os.execl(sys.executable, sys.executable, __file__, *sys.argv[1:])
                self.last_reload = current_time

def start_script_reload_observer():
    """Start watchdog observer for script changes."""
    script_handler = ScriptChangeHandler()
    script_observer = Observer()
    script_observer.schedule(script_handler, path=os.getcwd(), recursive=False)
    script_observer.start()

# ✅ Main asynchronous entry point
async def main():
    start_script_reload_observer()
    load_embed_ids()
    await bot.start(TOKEN)

# ✅ Run the bot with asyncio.run()
if __name__ == "__main__":
    asyncio.run(main())
